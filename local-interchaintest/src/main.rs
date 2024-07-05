use clap::Parser;
use cosmwasm_std::Decimal;
use localic_utils::{ConfigChainBuilder, TestContextBuilder};
use setup::{AstroportPoolBuilder, AuctionPoolBuilder, Pool, TestBuilder, TestFn, TestRunner};
use std::{error::Error as StdError, panic, process};

mod setup;
mod tests;
mod util;

/// Test wallet mnemonic
const TEST_MNEMONIC: &str = "decorate bright ozone fork gallery riot bus exhaust worth way bone indoor calm squirrel merry zero scheme cotton until shop any excess stage laundry";

/// Path to a file where found arbs are stored
const ARBFILE_PATH: &str = "../arbs.json";

/// The address that should principally own all contracts
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

    let bruhtoken_owned = format!("factory/{OWNER_ADDR}/bruhtoken");
    let amoguscoin_owned = format!("factory/{OWNER_ADDR}/amoguscoin");

    let bruhtoken = &bruhtoken_owned;
    let amoguscoin = &amoguscoin_owned;
    let untrn = "untrn";

    TestRunner::new(&mut ctx, args.cached)
        .start()?
        // Test case (profitable arb):
        //
        // - Astroport: bruhtoken-amoguscoin @1.5 bruhtoken/amoguscoin
        // - Auction: NTRN-bruhtoken @ 10 bruhtoken/NTRN
        // - Astroport: amoguscoin-NTRN @ 1 NTRN/amoguscoin
        .run(
            TestBuilder::default()
                .with_name("Profitable Arb")
                .with_description("The arbitrage bot should execute a profitable arb successfully")
                .with_denom("untrn", 10000000000)
                .with_denom(bruhtoken, 10000000000)
                .with_denom(amoguscoin, 10000000000)
                .with_pool(
                    bruhtoken,
                    amoguscoin,
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(bruhtoken)
                            .with_asset_b(amoguscoin)
                            .with_balance_asset_a(15000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    untrn,
                    amoguscoin,
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(untrn)
                            .with_asset_b(amoguscoin)
                            .with_balance_asset_a(10000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken,
                    untrn,
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken)
                            .with_ask_asset(untrn)
                            .with_balance_offer_asset(100000000u128)
                            .with_price(Decimal::percent(10))
                            .build()?,
                    ),
                )
                .with_test(Box::new(tests::test_profitable_arb) as TestFn)
                .build()?,
        )?
        // Test case (unprofitable arb):
        //
        // - Astroport: bruhtoken-amoguscoin @1.5 bruhtoken/amoguscoin
        // - Auction: NTRN-bruhtoken @ 0.1 bruhtoken/NTRN
        // - Astroport: amoguscoin-NTRN @ 1 NTRN/amoguscoin
        .run(
            TestBuilder::default()
                .with_name("Unprofitable Arb")
                .with_description("The arbitrage bot should not execute an unprofitable arb")
                .with_denom("untrn", 10000000)
                .with_denom(bruhtoken, 10000000000)
                .with_denom(amoguscoin, 10000000000)
                .with_pool(
                    bruhtoken,
                    amoguscoin,
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(bruhtoken)
                            .with_asset_b(amoguscoin)
                            .with_balance_asset_a(15000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    untrn,
                    amoguscoin,
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(untrn)
                            .with_asset_b(amoguscoin)
                            .with_balance_asset_a(10000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken,
                    untrn,
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken)
                            .with_ask_asset(untrn)
                            .with_balance_offer_asset(10000000u128)
                            .with_price(Decimal::percent(1000))
                            .build()?,
                    ),
                )
                .with_test(Box::new(tests::test_unprofitable_arb) as TestFn)
                .build()?,
        )?
        .join()
}
