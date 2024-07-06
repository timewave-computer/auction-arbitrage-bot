use clap::Parser;
use cosmwasm_std::Decimal;
use localic_utils::{ConfigChainBuilder, TestContextBuilder};
use setup::{
    Args, AstroportPoolBuilder, AuctionPoolBuilder, OsmosisPoolBuilder, Pool, TestBuilder, TestFn,
    TestRunner,
};
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
const OSMO_OWNER_ADDR: &str = "osmo1hj5fveer5cjtn4wd6wstzugjfdxzl0xpwhpz63";

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

    let bruhtoken_owned_osmo = format!("factory/{OSMO_OWNER_ADDR}/bruhtoken");
    let amoguscoin_owned_osmo = format!("factory/{OSMO_OWNER_ADDR}/amoguscoin");

    let bruhtoken_osmo = &bruhtoken_owned_osmo;
    let amoguscoin_osmo = &amoguscoin_owned_osmo;
    let uosmo = "uosmo";
    let untrn_osmo = ctx
        .get_ibc_denom(untrn, "neutron", "osmosis")
        .expect("Missing IBC denom for untrn");

    TestRunner::new(&mut ctx, args)
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
                .with_denom("neutron", "untrn", 100000000000)
                .with_denom("neutron", bruhtoken, 100000000000)
                .with_denom("neutron", amoguscoin, 100000000000)
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
                .with_denom("neutron", "untrn", 100000000000)
                .with_denom("neutron", bruhtoken, 100000000000)
                .with_denom("neutron", amoguscoin, 100000000000)
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
        // Test case (astroport arb):
        //
        // - Astroport: bruhtoken-amoguscoin @1.5 bruhtoken/amoguscoin
        // - Auction: NTRN-bruhtoken @ 1 bruhtoken/NTRN
        // - Astroport: amoguscoin-NTRN @ 1 NTRN/amoguscoin
        .run(
            TestBuilder::default()
                .with_name("Astro-Profitable Arb")
                .with_description("The arbitrage bot execute a slightly profitable arb only due to astroport price differences")
                .with_denom("neutron", "untrn", 100000000000)
                .with_denom("neutron", bruhtoken, 100000000000)
                .with_denom("neutron", amoguscoin, 100000000000)
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
                            .with_price(Decimal::percent(100))
                            .build()?,
                    ),
                )
                .with_test(Box::new(tests::test_unprofitable_arb) as TestFn)
                .build()?,
        )?
        // Test case (auction arb):
        //
        // - Auction: NTRN-bruhtoken @ 1 bruhtoken/NTRN
        // - Auction: bruhtoken-amoguscoin @ 1.5 amoguscoin/bruhtoken
        // - Auction: amoguscoin-NTRN @ 1 NTRN/amoguscoin
        .run(
            TestBuilder::default()
                .with_name("Auction-Profitable Arb")
                .with_description("The arbitrage bot execute a slightly profitable arb only due to auction price differences")
                .with_denom("neutron", "untrn", 100000000000)
                .with_denom("neutron", bruhtoken, 100000000000)
                .with_denom("neutron", amoguscoin, 100000000000)
                .with_pool(
                    untrn,
                    bruhtoken,
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken)
                            .with_ask_asset(untrn)
                            .with_balance_offer_asset(10000000000u128)
                            .with_price(Decimal::percent(100))
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken,
                    untrn,
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(amoguscoin)
                            .with_ask_asset(bruhtoken)
                            .with_balance_offer_asset(10000000000u128)
                            .with_price(Decimal::percent(90))
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken,
                    untrn,
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(untrn)
                            .with_ask_asset(amoguscoin)
                            .with_balance_offer_asset(10000000000u128)
                            .with_price(Decimal::percent(100))
                            .build()?,
                    ),
                )
                .with_test(Box::new(tests::test_unprofitable_arb) as TestFn)
                .build()?,
        )?
        // Test case (osmo -> osmos arb):
        //
        // - Osmo: untrn-uosmo @ 1 untrn/uosmo
        // - Osmo: OSMO-bruhtoken @ 1 bruhtoken/OSMO
        // - Osmo: bruhtoken-amoguscoin @ 1.5 amoguscoin/bruhtoken
        // - Osmo: amoguscoin-OSMO @ 1 NTRN/amoguscoin
        .run(
            TestBuilder::default()
                .with_name("Osmosis Arb")
                .with_description("The arbitrage bot execute a slightly profitable arb only due to osmosis price differences")
                .with_denom("osmosis", "uosmo", 100000000000)
                .with_denom("osmosis", bruhtoken, 100000000000)
                .with_denom("osmosis", amoguscoin, 100000000000)
                .with_pool(
                    untrn_osmo.clone(),
                    uosmo,
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(untrn_osmo.clone(), 10000000u128)
                            .with_funds(uosmo, 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    uosmo,
                    bruhtoken_osmo,
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(uosmo, 10000000u128)
                            .with_funds(bruhtoken_osmo, 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    amoguscoin_osmo,
                    bruhtoken_osmo,
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(amoguscoin_osmo, 15000000u128)
                            .with_funds(bruhtoken_osmo, 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    bruhtoken_osmo,
                    uosmo,
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(amoguscoin_osmo, 10000000u128)
                            .with_funds(bruhtoken_osmo, 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    bruhtoken,
                    untrn,
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(untrn)
                            .with_ask_asset(amoguscoin)
                            .with_balance_offer_asset(10000000000u128)
                            .with_price(Decimal::percent(100))
                            .build()?,
                    ),
                )
                .with_test(Box::new(tests::test_unprofitable_arb) as TestFn)
                .build()?,
        )?
        .join()
}
