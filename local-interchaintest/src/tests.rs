use super::util;
use serde_json::Value;
use std::error::Error;

pub fn test_profitable_arb(arbfile: Value) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let arbs = arbfile.as_array().expect("no arbs in arbfile");

    util::assert_err("!arbs.is_empty()", !arbs.is_empty())?;

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

    util::assert_err("profit == 466496", profit == 466496)?;
    util::assert_err("auction_profit == 466496", auction_profit == 466496)?;

    Ok(())
}

pub fn test_unprofitable_arb(arbfile: Value) -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let arbs = arbfile.as_array().expect("no arbs in arbfile");

    util::assert_err("!arbs.is_empty()", !arbs.is_empty())?;

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

    util::assert_err("profit == 0", profit == 0)?;
    util::assert_err("auction_profit == 0", auction_profit == 0)?;

    Ok(())
}
