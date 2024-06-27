use std::{error::Error, fs::OpenOptions, io::Write};

/// Creates an arb bot contract address file.
pub(crate) fn create_deployment_file(
    astroport_factory_address: &str,
    auctions_manager_address: &str,
) -> Result<(), Box<dyn Error>> {
    let mut f = OpenOptions::new()
        .create(true)
        .write(true)
        .open("/tmp/deployments_file.json")?;

    f.write_all(
        serde_json::json!({
            "pools": {
                "astroport": {
                    "neutron": {
                        "directory": {
                            "address": astroport_factory_address,
                            "src": "contracts/astroport_factory.wasm",
                        },
                        "pair": {
                            "src": "contracts/astroport_pair.wasm"
                        }
                    }
                }
            },
            "auctions": {
                "neutron": {
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
