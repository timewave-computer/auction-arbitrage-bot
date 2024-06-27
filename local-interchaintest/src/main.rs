use localic_utils::{types::contract::MinAmount, ConfigChainBuilder, TestContextBuilder};
use notify::{Event, RecursiveMode, Result as NotifyResult, Watcher};
use std::{error::Error as StdError, path::Path, process::Command};

mod util;

/// Tokens that arbitrage is tested against
const TEST_TOKENS: [&str; 4] = ["bruhtoken", "amoguscoin", "susdao", "skibidicoin"];

/// The address that should principally own all contracts
const OWNER_ADDR: &str = "neutron1kuf2kxwuv2p8k3gnpja7mzf05zvep0cyuy7mxg";

fn main() -> Result<(), Box<dyn StdError>> {
    let mut ctx = TestContextBuilder::default()
        .with_artifacts_dir("contracts")
        .with_chain(ConfigChainBuilder::default_neutron().build()?)
        .build()?;

    ctx.build_tx_upload_contracts().send()?;

    // Create tokens w tokenfactory for all test tokens
    for token in TEST_TOKENS.into_iter() {
        ctx.build_tx_create_tokenfactory_token()
            .with_subdenom(token)
            .send()?;
    }

    let token_denoms = TEST_TOKENS
        .into_iter()
        .map(|token| format!("factory/{OWNER_ADDR}/{token}"));

    // Setup astroport
    ctx.build_tx_create_token_registry()
        .with_owner(OWNER_ADDR)
        .send()?;
    ctx.build_tx_create_factory()
        .with_owner(OWNER_ADDR)
        .send()?;

    // Create pools for each token against ntrn
    for token in token_denoms.clone() {
        ctx.build_tx_create_pool()
            .with_denom_a("untrn")
            .with_denom_b(&token)
            .send()?;
        ctx.build_tx_fund_pool()
            .with_denom_a("untrn")
            .with_denom_b(&token)
            .with_amount_denom_a((rand::random::<f64>() * 1000.0) as u128)
            .with_amount_denom_b((rand::random::<f64>() * 1000.0) as u128)
            .with_liq_token_receiver(OWNER_ADDR)
            .send()?;
    }

    // Create a valence auction manager and an auction for each token
    ctx.build_tx_create_auctions_manager()
        .with_min_auction_amount(&[(
            &String::from("untrn"),
            MinAmount {
                send: "0".into(),
                start_auction: "0".into(),
            },
        )])
        .send()?;
    for token in token_denoms {
        ctx.build_tx_create_auction()
            .with_offer_asset("untrn")
            .with_ask_asset(&token)
            .with_amount_offer_asset((rand::random::<f64>() * 1000.0) as u128)
            .send()?;
        ctx.build_tx_fund_auction()
            .with_offer_asset("untrn")
            .with_ask_asset(&token)
            .with_amount_offer_asset((rand::random::<f64>() * 1000.0) as u128)
            .send()?;
        ctx.build_tx_start_auction()
            .with_offer_asset("untrn")
            .with_ask_asset(&token)
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

    Command::new("python")
        .current_dir("..")
        .arg("main.py")
        .arg("--deployments_file")
        .arg("/tmp/deployments_file.json")
        .env(
            "WALLET_MNEMONIC",
            "dutch suspect purchase critic kind candy clarify polar degree kitchen trend impulse",
        )
        .spawn()?;

    // Wait until the arbs.json file has been produced
    let mut watcher = notify::recommended_watcher(|res: NotifyResult<Event>| {
        res.expect("failed to watch arbs.json");
    })?;

    watcher.watch(Path::new("../arbs.json"), RecursiveMode::NonRecursive)?;

    Ok(())
}
