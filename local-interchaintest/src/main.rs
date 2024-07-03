use clap::Parser;
use cosmwasm_std::Decimal;
use itertools::Itertools;
use localic_utils::{ConfigChainBuilder, TestContextBuilder};
use serde_json::Value;
use setup::TestRunner;
use std::{
    error::Error as StdError,
    panic,
    process::{self},
};

mod util;

mod setup;

/// Tokens that arbitrage is tested against
const TEST_TOKENS: [&str; 2] = ["bruhtoken", "amoguscoin"];

/// Test wallet mnemonic
const TEST_MNEMONIC: &str = "decorate bright ozone fork gallery riot bus exhaust worth way bone indoor calm squirrel merry zero scheme cotton until shop any excess stage laundry";

/// Path to a file where found arbs are stored
const ARBFILE_PATH: &str = "../arbs.json";

/// The address that should principally own all contracts
const OSMO_OWNER_ADDR: &str = "osmo1hj5fveer5cjtn4wd6wstzugjfdxzl0xpwhpz63";
const OWNER_ADDR: &str = "neutron1hj5fveer5cjtn4wd6wstzugjfdxzl0xpznmsky";

#[derive(Parser, Debug)]
struct Args {
    #[arg(short, long, default_value_t = false)]
    cached: bool,
}

fn main() -> Result<(), Box<dyn StdError + Send + Sync>> {
    let orig_hook = panic::take_hook();
    panic::set_hook(Box::new(move |panic_info| {
        orig_hook(panic_info);
        process::exit(1);
    }));

    let args = Args::parse();

    let mut ctx = TestContextBuilder::default()
        .with_artifacts_dir("contracts")
        .with_unwrap_raw_logs(true)
        .with_chain(ConfigChainBuilder::default_neutron().build()?)
        .with_chain(ConfigChainBuilder::default_osmosis().build()?)
        .with_transfer_channel("neutron", "osmosis")
        .with_transfer_channel("osmosis", "neutron")
        .build()?;

    TestRunner::new(&mut ctx, args.cached)
        .start()?
        .run(
            Box::new(|ctx| {
                let mut token_denoms = TEST_TOKENS
                    .into_iter()
                    .map(|token| format!("factory/{OWNER_ADDR}/{token}"))
                    .collect::<Vec<String>>();
                token_denoms.push("untrn".to_owned());

                let token_pairs =
                    token_denoms
                        .iter()
                        .cloned()
                        .permutations(2)
                        .unique_by(|tokens| {
                            let mut to_sort = tokens.clone();
                            to_sort.sort();

                            to_sort
                        });

                for mut tokens in token_pairs {
                    let token_a = tokens.remove(0);
                    let token_b = tokens.remove(0);

                    ctx.build_tx_fund_pool()
                        .with_denom_a(&token_a)
                        .with_denom_b(&token_b)
                        .with_amount_denom_a(
                            (rand::random::<f64>() * 10000000000000.0) as u128 + 1000,
                        )
                        .with_amount_denom_b(
                            (rand::random::<f64>() * 10000000000000.0) as u128 + 1000,
                        )
                        .with_liq_token_receiver(OWNER_ADDR)
                        .with_slippage_tolerance(Decimal::percent(50))
                        .send()?;

                    ctx.build_tx_create_auction()
                        .with_offer_asset(&token_a)
                        .with_ask_asset(&token_b)
                        .with_amount_offer_asset(
                            (rand::random::<f64>() * 10000000000000.0) as u128 + 1000000,
                        )
                        .send()?;

                    ctx.build_tx_manual_oracle_price_update()
                        .with_offer_asset(&token_a)
                        .with_ask_asset(&token_b)
                        .with_price(Decimal::percent(10))
                        .send()?;

                    ctx.build_tx_fund_auction()
                        .with_offer_asset(&token_a)
                        .with_ask_asset(&token_b)
                        .with_amount_offer_asset(
                            (rand::random::<f64>() * 10000000000000.0) as u128 + 1000000,
                        )
                        .send()?;
                    ctx.build_tx_start_auction()
                        .with_offer_asset(&token_a)
                        .with_ask_asset(&token_b)
                        .with_end_block_delta(10000)
                        .send()?;
                }

                setup::with_arb_bot_output(Box::new(|arbfile: Value| {
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
                    let auction_profit: u64 = arbs
                        .into_iter()
                        .filter_map(|arb_str| arb_str.as_str())
                        .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
                        .filter(|arb| {
                            arb.get("route")
                                .and_then(|route| route.as_str())
                                .map(|route| route.contains("auction"))
                                .unwrap_or_default()
                        })
                        .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
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
                    println!("AUCTION BOT PROFIT: {auction_profit}");
                    println!("OSMO BOT PROFIT: {non_atomic_profit}");

                    assert!(profit > 0);
                    assert!(auction_profit > 0);
                    assert!(non_atomic_profit > 0);

                    Ok(())
                }))
            }),
            "Profitable auction arb",
            "The arbitrage bot should execute an auction arbitrage route successfully.",
        )?
        .join()
}
