use cosmwasm_std::Decimal;
use itertools::Itertools;
use localic_utils::{
    error::Error, types::contract::MinAmount, utils::test_context::TestContext, ConfigChainBuilder,
    TestContextBuilder, NEUTRON_CHAIN_NAME, OSMOSIS_CHAIN_NAME,
};
use notify::{Event, EventKind, RecursiveMode, Result as NotifyResult, Watcher};
use serde_json::Value;
use shared_child::SharedChild;
use std::{
    collections::HashMap,
    error::Error as StdError,
    fs::OpenOptions,
    path::Path,
    process::ExitStatus,
    process::{self, Command},
    sync::Arc,
};

mod util;

/// Tokens that arbitrage is tested against
const TEST_TOKENS: [&str; 2] = ["bruhtoken", "amoguscoin"];

/// Test wallet mnemonic
const TEST_MNEMONIC: &str = "south excuse merit payment amazing trash salon core cloth wine claw father fiscal anger entry hawk equip cream key inner away outdoor despair air";

/// Path to a file where found arbs are stored
const ARBFILE_PATH: &str = "../arbs.json";

/// The address that should principally own all contracts
const OSMO_OWNER_ADDR: &str = "osmo1hj5fveer5cjtn4wd6wstzugjfdxzl0xpwhpz63";
const OWNER_ADDR: &str = "neutron1hj5fveer5cjtn4wd6wstzugjfdxzl0xpznmsky";

