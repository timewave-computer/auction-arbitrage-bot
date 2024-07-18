use super::util;
use serde_json::Value;
use std::{error::Error, process::Command};

const ERROR_MARGIN_PROFIT: u64 = 100000;

pub fn test_transfer_osmosis(
    _: Option<Value>,
) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    Command::new("python")
        .current_dir("tests")
        .arg("transfer_osmosis.py")
        .output()?;

    Ok(())
}

pub fn test_transfer_neutron(
    _: Option<Value>,
) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    Command::new("python")
        .current_dir("tests")
        .arg("transfer_neutron.py")
        .output()?;

    Ok(())
}

pub fn test_profitable_arb(
    arbfile: Option<Value>,
) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let arbfile = arbfile.unwrap();
    let arbs = arbfile.as_array().expect("no arbs in arbfile");

    util::assert_err("!arbs.is_empty()", arbs.is_empty(), false)?;

    let profit: u64 = arbs
        .iter()
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
        .iter()
        .filter_map(|arb_str| arb_str.as_str())
        .filter(|arb_str| arb_str.contains("auction"))
        .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
        .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
        .sum();

    println!("ARB BOT PROFIT: {profit}");
    println!("AUCTION BOT PROFIT: {auction_profit}");

    util::assert_err(
        "500000 + PROFIT_MARGIN > profit > 500000 - PROFIT_MARGIN",
        500000 + ERROR_MARGIN_PROFIT > profit && profit > 500000 - ERROR_MARGIN_PROFIT,
        true,
    )?;
    util::assert_err(
        "500000 + PROFIT_MARGIN > auction_profit > 500000 - PROFIT_MARGIN",
        500000 + ERROR_MARGIN_PROFIT > auction_profit
            && auction_profit > 500000 - ERROR_MARGIN_PROFIT,
        true,
    )?;

    Ok(())
}

pub fn test_unprofitable_arb(
    arbfile: Option<Value>,
) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let arbfile = arbfile.unwrap();
    let arbs = arbfile.as_array().expect("no arbs in arbfile");

    util::assert_err("!arbs.is_empty()", arbs.is_empty(), false)?;

    let profit: u64 = arbs
        .iter()
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
        .iter()
        .filter_map(|arb_str| arb_str.as_str())
        .filter(|arb_str| arb_str.contains("auction"))
        .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
        .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
        .sum();

    println!("ARB BOT PROFIT: {profit}");
    println!("AUCTION BOT PROFIT: {auction_profit}");

    util::assert_err("profit == 0", profit, 0)?;
    util::assert_err("auction_profit == 0", auction_profit, 0)?;

    Ok(())
}

pub fn test_osmo_arb(arbfile: Option<Value>) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let arbfile = arbfile.unwrap();
    let arbs = arbfile.as_array().expect("no arbs in arbfile");

    util::assert_err("!arbs.is_empty()", arbs.is_empty(), false)?;

    let profit: u64 = arbs
        .iter()
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
        .iter()
        .filter_map(|arb_str| arb_str.as_str())
        .filter(|arb_str| arb_str.contains("auction"))
        .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
        .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
        .sum();
    let osmo_profit: u64 = arbs
        .iter()
        .filter_map(|arb_str| arb_str.as_str())
        .filter(|arb_str| arb_str.contains("osmosis"))
        .filter_map(|arb_str| serde_json::from_str::<Value>(arb_str).ok())
        .filter_map(|arb| arb.get("realized_profit")?.as_number()?.as_u64())
        .sum();

    println!("ARB BOT PROFIT: {profit}");
    println!("AUCTION BOT PROFIT: {auction_profit}");
    println!("OSMOSIS BOT PROFIT: {osmo_profit}");

    util::assert_err("osmo_profit == 0", osmo_profit, 0)?;

    Ok(())
}
