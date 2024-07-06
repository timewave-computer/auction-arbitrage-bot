use super::{util, ARBFILE_PATH, OSMO_OWNER_ADDR, OWNER_ADDR, TEST_MNEMONIC};
use cosmwasm_std::Decimal;
use derive_builder::Builder;
use localic_utils::{types::contract::MinAmount, utils::test_context::TestContext};
use notify::{Event, EventKind, RecursiveMode, Result as NotifyResult, Watcher};
use serde_json::Value;
use shared_child::SharedChild;
use std::{
    borrow::BorrowMut,
    collections::{HashMap, HashSet},
    error::Error,
    fs::OpenOptions,
    path::Path,
    process::Command,
    sync::{mpsc, Arc, Mutex},
};

const EXIT_STATUS_SUCCESS: i32 = 9;
const EXIT_STATUS_SIGKILL: i32 = 9;

/// Runs all provided tests while reporting a final
/// exit status.
pub struct TestRunner<'a> {
    test_statuses: Arc<Mutex<HashMap<(String, String), TestResult>>>,
    cached: bool,
    denom_map: HashMap<String, Vec<HashMap<String, String>>>,
    created_denoms: HashSet<String>,
    test_ctx: &'a mut TestContext,
}

impl<'a> TestRunner<'a> {
    pub fn new(ctx: &'a mut TestContext, cached: bool) -> Self {
        Self {
            test_statuses: Default::default(),
            cached,
            denom_map: Default::default(),
            created_denoms: Default::default(),
            test_ctx: ctx,
        }
    }

    /// Performs setup that should only be performed once per localic spinup,
    /// including:
    /// - Creating tokenfactory tokens
    pub fn start(&mut self) -> Result<&mut Self, Box<dyn Error + Send + Sync>> {
        Ok(self)
    }

    /// Runs a test with access to the test context with some metadata.
    pub fn run(&mut self, mut test: Test) -> Result<&mut Self, Box<dyn Error + Send + Sync>> {
        if !self.cached {
            // Perform cold start setup
            // Create tokens w tokenfactory for all test tokens
            test.denoms
                .iter()
                .filter(|denom| denom.contains("factory"))
                .filter(|denom| !self.created_denoms.contains(*denom))
                .collect::<Vec<_>>()
                .into_iter()
                .try_for_each(|token| {
                    self.test_ctx
                        .build_tx_create_tokenfactory_token()
                        .with_subdenom(
                            token
                                .split('/')
                                .nth(2)
                                .expect("Improperly formatted tokenfactory denom"),
                        )
                        .send()?;
                    self.created_denoms.insert(token.clone());

                    let res: Result<(), Box<dyn Error + Send + Sync>> = Ok(());
                    res
                })?;
        }

        // Perform hot start setup
        // Mapping of denoms to their matching denoms, chain id's, channel id's, and ports
        self.denom_map = Default::default();

        let ctx = &mut self.test_ctx;

        ctx.build_tx_upload_contracts().send()?;

        // Setup astroport
        ctx.build_tx_create_token_registry()
            .with_owner(OWNER_ADDR)
            .send()?;
        ctx.build_tx_create_factory()
            .with_owner(OWNER_ADDR)
            .send()?;

        let min_tokens = test
            .denoms
            .iter()
            .map(|token| {
                (
                    token.as_str(),
                    MinAmount {
                        send: "0".into(),
                        start_auction: "0".into(),
                    },
                )
            })
            .collect::<Vec<_>>();

        // Create a valence auction manager and an auction for each token
        ctx.build_tx_create_auctions_manager()
            .with_min_auction_amount(min_tokens.as_slice())
            .with_server_addr(OWNER_ADDR)
            .send()?;
        ctx.build_tx_create_price_oracle().send()?;
        ctx.build_tx_update_auction_oracle().send()?;

        util::create_deployment_file(
            ctx.get_astroport_factory()?
                .contract_addr
                .expect("missing deployed astroport factory")
                .as_str(),
            ctx.get_auctions_manager()?
                .contract_addr
                .expect("missing deployed astroport factory")
                .as_str(),
        )?;
        util::create_arbs_file()?;
        util::create_netconfig()?;
        util::create_denom_file(&self.denom_map)?;

        let statuses = self.test_statuses.clone();

        test.setup(self, self.test_ctx)?;

        with_arb_bot_output(Arc::new(Box::new(move |arbfile: Value| {
            statuses.lock().expect("Failed to lock statuses").insert(
                (test.name.clone(), test.description.clone()),
                (*test.test)(arbfile),
            );

            Ok(())
        })))?;

        Ok(self)
    }

    /// Produces a single result representing any failure that may
    /// have occurred in any test.
    /// Logs successes to stdout, and failures to stderr.
    pub fn join(&mut self) -> TestResult {
        for ((name, description), status) in self
            .test_statuses
            .lock()
            .expect("Failed to lock test statuses")
            .iter()
        {
            match status {
                Ok(_) => {
                    println!("✅ 🤑 SUCCESS {name} - {description}");
                }
                Err(e) => {
                    eprintln!("❌ ඞ FAILURE {name} - {description}: {:?}", e);
                }
            }
        }

        if let Some(e) = self
            .test_statuses
            .lock()
            .expect("Failed to lock test statuses")
            .drain()
            .filter_map(|res| res.1.err())
            .next()
        {
            return Err(e);
        }

        Ok(())
    }
}