fn main() -> Result<(), Box<dyn StdError>> {
    let mut ctx = TestContextBuilder::default()
        .with_artifacts_dir("contracts")
        .with_unwrap_raw_logs(true)
        .with_chain(ConfigChainBuilder::default_neutron().build()?)
        .with_chain(ConfigChainBuilder::default_osmosis().build()?)
        .with_transfer_channel("neutron", "osmosis")
        .with_transfer_channel("osmosis", "neutron")
        .build()?;

    // Mapping of denoms to their matching denoms, chain id's, channel id's, and ports
    let mut denom_map: HashMap<String, Vec<HashMap<String, String>>> = Default::default();

    fn denom_map_entry_for(
        denom: &str,
        src_chain: &str,
        dest_chain: &str,
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

    denom_map.insert(
        String::from("untrn"),
        vec![denom_map_entry_for("untrn", "neutron", "osmosis", &mut ctx)
            .expect("Failed to get denom map entry for untrn")],
    );

    ctx.build_tx_transfer()
        .with_chain_name("osmosis")
        .with_recipient(OWNER_ADDR)
        .with_denom(&denom_map["untrn"][0]["denom"].clone())
        .with_amount(1)
        .send()?;

    denom_map.insert(
        denom_map["untrn"][0]["denom"].clone(),
        vec![denom_map_entry_for(
            &denom_map["untrn"][0]["denom"],
            "osmosis",
            "neutron",
            &mut ctx,
        )
        .expect("Failed to get denom map entry for untrn osmosis denom")],
    );

    // Create tokens w tokenfactory for all test tokens
    for token in TEST_TOKENS.into_iter() {
        ctx.build_tx_create_tokenfactory_token()
            .with_subdenom(token)
            .send()?;
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

    let token_pairs = token_denoms
        .iter()
        .cloned()
        .permutations(2)
        .unique_by(|tokens| {
            let mut to_sort = tokens.clone();
            to_sort.sort();

            to_sort
        });

    // Setup astroport
    ctx.build_tx_create_token_registry()
        .with_owner(OWNER_ADDR)
        .send()?;
    ctx.build_tx_create_factory()
        .with_owner(OWNER_ADDR)
        .send()?;

    // Create pools for each token against ntrn
    for mut tokens in token_pairs.clone() {
        let token_a = tokens.remove(0);
        let token_b = tokens.remove(0);

        ctx.build_tx_create_pool()
            .with_denom_a(&token_a)
            .with_denom_b(&token_b)
            .send()?;
        ctx.build_tx_fund_pool()
            .with_denom_a(&token_a)
            .with_denom_b(&token_b)
            .with_amount_denom_a((rand::random::<f64>() * 10000000000000.0) as u128 + 1000)
            .with_amount_denom_b((rand::random::<f64>() * 10000000000000.0) as u128 + 1000)
            .with_liq_token_receiver(OWNER_ADDR)
            .with_slippage_tolerance(Decimal::percent(50))
            .send()?;
    }

    // Create pools for each token on osmosis
    for mut tokens in token_pairs.clone() {
        let token_a = tokens.remove(0);
        let token_b = tokens.remove(0);

        let weight_a = (rand::random::<f64>() * 10.0) as u64 + 1;
        let weight_b = (rand::random::<f64>() * 10.0) as u64 + 1;

        let scale = (rand::random::<f64>() * 1000.0) as u64 + 1;

        ctx.build_tx_transfer()
            .with_chain_name("neutron")
            .with_recipient(OSMO_OWNER_ADDR)
            .with_denom(&token_a)
            .with_amount((scale * weight_a) as u128)
            .send()?;
        ctx.build_tx_transfer()
            .with_chain_name("neutron")
            .with_recipient(OSMO_OWNER_ADDR)
            .with_denom(&token_a)
            .with_amount((scale * weight_a) as u128)
            .send()?;

        ctx.build_tx_transfer()
            .with_chain_name("neutron")
            .with_recipient(OSMO_OWNER_ADDR)
            .with_denom(&token_b)
            .with_amount((scale * weight_b) as u128)
            .send()?;
        ctx.build_tx_transfer()
            .with_chain_name("neutron")
            .with_recipient(OSMO_OWNER_ADDR)
            .with_denom(&token_b)
            .with_amount((scale * weight_b) as u128)
            .send()?;

        let ibc_denom_a = ctx
            .get_ibc_denom(&token_a, NEUTRON_CHAIN_NAME, OSMOSIS_CHAIN_NAME)
            .ok_or(Error::MissingContextVariable(format!(
                "ibc_denom::{}",
                &token_a
            )))?;
        let ibc_denom_b = ctx
            .get_ibc_denom(&token_b, NEUTRON_CHAIN_NAME, OSMOSIS_CHAIN_NAME)
            .ok_or(Error::MissingContextVariable(format!(
                "ibc_denom::{}",
                &token_b
            )))?;

        denom_map.insert(
            String::from(token_a.clone()),
            vec![
                denom_map_entry_for(&token_a, "neutron", "osmosis", &mut ctx)
                    .expect("Failed to get denom map entry for untrn"),
            ],
        );
        denom_map.insert(
            String::from(token_b.clone()),
            vec![
                denom_map_entry_for(&token_b, "neutron", "osmosis", &mut ctx)
                    .expect("Failed to get denom map entry for untrn"),
            ],
        );

        ctx.build_tx_transfer()
            .with_chain_name("osmosis")
            .with_recipient(OWNER_ADDR)
            .with_denom(&ibc_denom_a)
            .with_amount(1)
            .send()?;
        ctx.build_tx_transfer()
            .with_chain_name("osmosis")
            .with_recipient(OWNER_ADDR)
            .with_denom(&ibc_denom_b)
            .with_amount(1)
            .send()?;

        denom_map.insert(
            String::from(ibc_denom_a.clone()),
            vec![
                denom_map_entry_for(&ibc_denom_a, "osmosis", "neutron", &mut ctx)
                    .expect("Failed to get denom map entry for untrn"),
            ],
        );
        denom_map.insert(
            String::from(ibc_denom_b.clone()),
            vec![
                denom_map_entry_for(&ibc_denom_b, "osmosis", "neutron", &mut ctx)
                    .expect("Failed to get denom map entry for untrn"),
            ],
        );

        ctx.build_tx_create_osmo_pool()
            .with_weight(&ibc_denom_a, weight_a)
            .with_weight(&ibc_denom_b, weight_b)
            .with_initial_deposit(&ibc_denom_a, (scale * weight_a) as u64)
            .with_initial_deposit(&ibc_denom_b, (scale * weight_b) as u64)
            .send()?;

        let pool_id = ctx.get_osmo_pool(&ibc_denom_a, &ibc_denom_b)?;

        // Fund the pool
        ctx.build_tx_fund_osmo_pool()
            .with_pool_id(pool_id)
            .with_max_amount_in(&ibc_denom_a, (scale * weight_a) as u64)
            .with_max_amount_in(&ibc_denom_b, (scale * weight_b) as u64)
            .with_share_amount_out(1000000000000)
            .send()?;
    }

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

    for mut tokens in token_pairs {
        let token_a = tokens.remove(0);
        let token_b = tokens.remove(0);

        ctx.build_tx_create_auction()
            .with_offer_asset(&token_a)
            .with_ask_asset(&token_b)
            .with_amount_offer_asset((rand::random::<f64>() * 10000000000000.0) as u128 + 1000000)
            .send()?;

        ctx.build_tx_manual_oracle_price_update()
            .with_offer_asset(&token_a)
            .with_ask_asset(&token_b)
            .with_price(Decimal::percent(10))
            .send()?;

        ctx.build_tx_fund_auction()
            .with_offer_asset(&token_a)
            .with_ask_asset(&token_b)
            .with_amount_offer_asset((rand::random::<f64>() * 10000000000000.0) as u128 + 1000000)
            .send()?;
        ctx.build_tx_start_auction()
            .with_offer_asset(&token_a)
            .with_ask_asset(&token_b)
            .with_end_block_delta(10000)
            .send()?;
    }

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
    util::create_denom_file(denom_map)?;

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
                let arbs = arbfile.as_array().expect("no arbs in arbfile");

                assert!(!arbs.is_empty());

                let profit: u64 = arbs
                    .into_iter()
                    .filter_map(|arb_str| arb_str.as_str())
                    .filter_map(|arb_str| {
                        serde_json::from_str::<Value>(arb_str)
                            .ok()?
                            .get("realized_profit")?
                            .as_number()?
                            .as_u64()
                    })
                    .sum();
                let non_atomic_profit: u64 = arbs
                    .into_iter()
                    .filter_map(|arb_str| arb_str.as_str())
                    .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
                    .filter(|arb| {
                        arb.get("route")
                            .and_then(|route| route.as_str())
                            .map(|route| route.contains("osmo"))
                            .unwrap_or_default()
                    })
                    .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
                    .sum();

                println!("ARB BOT PROFIT: {profit}");
                println!("OSMO BOT PROFIT: {non_atomic_profit}");

                assert!(profit > 0);
                assert!(non_atomic_profit > 0);

                proc_handle_watcher.kill().expect("failed to kill arb bot");

                process::exit(0);
            }
            _ => {}
        }
    })?;

    watcher.watch(Path::new(ARBFILE_PATH), RecursiveMode::NonRecursive)?;
    let exit_status = proc_handle.wait()?;

    assert!(exit_status.success());

    Ok(())
}
