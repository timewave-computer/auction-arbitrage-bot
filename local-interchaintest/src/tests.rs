use super::{util, ARBFILE_PATH};
use std::{error::Error, process::Command};

pub fn test_transfer_osmosis() -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    Command::new("python")
        .current_dir("tests")
        .arg("transfer_osmosis.py")
        .output()?;

    Ok(())
}

pub fn test_transfer_neutron() -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    Command::new("python")
        .current_dir("tests")
        .arg("transfer_neutron.py")
        .output()?;

    Ok(())
}

pub fn test_profitable_arb() -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let conn = sqlite::open(ARBFILE_PATH).expect("failed to open db");

    let profit = {
        let query = "SELECT SUM(o.realized_profit) AS total_profit FROM orders o";

        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    let auction_profit = {
        let query = "SELECT SUM(order_profit) AS total_profit FROM (SELECT MAX(o.realized_profit) AS order_profit FROM orders o INNER JOIN legs l ON o.uid = l.order_uid GROUP BY o.uid)";
        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    println!("ARB BOT PROFIT: {profit}");
    println!("AUCTION BOT PROFIT: {auction_profit}");

    util::assert_err("profit > 0", profit > 0, true)?;
    util::assert_err("auction_profit > 0", auction_profit > 0, true)?;

    Ok(())
}

pub fn test_unprofitable_arb() -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let conn = sqlite::open(ARBFILE_PATH).expect("failed to open db");

    let profit = {
        let query = "SELECT SUM(o.realized_profit) AS total_profit FROM orders o";

        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    let auction_profit = {
        let query = "SELECT SUM(order_profit) AS total_profit FROM (SELECT MAX(o.realized_profit) AS order_profit FROM orders o INNER JOIN legs l ON o.uid = l.order_uid GROUP BY o.uid)";
        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    println!("ARB BOT PROFIT: {profit}");
    println!("AUCTION BOT PROFIT: {auction_profit}");

    util::assert_err("profit == 0", profit == 0, true)?;
    util::assert_err("auction_profit == 0", auction_profit == 0, true)?;

    Ok(())
}

pub fn test_osmo_arb() -> Result<(), Box<dyn Error + Send + Sync + 'static>> {
    let conn = sqlite::open(ARBFILE_PATH).expect("failed to open db");

    let profit = {
        let query = "SELECT SUM(o.realized_profit) AS total_profit FROM orders o";

        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    let auction_profit = {
        let query = "SELECT SUM(order_profit) AS total_profit FROM (SELECT MAX(o.realized_profit) AS order_profit FROM orders o WHERE l.kind = 'auction' INNER JOIN legs l ON o.uid == l.order_uid GROUP BY o.uid)";
        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    let osmo_profit = {
        let query = "SELECT SUM(order_profit) AS total_profit FROM (SELECT MAX(o.realized_profit) AS order_profit FROM orders o WHERE l.kind = 'osmosis' INNER JOIN legs l ON o.uid == l.order_uid GROUP BY o.uid)";
        let mut statement = conn.prepare(query).unwrap();

        statement
            .next()
            .ok()
            .and_then(|_| statement.read::<i64, _>("total_profit").ok())
            .unwrap_or_default()
    };

    println!("ARB BOT PROFIT: {profit}");
    println!("AUCTION BOT PROFIT: {auction_profit}");
    println!("OSMOSIS BOT PROFIT: {osmo_profit}");

    util::assert_err("profit > 0", profit > 0, true)?;
    util::assert_err("osmo_profit > 0", osmo_profit > 0, true)?;
    util::assert_err("auction_profit > 0", auction_profit > 0, true)?;

    Ok(())
}
