use clap::Parser;
use cosmwasm_std::Decimal;
use localic_utils::{ConfigChainBuilder, TestContextBuilder};
use setup::{
    Args, AstroportPoolBuilder, AuctionPoolBuilder, Denom, OsmosisPoolBuilder, Pool, TestBuilder,
    TestFn, TestRunner,
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
        .with_transfer_channels("neutron", "osmosis")
        .build()?;

    let bruhtoken = Denom::Local {
        base_chain: String::from("neutron"),
        base_denom: format!("factory/{OWNER_ADDR}/bruhtoken"),
    };
    let amoguscoin = Denom::Local {
        base_chain: String::from("neutron"),
        base_denom: format!("factory/{OWNER_ADDR}/amoguscoin"),
    };
    let untrn = Denom::Local {
        base_chain: String::from("neutron"),
        base_denom: String::from("untrn"),
    };

    let bruhtoken_osmo = Denom::Interchain {
        base_chain: String::from("neutron"),
        dest_chain: String::from("osmosis"),
        base_denom: format!("factory/{OWNER_ADDR}/bruhtoken"),
    };
    let amoguscoin_osmo = Denom::Interchain {
        base_chain: String::from("neutron"),
        dest_chain: String::from("osmosis"),
        base_denom: format!("factory/{OWNER_ADDR}/amoguscoin"),
    };
    let untrn_osmo = Denom::Interchain {
        base_denom: String::from("untrn"),
        base_chain: String::from("neutron"),
        dest_chain: String::from("osmosis"),
    };
    let uosmo = Denom::Local {
        base_chain: String::from("osmosis"),
        base_denom: String::from("uosmo"),
    };

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
                .with_denom(untrn.clone(), 100000000000)
                .with_denom(bruhtoken.clone(), 100000000000)
                .with_denom(amoguscoin.clone(), 100000000000)
                .with_pool(
                    bruhtoken.clone(),
                    amoguscoin.clone(),
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(bruhtoken.clone())
                            .with_asset_b(amoguscoin.clone())
                            .with_balance_asset_a(15000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    untrn.clone(),
                    amoguscoin.clone(),
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(untrn.clone())
                            .with_asset_b(amoguscoin.clone())
                            .with_balance_asset_a(10000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken.clone(),
                    untrn.clone(),
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken.clone())
                            .with_ask_asset(untrn.clone())
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
                .with_denom(untrn.clone(), 100000000000)
                .with_denom(bruhtoken.clone(), 100000000000)
                .with_denom(amoguscoin.clone(), 100000000000)
                .with_pool(
                    bruhtoken.clone(),
                    amoguscoin.clone(),
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(bruhtoken.clone())
                            .with_asset_b(amoguscoin.clone())
                            .with_balance_asset_a(15000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    untrn.clone(),
                    amoguscoin.clone(),
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(untrn.clone())
                            .with_asset_b(amoguscoin.clone())
                            .with_balance_asset_a(10000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken.clone(),
                    untrn.clone(),
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken.clone())
                            .with_ask_asset(untrn.clone())
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
                .with_denom(untrn.clone(), 100000000000)
                .with_denom(bruhtoken.clone(), 100000000000)
                .with_denom(amoguscoin.clone(), 100000000000)
                .with_pool(
                    bruhtoken.clone(),
                    amoguscoin.clone(),
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(bruhtoken.clone())
                            .with_asset_b(amoguscoin.clone())
                            .with_balance_asset_a(15000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    untrn.clone(),
                    amoguscoin.clone(),
                    Pool::Astroport(
                        AstroportPoolBuilder::default()
                            .with_asset_a(untrn.clone())
                            .with_asset_b(amoguscoin.clone())
                            .with_balance_asset_a(10000000u128)
                            .with_balance_asset_b(10000000u128)
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken.clone(),
                    untrn.clone(),
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken.clone())
                            .with_ask_asset(untrn.clone())
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
                .with_denom(untrn.clone(), 100000000000)
                .with_denom(bruhtoken.clone(), 100000000000)
                .with_denom(amoguscoin.clone(), 100000000000)
                .with_pool(
                    untrn.clone(),
                    bruhtoken.clone(),
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(bruhtoken.clone())
                            .with_ask_asset(untrn.clone())
                            .with_balance_offer_asset(10000000000u128)
                            .with_price(Decimal::percent(100))
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken.clone(),
                    untrn.clone(),
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(amoguscoin.clone())
                            .with_ask_asset(bruhtoken.clone())
                            .with_balance_offer_asset(10000000000u128)
                            .with_price(Decimal::percent(90))
                            .build()?,
                    ),
                )
                .with_pool(
                    bruhtoken.clone(),
                    untrn.clone(),
                    Pool::Auction(
                        AuctionPoolBuilder::default()
                            .with_offer_asset(untrn.clone())
                            .with_ask_asset(amoguscoin.clone())
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
        // - Osmo: amoguscoin-OSMO @ 1 OSMO/amoguscoin
        .run(
            TestBuilder::default()
                .with_name("Osmosis Arb")
                .with_description("The arbitrage bot execute a slightly profitable arb only due to osmosis price differences")
                .with_denom(untrn_osmo.clone(), 100000000000)
                .with_denom(uosmo.clone(), 100000000000)
                .with_denom(bruhtoken.clone(), 100000000000)
                .with_denom(amoguscoin.clone(), 100000000000)
                .with_pool(
                    untrn_osmo.clone(),
                    uosmo.clone(),
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(untrn_osmo.clone(), 10000000u128)
                            .with_funds(uosmo.clone(), 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    uosmo.clone(),
                    bruhtoken_osmo.clone(),
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(uosmo.clone(), 10000000u128)
                            .with_funds(bruhtoken_osmo.clone(), 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    amoguscoin_osmo.clone(),
                    bruhtoken_osmo.clone(),
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(amoguscoin_osmo.clone(), 15000000u128)
                            .with_funds(bruhtoken_osmo.clone(), 10000000u128)
                            .build(),
                    ),
                )
                .with_pool(
                    amoguscoin_osmo.clone(),
                    uosmo.clone(),
                    Pool::Osmosis(
                        OsmosisPoolBuilder::default()
                            .with_funds(amoguscoin_osmo.clone(), 10000000u128)
                            .with_funds(uosmo.clone(), 10000000u128)
                            .build(),
                    ),
                )
                .with_test(Box::new(tests::test_osmo_arb) as TestFn)
                .build()?,
        )?
        .join()
}
