use super::{
    util::{self, DenomMapEntry},
    ARBFILE_PATH, OSMO_OWNER_ADDR, OWNER_ADDR, TEST_MNEMONIC,
};
use clap::Parser;
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
    fmt::{self, Display, Formatter},
    fs::OpenOptions,
    path::Path,
    process::Command,
    sync::{mpsc, Arc, Mutex},
};

const EXIT_STATUS_SUCCESS: i32 = 9;
const EXIT_STATUS_SIGKILL: i32 = 9;

/// A lazily evaluated denom hash,
/// based on an src chain, a dest chain
/// and a base denom. If the dest chain
/// and base chain are the same,
/// no hash is created.
#[derive(Hash, Eq, PartialEq, Clone, Debug)]
pub enum Denom {
    /// A denom with no dest chain
    Local {
        base_chain: String,
        base_denom: String,
    },
    /// A denom representing a transfer
    Interchain {
        base_denom: String,
        base_chain: String,
        dest_chain: String,
    },
}

impl Denom {
    /// Transfers some quantity of the denom to the denom's destination chain, yielding
    /// the "normalized" IBC denom (i.e., the one native to the destination chain).
    pub fn normalize(
        &self,
        amount: u128,
        ctx: &mut TestContext,
    ) -> Result<(String, Option<(DenomMapEntry, DenomMapEntry)>), Box<dyn Error + Send + Sync>>
    {
        match self {
            Self::Local { base_denom, .. } => Ok((base_denom.to_owned(), None)),
            Self::Interchain {
                base_denom,
                base_chain,
                dest_chain,
            } => {
                let admin_addr = ctx.get_chain(&dest_chain).admin_addr.to_owned();

                ctx.build_tx_transfer()
                    .with_amount(amount)
                    .with_chain_name(&base_chain)
                    .with_recipient(&admin_addr)
                    .with_denom(&base_denom)
                    .send()?;

                let trace_a = ctx
                    .transfer_channel_ids
                    .get(&(base_chain.clone(), dest_chain.clone()))
                    .expect(&format!("Missing IBC trace for {base_denom}"))
                    .clone();

                let ibc_denom_a = ctx.get_ibc_denom(base_denom, base_chain, dest_chain);

                let trace_a_counter = ctx
                    .transfer_channel_ids
                    .get(&(dest_chain.clone(), base_chain.clone()))
                    .expect(&format!("Missing IBC trace for {base_denom}"));

                let src_chain = ctx.get_chain(&base_chain);
                let dest_chain = ctx.get_chain(&dest_chain);

                Ok((
                    ibc_denom_a.clone(),
                    Some((
                        DenomMapEntry {
                            chain_id: dest_chain.rb.chain_id.clone(),
                            denom: ibc_denom_a.clone(),
                            channel_id: trace_a.to_owned(),
                            port_id: "transfer".to_owned(),
                        },
                        DenomMapEntry {
                            chain_id: src_chain.rb.chain_id.clone(),
                            denom: base_denom.to_string(),
                            channel_id: trace_a_counter.to_owned(),
                            port_id: "transfer".to_owned(),
                        },
                    )),
                ))
            }
        }
    }
}

impl Display for Denom {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Local { base_denom, .. } => {
                write!(f, "{}", base_denom)
            }
            Self::Interchain { base_denom, .. } => {
                write!(f, "{}", base_denom)
            }
        }
    }
}

#[derive(Parser, Debug)]
pub struct Args {
    #[arg(short, long, default_value_t = false)]
    cached: bool,

    /// Names of tests to run
    #[arg(short, long)]
    tests: Vec<String>,
}

/// Runs all provided tests while reporting a final
/// exit status.
pub struct TestRunner<'a> {
    test_statuses: Arc<Mutex<HashMap<(String, String), TestResult>>>,
    cli_args: Args,
    /// Mapping from (src_denom, dest_chain) -> dest_denom
    denom_map: HashMap<(String, String), DenomMapEntry>,
    created_denoms: HashSet<String>,
    test_ctx: &'a mut TestContext,
}

