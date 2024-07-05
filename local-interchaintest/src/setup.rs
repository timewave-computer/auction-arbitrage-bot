use super::{util, ARBFILE_PATH, OSMO_OWNER_ADDR, OWNER_ADDR, TEST_MNEMONIC, TEST_TOKENS};
use localic_utils::{types::contract::MinAmount, utils::test_context::TestContext};
use notify::{Event, EventKind, RecursiveMode, Result as NotifyResult, Watcher};
use serde_json::Value;
use shared_child::SharedChild;
use std::{
    collections::HashMap,
    error::Error,
    fs::OpenOptions,
    path::Path,
    process::Command,
    sync::{mpsc, Arc},
};

const EXIT_STATUS_SUCCESS: i32 = 9;
const EXIT_STATUS_SIGKILL: i32 = 9;

/// Runs all provided tests while reporting a final
/// exit status.
pub struct TestRunner<'a> {
    test_statuses: HashMap<(&'a str, &'a str), Result<(), Box<dyn Error + Send + Sync>>>,
    cached: bool,
    denom_map: HashMap<String, Vec<HashMap<String, String>>>,
    test_ctx: &'a mut TestContext,
}

impl<'a> TestRunner<'a> {
    pub fn new(ctx: &'a mut TestContext, cached: bool) -> Self {
        Self {
            test_statuses: Default::default(),
            cached,
            denom_map: Default::default(),
            test_ctx: ctx,
        }
    }

    /// Performs setup that should only be performed once per localic spinup,
    /// including:
    /// - Creating tokenfactory tokens
    pub fn start(&mut self) -> Result<&mut Self, Box<dyn Error + Send + Sync>> {
        if self.cached {
            return Ok(self);
        }

        // Perform cold start setup
        // Create tokens w tokenfactory for all test tokens
        TEST_TOKENS.into_iter().try_for_each(|token| {
            self.test_ctx
                .build_tx_create_tokenfactory_token()
                .with_subdenom(token)
                .send()
        })?;

        Ok(self)
    }

    /// Runs a test with access to the test context with some metadata.
    pub fn run(
        &mut self,
        test: Box<
            dyn Fn(&mut TestContext) -> Result<(), Box<dyn Error + Send + Sync>> + Send + Sync,
        >,
        name: &'a str,
        description: &'a str,
    ) -> Result<&mut Self, Box<dyn Error + Send + Sync>> {
        // Perform hot start setup
        // Mapping of denoms to their matching denoms, chain id's, channel id's, and ports
        self.denom_map = Default::default();

        let ctx = &mut self.test_ctx;

        fn denom_map_entry_for<'a>(
            denom: &str,
            src_chain: &'a str,
            dest_chain: &'a str,
            ctx: &mut TestContext,
        ) -> Option<HashMap<String, String>> {
            let trace = ctx.get_ibc_trace(denom, src_chain, dest_chain)?;

            let mut ent = HashMap::new();
            ent.insert("chain_id".into(), "localosmosis-1".into());
            ent.insert("channel_id".into(), trace.channel_id);
            ent.insert("port_id".into(), "transfer".into());
            ent.insert("denom".into(), trace.dest_denom);

            Some(ent)
        }

        ctx.build_tx_upload_contracts().send()?;

        ctx.build_tx_transfer()
            .with_chain_name("neutron")
            .with_recipient(OSMO_OWNER_ADDR)
            .with_denom("untrn")
            .with_amount(1000000)
            .send()?;

        self.denom_map.insert(
            String::from("untrn"),
            vec![denom_map_entry_for("untrn", "neutron", "osmosis", ctx)
                .expect("Failed to get denom map entry for untrn")],
        );

        ctx.build_tx_transfer()
            .with_chain_name("osmosis")
            .with_recipient(OWNER_ADDR)
            .with_denom(&self.denom_map["untrn"][0]["denom"].clone())
            .with_amount(1)
            .send()?;

        self.denom_map.insert(
            self.denom_map["untrn"][0]["denom"].clone(),
            vec![denom_map_entry_for(
                &self.denom_map["untrn"][0]["denom"],
                "osmosis",
                "neutron",
                ctx,
            )
            .expect("Failed to get denom map entry for untrn osmosis denom")],
        );

        // Create tokens w tokenfactory for all test tokens
        for token in TEST_TOKENS.into_iter() {
            ctx.build_tx_mint_tokenfactory_token()
                .with_denom(format!("factory/{OWNER_ADDR}/{token}").as_str())
                .with_amount(10000000000000000000000)
                .send()?;
        }

        let mut token_denoms = TEST_TOKENS
            .into_iter()
            .map(|token| format!("factory/{OWNER_ADDR}/{token}"))
            .collect::<Vec<String>>();
        token_denoms.push("untrn".to_owned());

        // Setup astroport
        ctx.build_tx_create_token_registry()
            .with_owner(OWNER_ADDR)
            .send()?;
        ctx.build_tx_create_factory()
            .with_owner(OWNER_ADDR)
            .send()?;

        let min_tokens = token_denoms
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
                .remove(0)
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

        self.test_statuses
            .insert((name, description), test(self.test_ctx));

        Ok(self)
    }

    /// Produces a single result representing any failure that may
    /// have occurred in any test.
    /// Logs successes to stdout, and failures to stderr.
    pub fn join(&mut self) -> Result<(), Box<dyn Error + Send + Sync>> {
        for ((name, description), status) in self.test_statuses.iter() {
            match status {
                Ok(_) => {
                    println!("✅ 🤑 SUCCESS {name} - {description}");
                }
                Err(e) => {
                    eprintln!("❌ ඞ FAILURE {name} - {description}: {:?}", e);
                }
            }
        }

        if let Some((_, Err(e))) = self.test_statuses.drain().next() {
            return Err(e);
        }

        Ok(())
    }
}

pub fn with_arb_bot_output(
    test: Box<dyn Fn(Value) -> Result<(), Box<dyn Error + Send + Sync>> + Send>,
) -> Result<(), Box<dyn Error + Send + Sync>> {
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

    // Wait until the arbs.json file has been produced
    let mut watcher = notify::recommended_watcher(move |res: NotifyResult<Event>| {
        let e = res.expect("failed to watch arbs.json");

        // An arb was found
        match e.kind {
            EventKind::Modify(_) => {
                let f = OpenOptions::new()
                    .read(true)
                    .open(ARBFILE_PATH)
                    .expect("failed to open arbs.json");

                if f.metadata().expect("can't get arbs metadata").len() == 0 {
                    return;
                }

                let arbfile: Value =
                    serde_json::from_reader(&f).expect("failed to deserialize arbs.json");

                let res = test(arbfile);

                proc_handle_watcher.kill().expect("failed to kill arb bot");
                tx_res.send(res).expect("failed to send test results");
            }
            _ => {}
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