/// A test that receives arb bot executable output.
pub type TestFn = Box<dyn Fn(Value) -> TestResult + Send + Sync>;
pub type OwnedTestFn = Arc<TestFn>;
pub type TestResult = Result<(), Box<dyn Error + Send + Sync>>;

/// Defines a test case. A test case is characterized by its
/// pools, and the balances associated with each pool,
/// and its auctions, and the price associated with each auction.
#[derive(Builder)]
#[builder(setter(into, strip_option, prefix = "with"), pattern = "owned")]
pub struct Test {
    /// Test metadata
    name: String,
    description: String,

    /// Fully qualified denoms (i.e., factory/neutronxyz/tokenabc or untrn or ibc/xyz)
    denoms: Vec<String>,

    /// How much of a given subdenom acc0 owns
    tokenfactory_token_balances_acc0: HashMap<String, u128>,

    /// (Denom a, denom b) or (offer asset, ask asset) -> pool
    pools: HashMap<(String, String), Vec<Pool>>,

    /// The test that should be run with the arb bot output
    test: OwnedTestFn,
}

impl Test {
    pub fn setup(
        &mut self,
        runner: &mut TestRunner,
        ctx: &mut TestContext,
    ) -> Result<&mut Self, Box<dyn Error + Send + Sync>> {
        self.tokenfactory_token_balances_acc0
            .iter()
            .try_for_each(|(token, balance)| {
                ctx.build_tx_mint_tokenfactory_token()
                    .with_denom(token)
                    .with_amount(*balance)
                    .send()
            })?;

        self.pools
            .iter()
            .try_for_each(|((denom_a, denom_b), pools)| {
                pools.iter().try_for_each(|pool_spec| match pool_spec {
                    Pool::Astroport(spec) => {
                        ctx.build_tx_create_pool()
                            .with_denom_a(denom_a.clone())
                            .with_denom_b(&denom_b.clone())
                            .send()?;
                        ctx.build_tx_fund_pool()
                            .with_denom_a(&denom_a.clone())
                            .with_denom_b(&denom_b.clone())
                            .with_amount_denom_a(spec.balance_asset_a)
                            .with_amount_denom_b(spec.balance_asset_b)
                            .with_liq_token_receiver(OWNER_ADDR)
                            .with_slippage_tolerance(Decimal::percent(50))
                            .send()
                    }
                    Pool::Osmosis(spec) => {
                        let funds_a = spec.denom_funds.get(denom_a).unwrap_or_default();
                        let funds_b = spec.denom_funds.get(denom_b).unwrap_or_default();

                        // Transfer requisite funds to osmosis
                        ctx.build_tx_transfer()
                            .with_chain_name("neutron")
                            .with_recipient(OSMO_OWNER_ADDR)
                            .with_denom(&denom_a)
                            .with_amount(funds_a)
                            .send()?;
                        ctx.build_tx_transfer()
                            .with_chain_name("neutron")
                            .with_recipient(OSMO_OWNER_ADDR)
                            .with_denom(&denom_b)
                            .with_amount(funds_b)
                            .send()?;

                        // Create the osmo pool and join it
                        let ibc_denom_a = ctx.get_ibc_denom(&denom_a, "neutron", "osmosis")?;
                        let ibc_denom_b = ctx.get_ibc_denom(&denom_b, "neutron", "osmosis")?;

                        // (denom, neutron) -> denom'
                        // (denom', osmo) -> denom
                        runner.denom_map.insert((denom_a, "osmosis"), ibc_denom_a);
                        runner.denom_map.insert((ibc_denom_a, "neutron"), denom_a);

                        // (denom, neutron) -> denom'
                        // (denom', osmo) -> denom
                        runner.denom_map.insert((denom_b, "osmosis"), ibc_denom_b);
                        runner.denom_map.insert((ibc_denom_b, "neutron"), denom_b);

                        ctx.build_tx_create_osmo_pool()
                            .with_weight(&ibc_denom_a, funds_a)
                            .with_weight(&ibc_bruhtoken, funds_b)
                            .with_initial_deposit(&ibc_neutron, funds_a)
                            .with_initial_deposit(&ibc_bruhtoken, funds_b)
                            .send()?;

                        let pool_id = ctx.get_osmo_pool(&ibc_denom_a, &ibc_denom_b)?;

                        // Fund the pool
                        ctx.build_tx_fund_osmo_pool()
                            .with_pool_id(pool_id)
                            .with_max_amount_in(&ibc_denom_a, funds_a)
                            .with_max_amount_in(&ibc_denom_b, funds_b)
                            .with_share_amount_out(1000000000000)
                            .send()
                    }
                    Pool::Auction(spec) => {
                        ctx.build_tx_create_auction()
                            .with_offer_asset(denom_a)
                            .with_ask_asset(denom_b)
                            .with_amount_offer_asset(spec.balance_offer_asset)
                            .send()?;

                        ctx.build_tx_manual_oracle_price_update()
                            .with_offer_asset(denom_a)
                            .with_ask_asset(denom_b)
                            .with_price(spec.price)
                            .send()?;

                        ctx.build_tx_fund_auction()
                            .with_offer_asset(denom_a)
                            .with_ask_asset(denom_b)
                            .with_amount_offer_asset(spec.balance_offer_asset)
                            .send()?;
                        ctx.build_tx_start_auction()
                            .with_offer_asset(denom_a)
                            .with_ask_asset(denom_b)
                            .with_start_block(0)
                            .with_end_block_delta(10000)
                            .send()
                    }
                })
            })?;

        Ok(self)
    }
}

