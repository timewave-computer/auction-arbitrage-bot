use clap::Parser;
use cosmwasm_std::Decimal;
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

                let bruhtoken = token_denoms.remove(0);
                let amoguscoin = token_denoms.remove(0);
                let untrn = token_denoms.remove(0);

                // Test case:
                // - Astroport: bruhtoken-amoguscoin @ 1.5 bruhtoken / amoguscoin
                // - Auction: NTRN-bruhtoken @ 10 bruhtoken / NTRN
                // - Astroport: amoguscoin-NTRN @ 1 NTRN / amoguscoin
                ctx.build_tx_create_pool()
                    .with_denom_a(&bruhtoken)
                    .with_denom_b(&amoguscoin)
                    .send()?;
                ctx.build_tx_fund_pool()
                    .with_denom_a(&bruhtoken)
                    .with_denom_b(&amoguscoin)
                    .with_amount_denom_a(15000000)
                    .with_amount_denom_b(10000000)
                    .with_liq_token_receiver(OWNER_ADDR)
                    .with_slippage_tolerance(Decimal::percent(50))
                    .send()?;

                ctx.build_tx_create_pool()
                    .with_denom_a(&untrn)
                    .with_denom_b(&amoguscoin)
                    .send()?;
                ctx.build_tx_fund_pool()
                    .with_denom_a(&untrn)
                    .with_denom_b(&amoguscoin)
                    .with_amount_denom_a(10000000)
                    .with_amount_denom_b(10000000)
                    .with_liq_token_receiver(OWNER_ADDR)
                    .with_slippage_tolerance(Decimal::percent(50))
                    .send()?;

                ctx.build_tx_create_auction()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_amount_offer_asset(1000000)
                    .send()?;

                ctx.build_tx_manual_oracle_price_update()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_price(Decimal::percent(10))
                    .send()?;

                ctx.build_tx_fund_auction()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_amount_offer_asset(10000000)
                    .send()?;
                ctx.build_tx_start_auction()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_end_block_delta(10000)
                    .send()?;

                setup::with_arb_bot_output(Box::new(|arbfile: Value| {
                    let arbs = arbfile.as_array().expect("no arbs in arbfile");

                    util::assert_err("!arbs.is_empty()", !arbs.is_empty())?;

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
                        .filter(|arb_str| arb_str.contains("auction"))
                        .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
                        .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
                        .sum();

                    println!("ARB BOT PROFIT: {profit}");
                    println!("AUCTION BOT PROFIT: {auction_profit}");

                    util::assert_err("profit == 466496", profit == 466496)?;
                    util::assert_err("auction_profit == 466496", auction_profit == 466496)?;

                    Ok(())
                }))
            }),
            "Profitable auction arb",
            "The arbitrage bot should execute an auction arbitrage route successfully.",
        )?
        .run(
            Box::new(|ctx| {
                let mut token_denoms = TEST_TOKENS
                    .into_iter()
                    .map(|token| format!("factory/{OWNER_ADDR}/{token}"))
                    .collect::<Vec<String>>();
                token_denoms.push("untrn".to_owned());

                let bruhtoken = token_denoms.remove(0);
                let amoguscoin = token_denoms.remove(0);
                let untrn = token_denoms.remove(0);

                // Test case:
                // - Astroport: bruhtoken-amoguscoin @ 1.5 bruhtoken / amoguscoin
                // - Auction: NTRN-bruhtoken @ 0.1 bruhtoken / NTRN
                // - Astroport: amoguscoin-NTRN @ 1 NTRN / amoguscoin
                //
                // No arb should be executed, since this is not profitable.
                ctx.build_tx_create_pool()
                    .with_denom_a(&bruhtoken)
                    .with_denom_b(&amoguscoin)
                    .send()?;
                ctx.build_tx_fund_pool()
                    .with_denom_a(&bruhtoken)
                    .with_denom_b(&amoguscoin)
                    .with_amount_denom_a(15000000)
                    .with_amount_denom_b(10000000)
                    .with_liq_token_receiver(OWNER_ADDR)
                    .with_slippage_tolerance(Decimal::percent(50))
                    .send()?;

                ctx.build_tx_create_pool()
                    .with_denom_a(&untrn)
                    .with_denom_b(&amoguscoin)
                    .send()?;
                ctx.build_tx_fund_pool()
                    .with_denom_a(&untrn)
                    .with_denom_b(&amoguscoin)
                    .with_amount_denom_a(10000000)
                    .with_amount_denom_b(10000000)
                    .with_liq_token_receiver(OWNER_ADDR)
                    .with_slippage_tolerance(Decimal::percent(50))
                    .send()?;

                ctx.build_tx_create_auction()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_amount_offer_asset(1000000)
                    .send()?;

                ctx.build_tx_manual_oracle_price_update()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_price(Decimal::percent(1000))
                    .send()?;

                ctx.build_tx_fund_auction()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_amount_offer_asset(10000000)
                    .send()?;
                ctx.build_tx_start_auction()
                    .with_offer_asset(&bruhtoken)
                    .with_ask_asset(&untrn)
                    .with_end_block_delta(10000)
                    .send()?;

                setup::with_arb_bot_output(Box::new(|arbfile: Value| {
                    let arbs = arbfile.as_array().expect("no arbs in arbfile");

                    // Arbs should not be attempted or queued
                    util::assert_err("!arbs.is_empty()", !arbs.is_empty())?;

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

                    util::assert_err("profit == 0", profit == 0)?;

                    Ok(())
                }))
            }),
            "Unprofitable auction arb",
            "The arbitrage bot should not execute an auction arbitrage route when it is not profitable.",
        )?
        .join()
}
