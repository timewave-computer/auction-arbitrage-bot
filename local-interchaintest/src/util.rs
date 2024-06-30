use std::{error::Error, fs::OpenOptions, io::Write};

/// Creates an arb bot contract address file.
pub(crate) fn create_deployment_file(
    astroport_factory_address: &str,
    auctions_manager_address: &str,
) -> Result<(), Box<dyn Error>> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open("../deployments_file.json")?;

    f.write_all(
        serde_json::json!({
            "pools": {
                "astroport": {
                    "localneutron-1": {
                        "chain_name": "neutron",
                        "chain_prefix": "neutron",
                        "chain_fee_denom": "untrn",
                        "directory": {
                            "address": astroport_factory_address,
                            "src": "contracts/astroport_factory.wasm",
                        },
                        "pair": {
                            "src": "contracts/astroport_pair.wasm"
                        }
                    }
                },
                "osmosis": {
                    "localosmosis-1": {
                        "chain_name": "osmosis",
                        "chain_prefix": "osmo",
                        "chain_fee_denom": "uosmo"
                    }
                }
            },
            "auctions": {
                "localneutron-1": {
                    "chain_name": "neutron",
                    "chain_prefix": "neutron",
                    "chain_fee_denom": "untrn",
                    "auctions_manager": {
                        "address": auctions_manager_address,
                        "src": "contracts/auctions_manager.wasm",
                    },
                    "auction": {
                        "src": "contracts/auction.wasm"
                    }
                }
            }
        })
        .to_string()
        .as_bytes(),
    )?;

    Ok(())
}

pub(crate) fn create_arbs_file() -> Result<(), Box<dyn Error>> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open("../arbs.json")?;

    f.write_all(serde_json::json!([]).to_string().as_bytes())?;

    Ok(())
}

pub(crate) fn create_netconfig() -> Result<(), Box<dyn Error>> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open("../net_config.json")?;

    f.write_all(
        serde_json::json!({
            "localneutron-1": {
                "http": ["http://localhost:1317"],
                "grpc": ["grpc+http://localhost:9090"],
            },
            "localosmosis-1": {
                "http": ["http://localhost:1319"],
                "grpc": ["grpc+http://localhost:9092"]
            }
        })
        .to_string()
        .as_bytes(),
    )?;

    Ok(())
}
