use cosmwasm_std::Decimal;
use itertools::Itertools;
use localic_utils::{types::contract::MinAmount, ConfigChainBuilder, TestContextBuilder};
use notify::{Event, EventKind, RecursiveMode, Result as NotifyResult, Watcher};
use serde_json::Value;
use shared_child::SharedChild;
use std::{
    error::Error as StdError,
    fs::OpenOptions,
    path::Path,
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
const OWNER_ADDR: &str = "neutron1kuf2kxwuv2p8k3gnpja7mzf05zvep0cyuy7mxg";

fn main() -> Result<(), Box<dyn StdError>> {
    let mut ctx = TestContextBuilder::default()
        .with_artifacts_dir("contracts")
        .with_unwrap_raw_logs(false)
        .with_chain(
            ConfigChainBuilder::default_neutron()
                .with_chain_id("neutron-1")
                .build()?,
        )
        .with_chain(
            ConfigChainBuilder::default_osmosis()
                .with_chain_id("neutron-1")
                .build()?,
        )
        .build()?;

    ctx.build_tx_upload_contracts().send()?;

    // Create tokens w tokenfactory for all test tokens
    for token in TEST_TOKENS.into_iter() {
        ctx.build_tx_create_tokenfactory_token()
            .with_subdenom(token)
            .send()?;
        ctx.build_tx_mint_tokenfactory_token()
            .with_denom(format!("factory/{OWNER_ADDR}/{token}").as_str())
            .with_amount(100000000000000000)
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

    let mut cmd = Command::new("python");
    cmd.current_dir("..")
        .arg("main.py")
        .arg("--deployments_file")
        .arg("deployments_file.json")
        .arg("--net_config")
        .arg("net_config.json")
        .arg("--base_denom")
        .arg("untrn")
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

                println!("ARB BOT PROFIT: {profit}");

                assert!(profit > 0);

                proc_handle_watcher.kill().expect("failed to kill arb bot");
                process::exit(0);
            }
            _ => {}
        }
    })?;

    watcher.watch(Path::new(ARBFILE_PATH), RecursiveMode::NonRecursive)?;
    proc_handle.wait()?;

    Ok(())
}