impl<'a> TestRunner<'a> {
    pub fn new(ctx: &'a mut TestContext, cli_args: Args) -> Self {
        Self {
            test_statuses: Default::default(),
            cli_args,
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
        if !self.cli_args.tests.is_empty() && !self.cli_args.tests.contains(&test.name) {
            return Ok(self);
        }

        if !self.cli_args.cached {
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

        test.setup(&mut self.denom_map, ctx)?;

        let ntrn_to_osmo = ctx
            .transfer_channel_ids
            .get(&("neutron".into(), "osmosis".into()))
            .cloned()
            .unwrap();
        let osmo_to_ntrn = ctx
            .transfer_channel_ids
            .get(&("osmosis".into(), "neutron".into()))
            .cloned()
            .unwrap();

        util::create_deployment_file(
            ctx.get_astroport_factory()?
                .get(0)
                .and_then(|c| c.contract_addr.as_ref())
                .expect("missing deployed astroport factory")
                .as_str(),
            ctx.get_auctions_manager()?
                .contract_addr
                .expect("missing deployed astroport factory")
                .as_str(),
            &ntrn_to_osmo,
            &osmo_to_ntrn,
        )
        .expect("Failed to create deployments file");
        util::create_arbs_file().expect("Failed to create arbs file");
        util::create_netconfig().expect("Failed to create net config");
        util::create_denom_file(&self.denom_map).expect("Failed to create denom file");

        let statuses = self.test_statuses.clone();

        if test.run_arbbot {
            with_arb_bot_output(Arc::new(Box::new(move |arbfile: Option<Value>| {
                statuses.lock().expect("Failed to lock statuses").insert(
                    (test.name.clone(), test.description.clone()),
                    (*test.test)(arbfile),
                );

                Ok(())
            })))?;

            return Ok(self);
        }

        statuses.lock().expect("Failed to lock statuses").insert(
            (test.name.clone(), test.description.clone()),
            (*test.test)(None),
        );

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
pub type TestFn = Box<dyn Fn(Option<Value>) -> TestResult + Send + Sync>;
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
    #[builder(default)]
    denoms: HashSet<String>,

    /// How much of a given subdenom acc0 owns on a given chain
    /// (chain, token) -> balance
    #[builder(default)]
    tokenfactory_token_balances_acc0: HashMap<Denom, u128>,

    /// (Denom a, denom b) or (offer asset, ask asset) -> pool
    #[builder(default)]
    pools: HashMap<(Denom, Denom), Vec<Pool>>,

    /// The test that should be run with the arb bot output
    test: OwnedTestFn,

    /// Whether the arb bot output should be fed as input to the test
    #[builder(default)]
    run_arbbot: bool,
}

impl Test {
    pub fn setup(
        &mut self,
        denom_map: &mut HashMap<(String, String), DenomMapEntry>,
        ctx: &mut TestContext,
    ) -> Result<&mut Self, Box<dyn Error + Send + Sync>> {
        self.tokenfactory_token_balances_acc0.iter().try_for_each(
            |(denom, balance)| match denom {
                Denom::Interchain {
                    base_denom,
                    base_chain,
                    dest_chain,
                } => {
                    // First mint the token, and then transfer it to the destination
                    // chain
                    let mut builder = ctx.build_tx_mint_tokenfactory_token();

                    builder
                        .with_denom(base_denom)
                        .with_amount(*balance)
                        .with_chain_name(base_chain);

                    if base_chain == "osmosis" {
                        builder.with_recipient_addr(OSMO_OWNER_ADDR);
                    }

                    builder.send()?;

                    let admin_addr = ctx.get_chain(dest_chain).admin_addr.to_owned();

                    ctx.build_tx_transfer()
                        .with_amount(*balance)
                        .with_chain_name(base_chain)
                        .with_recipient(&admin_addr)
                        .with_denom(&base_denom)
                        .send()
                }
                Denom::Local {
                    base_chain,
                    base_denom,
                } => {
                    let mut builder = ctx.build_tx_mint_tokenfactory_token();
                    builder
                        .with_denom(base_denom)
                        .with_amount(*balance)
                        .with_chain_name(base_chain);

                    if base_chain == "osmosis" {
                        builder.with_recipient_addr(OSMO_OWNER_ADDR);
                    }

                    builder.send()
                }
            },
        )?;

        self.pools
            .iter()
            .try_for_each(|((denom_a, denom_b), pools)| {
                pools.iter().try_for_each(|pool_spec| match pool_spec {
                    Pool::Astroport(spec) => {
                        let funds_a = spec.balance_asset_a;
                        let funds_b = spec.balance_asset_b;

                        // Create the osmo pool and join it
                        let (norm_denom_a, denom_map_ent_1) =
                            denom_a.normalize(funds_a, ctx).unwrap();
                        let (norm_denom_b, denom_map_ent_2) =
                            denom_b.normalize(funds_b, ctx).unwrap();

                        if let Some((map_ent_a_1, map_ent_a_2)) = denom_map_ent_1 {
                            // (denom, neutron) -> denom'
                            // (denom', osmo) -> denom
                            denom_map.insert((denom_a.to_string(), "neutron".into()), map_ent_a_1);
                            denom_map.insert((norm_denom_a.clone(), "osmosis".into()), map_ent_a_2);
                        }

                        if let Some((map_ent_b_1, map_ent_b_2)) = denom_map_ent_2 {
                            // (denom, neutron) -> denom'
                            // (denom', osmo) -> denom
                            denom_map.insert((denom_b.to_string(), "neutron".into()), map_ent_b_1);
                            denom_map.insert((norm_denom_b.clone(), "osmosis".into()), map_ent_b_2);
                        }

                        ctx.build_tx_create_pool()
                            .with_denom_a(&norm_denom_a)
                            .with_denom_b(&norm_denom_b)
                            .send()?;
                        ctx.build_tx_fund_pool()
                            .with_denom_a(&norm_denom_a)
                            .with_denom_b(&norm_denom_b)
                            .with_amount_denom_a(spec.balance_asset_a)
                            .with_amount_denom_b(spec.balance_asset_b)
                            .with_liq_token_receiver(OWNER_ADDR)
                            .with_slippage_tolerance(Decimal::percent(50))
                            .send()
                    }
                    Pool::Osmosis(spec) => {
                        let funds_a = spec.denom_funds.get(denom_a).unwrap_or(&0);
                        let funds_b = spec.denom_funds.get(denom_b).unwrap_or(&0);
                        let weight_a = spec.denom_weights.get(denom_a).unwrap_or(&0);
                        let weight_b = spec.denom_weights.get(denom_b).unwrap_or(&0);

                        // Create the osmo pool and join it
                        let (norm_denom_a, denom_map_ent_1) =
                            denom_a.normalize(*funds_a, ctx).unwrap();
                        let (norm_denom_b, denom_map_ent_2) =
                            denom_b.normalize(*funds_b, ctx).unwrap();

                        if let Some((map_ent_a_1, map_ent_a_2)) = denom_map_ent_1 {
                            // (denom, neutron) -> denom'
                            // (denom', osmo) -> denom
                            denom_map.insert((denom_a.to_string(), "osmosis".into()), map_ent_a_1);
                            denom_map.insert((norm_denom_a.clone(), "neutron".into()), map_ent_a_2);
                        }

                        if let Some((map_ent_b_1, map_ent_b_2)) = denom_map_ent_2 {
                            // (denom, neutron) -> denom'
                            // (denom', osmo) -> denom
                            denom_map.insert((denom_b.to_string(), "osmosis".into()), map_ent_b_1);
                            denom_map.insert((norm_denom_b.clone(), "neutron".into()), map_ent_b_2);
                        }

                        ctx.build_tx_create_osmo_pool()
                            .with_weight(&norm_denom_a, *weight_a as u64)
                            .with_weight(&norm_denom_b, *weight_b as u64)
                            .with_initial_deposit(&norm_denom_a, *funds_a as u64)
                            .with_initial_deposit(&norm_denom_b, *funds_b as u64)
                            .send()?;

                        let pool_id = ctx.get_osmo_pool(&norm_denom_a, &norm_denom_b)?;

                        // Fund the pool
                        ctx.build_tx_fund_osmo_pool()
                            .with_pool_id(pool_id)
                            .with_max_amount_in(&norm_denom_a, *funds_a as u64)
                            .with_max_amount_in(&norm_denom_b, *funds_b as u64)
                            .with_share_amount_out(1000000000000)
                            .send()
                    }
                    Pool::Auction(spec) => {
                        ctx.build_tx_create_auction()
                            .with_offer_asset(&denom_a.to_string())
                            .with_ask_asset(&denom_b.to_string())
                            .with_amount_offer_asset(spec.balance_offer_asset)
                            .send()?;

                        ctx.build_tx_manual_oracle_price_update()
                            .with_offer_asset(&denom_a.to_string())
                            .with_ask_asset(&denom_b.to_string())
                            .with_price(spec.price)
                            .send()?;

                        ctx.build_tx_fund_auction()
                            .with_offer_asset(&denom_a.to_string())
                            .with_ask_asset(&denom_b.to_string())
                            .with_amount_offer_asset(spec.balance_offer_asset)
                            .send()?;
                        ctx.build_tx_start_auction()
                            .with_offer_asset(&denom_a.to_string())
                            .with_ask_asset(&denom_b.to_string())
                            .with_end_block_delta(10000)
                            .send()
                    }
                })
            })?;

        Ok(self)
    }
}

impl TestBuilder {
    pub fn with_denom(mut self, denom: Denom, balance: u128) -> Self {
        self.borrow_mut()
            .denoms
            .get_or_insert_with(Default::default)
            .insert(denom.to_string().into());

        if denom.to_string().contains("factory") {
            self.borrow_mut()
                .tokenfactory_token_balances_acc0
                .get_or_insert_with(Default::default)
                .insert(denom, balance);
        }

        self
    }

    pub fn with_pool(mut self, denom_a: Denom, denom_b: Denom, pool: Pool) -> Self {
        self.pools
            .get_or_insert_with(Default::default)
            .entry((denom_a, denom_b))
            .or_default()
            .push(pool);

        self
    }

    pub fn with_arbbot(mut self) -> Self {
        self.run_arbbot = Some(true);

        self
    }
}

/// A pool that should be tested against.
#[derive(Clone)]
pub enum Pool {
    Astroport(AstroportPool),
    Osmosis(OsmosisPool),
    Auction(AuctionPool),
}

/// Represents an astroport xyk pool.
#[derive(Builder, Clone)]
#[builder(setter(into, strip_option, prefix = "with"))]
pub struct AstroportPool {
    pub balance_asset_a: u128,
    pub balance_asset_b: u128,
}

/// Represents an osmosis gamm pool.
#[derive(Builder, Clone)]
#[builder(setter(into, strip_option, prefix = "with"), build_fn(skip))]
pub struct OsmosisPool {
    #[builder(default)]
    denom_funds: HashMap<Denom, u128>,

    #[builder(default)]
    denom_weights: HashMap<Denom, u128>,
}

impl OsmosisPoolBuilder {
    pub fn with_funds(&mut self, denom: Denom, funds: u128) -> &mut Self {
        self.denom_funds
            .get_or_insert_with(Default::default)
            .insert(denom.into(), funds);

        self
    }

    pub fn with_weight(&mut self, denom: Denom, weight: u128) -> &mut Self {
        self.denom_weights
            .get_or_insert_with(Default::default)
            .insert(denom.into(), weight);

        self
    }

    pub fn build(&mut self) -> OsmosisPool {
        OsmosisPool {
            denom_funds: self.denom_funds.clone().unwrap_or_default(),
            denom_weights: self.denom_weights.clone().unwrap_or_default(),
        }
    }
}

/// Represents a valence auction.
#[derive(Builder, Clone)]
#[builder(setter(into, strip_option, prefix = "with"))]
pub struct AuctionPool {
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
        .arg("net_config_test.json")
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

            let res = test_handle(Some(arbfile));

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
