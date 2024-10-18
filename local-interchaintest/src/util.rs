use serde::Serialize;
use std::{collections::HashMap, error::Error, fmt::Debug, fs::OpenOptions, io::Write};

#[derive(Clone, Serialize)]
pub(crate) struct ChainInfo {
    pub(crate) chain_name: String,
    pub(crate) chain_id: String,
    pub(crate) pfm_enabled: bool,
    pub(crate) supports_memo: bool,
    pub(crate) bech32_prefix: String,
    pub(crate) fee_asset: String,
    pub(crate) chain_type: String,
    pub(crate) pretty_name: String,
}

#[derive(Serialize)]
pub(crate) struct DenomChainInfo {
    pub(crate) denom: String,
    pub(crate) src_chain_id: String,
    pub(crate) dest_chain_id: String,
}

#[derive(Serialize, Clone)]
pub(crate) struct BidirectionalDenomRouteLeg {
    pub(crate) src_to_dest: DenomRouteLeg,
    pub(crate) dest_to_src: DenomRouteLeg,
}

#[derive(Serialize, Clone)]
pub(crate) struct DenomRouteLeg {
    pub(crate) src_chain: String,
    pub(crate) dest_chain: String,
    pub(crate) src_denom: String,
    pub(crate) dest_denom: String,
    pub(crate) from_chain: ChainInfo,
    pub(crate) to_chain: ChainInfo,
    pub(crate) port: String,
    pub(crate) channel: String,
}

impl From<DenomRouteLeg> for DenomChainInfo {
    fn from(v: DenomRouteLeg) -> Self {
        Self {
            denom: v.dest_denom,
            src_chain_id: v.src_chain,
            dest_chain_id: v.dest_chain,
        }
    }
}

#[derive(Serialize, Default)]
pub(crate) struct DenomFile {
    pub(crate) denom_map: HashMap<String, Vec<DenomChainInfo>>,
    pub(crate) denom_routes: HashMap<String, HashMap<String, Vec<DenomRouteLeg>>>,
    pub(crate) chain_info: HashMap<String, ChainInfo>,
}

impl DenomFile {
    /// Registers a denom in the denom file by adding its
    /// matching denoms on all respective chains,
    /// and the routes needed to get here.
    pub fn push_denom(&mut self, v: BidirectionalDenomRouteLeg) -> &mut Self {
        self.denom_map
            .entry(v.src_to_dest.src_denom.clone())
            .or_default()
            .push(v.src_to_dest.clone().into());
        self.denom_routes
            .entry(v.src_to_dest.src_denom.clone())
            .or_default()
            .entry(v.src_to_dest.dest_denom.clone())
            .or_default()
            .push(v.src_to_dest);

        self.denom_map
            .entry(v.dest_to_src.src_denom.clone())
            .or_default()
            .push(v.dest_to_src.clone().into());
        self.denom_routes
            .entry(v.dest_to_src.src_denom.clone())
            .or_default()
            .entry(v.dest_to_src.clone().dest_denom)
            .or_default()
            .push(v.dest_to_src);

        self
    }
}

/// Creates an error representing a failed assertion.
pub fn assert_err<T: Debug + PartialEq>(
    message: impl AsRef<str>,
    lhs: T,
    rhs: T,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    if lhs != rhs {
        let message_str = message.as_ref();

        return Err(format!("Assertion failed ({message_str}): {:?} == {:?}", lhs, rhs).into());
    }

    Ok(())
}

/// Creates an arb bot contract address file.
pub(crate) fn create_deployment_file(
    astroport_factory_address: &str,
    auctions_manager_address: &str,
    neutron_to_osmosis_channel_id: &str,
    osmosis_to_neutron_channel_id: &str,
) -> Result<(), Box<dyn Error + Send + Sync>> {
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
                        "chain_transfer_channel_ids": {
                            "localosmosis-1": neutron_to_osmosis_channel_id,
                        },
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
                        "chain_fee_denom": "uosmo",
                        "chain_transfer_channel_ids": {
                            "localneutron-1": osmosis_to_neutron_channel_id,
                        },
                    }
                }
            },
            "auctions": {
                "localneutron-1": {
                    "chain_name": "neutron",
                    "chain_prefix": "neutron",
                    "chain_fee_denom": "untrn",
                    "chain_transfer_channel_ids": {
                        "localosmosis-1": neutron_to_osmosis_channel_id,
                    },
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

pub(crate) fn create_arbs_file() -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open("../arbs.db")?;

    f.write_all(&[])?;

    Ok(())
}

pub(crate) fn create_netconfig() -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open("../net_config_test.json")?;

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

pub(crate) fn create_denom_file(file: &DenomFile) -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open("../denoms.json")?;

    f.write_all(serde_json::to_string(file)?.as_bytes())?;

    Ok(())
}