impl TestBuilder {
    pub fn with_denom(
        mut self,
        denom: impl Into<String> + AsRef<str> + Clone,
        balance: u128,
    ) -> Self {
        self.borrow_mut()
            .denoms
            .get_or_insert_with(Default::default)
            .push(denom.clone().into());

        if denom.as_ref().contains("factory") {
            self.borrow_mut()
                .tokenfactory_token_balances_acc0
                .get_or_insert_with(Default::default)
                .insert(denom.into(), balance);
        }

        self
    }

    pub fn with_pool(
        mut self,
        denom_a: impl Into<String>,
        denom_b: impl Into<String>,
        pool: Pool,
    ) -> Self {
        self.pools
            .get_or_insert_with(Default::default)
            .entry((denom_a.into(), denom_b.into()))
            .or_default()
            .push(pool);

        self
    }
}

/// A pool that should be tested against.
#[derive(Clone)]
pub enum Pool {
    Astroport(AstroportPool),
    Osmosis(OmsosisPool),
    Auction(AuctionPool),
}

/// Represents an astroport xyk pool.
#[derive(Builder, Clone)]
#[builder(setter(into, strip_option, prefix = "with"))]
pub struct AstroportPool {
    pub asset_a: String,
    pub asset_b: String,
    pub balance_asset_a: u128,
    pub balance_asset_b: u128,
}

/// Represents an osmosis gamm pool.
#[derive(Builder, Clone)]
#[builder(setter(into, strip_option, prefix = "with"))]
pub struct OsmosisPool {
    denom_funds: HashMap<String, u128>,
}

impl OsomsisPoolBuilder {
    pub fn with_funds(denom: impl Into<String>, funds: u128) -> &mut Self {
        self.denom_funds.insert(denom.into(), funds);

        self
    }

    pub fn build(&mut self) -> OsmosisPool {
        OsmosisPool {
            denom_funds: self.denom_funds.unwrap_or_default(),
        }
    }
}

/// Represents a valence auction.
#[derive(Builder, Clone)]
#[builder(setter(into, strip_option, prefix = "with"))]
pub struct AuctionPool {
    pub offer_asset: String,
    pub ask_asset: String,
    pub balance_offer_asset: u128,
    pub price: Decimal,
}

pub fn with_arb_bot_output(test: OwnedTestFn) -> TestResult {
    let mut cmd = Command::new("python");
    cmd.current_dir("..")
        .arg("main.py")
        .arg("--deployments_file")
        .arg("deployments_file.json")
        .arg("--net_config")
        .arg("net_config.json")
        .arg("--base_denom")
        .arg("untrn")
        .arg("--denom_file")
        .arg("denoms.json")
        .env("LOGLEVEL", "debug")
        .env("WALLET_MNEMONIC", TEST_MNEMONIC);

    let proc = SharedChild::spawn(&mut cmd)?;

    let proc_handle = Arc::new(proc);
    let proc_handle_watcher = proc_handle.clone();
    let (tx_res, rx_res) = mpsc::channel();

    let test_handle = test.clone();

    // Wait until the arbs.json file has been produced
    let mut watcher = notify::recommended_watcher(move |res: NotifyResult<Event>| {
        let e = res.expect("failed to watch arbs.json");

        // An arb was found
        if let EventKind::Modify(_) = e.kind {
            let f = OpenOptions::new()
                .read(true)
                .open(ARBFILE_PATH)
                .expect("failed to open arbs.json");

            if f.metadata().expect("can't get arbs metadata").len() == 0 {
                return;
            }

            let arbfile: Value =
                serde_json::from_reader(&f).expect("failed to deserialize arbs.json");

            let res = test_handle(arbfile);

            proc_handle_watcher.kill().expect("failed to kill arb bot");
            tx_res.send(res).expect("failed to send test results");
        }
    })?;

    watcher.watch(Path::new(ARBFILE_PATH), RecursiveMode::NonRecursive)?;
    let exit_status = proc_handle.wait()?;

    if !exit_status.success() {
        if let Some(status) = exit_status.code() {
            if status != EXIT_STATUS_SUCCESS && status != EXIT_STATUS_SIGKILL {
                return Err(format!("Arb bot failed: {:?}", exit_status).into());
            }
        }
    }

    rx_res.recv()?
}
