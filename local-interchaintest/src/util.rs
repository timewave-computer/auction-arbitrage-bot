use super::{error::Error, API_URL, OSMOSIS_CHAIN, OSMOSIS_CHAIN_ID};
use local_ictest_e2e::{
    utils::{test_context::LocalChain, types::ConfigChain},
    GAIA_CHAIN, GAIA_CHAIN_ID, NEUTRON_CHAIN, NEUTRON_CHAIN_ID, STRIDE_CHAIN, STRIDE_CHAIN_ID,
};
use localic_std::{relayer::Relayer, transactions::ChainRequestBuilder};
use reqwest::{
    blocking::Client,
    header::{ACCEPT, CONTENT_TYPE},
};
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;

pub struct SharedAsset {
    pub origin_chain_id: String,
    pub origin_denom: String,
    pub dest_chain_id: String,
    pub dest_denom: String,
    pub symbol: String,
    pub name: String,
    pub decimals: i32,
}

pub fn denoms_shared_between_chains(
    chain_a: impl AsRef<str>,
    chain_b: impl AsRef<str>,
) -> Result<Vec<SharedAsset>, Error> {
    let res = Client::new();

    let data = {
        let mut h = HashMap::new();
        h.insert("include_no_metadata_assets", Value::Bool(false));
        h.insert("allow_multi_tx", Value::Bool(false));
        h.insert(
            "source_chain_id",
            Value::String(chain_a.as_ref().to_owned()),
        );
        h.insert("dest_chain_id", Value::String(chain_b.as_ref().to_owned()));
    };

    #[derive(Deserialize)]
    struct RawAsset {
        denom: String,
        chain_id: String,
        origin_denom: String,
        origin_chain_id: String,
        symbol: String,
        name: String,
        decimals: i32,
    }

    /// An asset_on_source or asset_on_dest
    #[derive(Deserialize)]
    struct RawSharedAsset {
        asset_on_source: RawAsset,
        asset_on_dest: RawAsset,
    }

    #[derive(Deserialize)]
    struct SharedAssetResp {
        assets_between_chains: Vec<RawSharedAsset>,
    }

    let resp: SharedAssetResp = res
        .post("https://api.skip.money/v2/fungible/assets_between_chains")
        .header(ACCEPT, "application/json")
        .header(CONTENT_TYPE, "application/json")
        .json(&data)
        .send()?
        .json()?;

    Ok(resp
        .assets_between_chains
        .into_iter()
        .map(|shared_asset| {
            let src = shared_asset.asset_on_source;
            let dest = shared_asset.asset_on_dest;

            SharedAsset {
                origin_denom: src.origin_denom.clone(),
                origin_chain_id: src.origin_chain_id.clone(),
                dest_denom: if src.chain_id != src.origin_chain_id {
                    src.origin_denom
                } else {
                    dest.denom
                },
                dest_chain_id: if src.chain_id != src.origin_chain_id {
                    src.origin_chain_id
                } else {
                    dest.chain_id
                },
                symbol: src.symbol,
                name: src.name,
                decimals: src.decimals,
            }
        })
        .collect::<Vec<_>>())
}

/// Gets the name of the chain from the chain id.
pub fn chain_name_from_chain_id(id: impl AsRef<str>) -> Result<&'static str, Error> {
    Ok(match id.as_ref() {
        NEUTRON_CHAIN_ID => NEUTRON_CHAIN,
        GAIA_CHAIN_ID => GAIA_CHAIN,
        STRIDE_CHAIN_ID => STRIDE_CHAIN,
        OSMOSIS_CHAIN_ID => OSMOSIS_CHAIN,
        x => {
            return Err(Error::UnrecognizedChain(x.to_owned()));
        }
    })
}

pub fn local_chain_from_config_chain(chain: ConfigChain) -> Result<LocalChain, Error> {
    let rb =
        ChainRequestBuilder::new(API_URL.to_string(), chain.chain_id.clone(), chain.debugging)?;

    let relayer: Relayer = Relayer::new(&rb);
    let channels = relayer.get_channels(&rb.chain_id).unwrap();

    let (src_addr, denom) = match rb.chain_id.as_str() {
        NEUTRON_CHAIN_ID => ("neutron1hj5fveer5cjtn4wd6wstzugjfdxzl0xpznmsky", "untrn"),
        GAIA_CHAIN_ID => ("cosmos1hj5fveer5cjtn4wd6wstzugjfdxzl0xpxvjjvr", "uatom"),
        STRIDE_CHAIN_ID => ("stride1u20df3trc2c2zdhm8qvh2hdjx9ewh00sv6eyy8", "ustrd"),
        OSMOSIS_CHAIN_ID => ("osmo1hj5fveer5cjtn4wd6wstzugjfdxzl0xpwhpz63", "uosmo"),
        x => {
            return Err(Error::UnrecognizedChain(x.to_owned()));
        }
    };

    Ok(LocalChain::new(
        rb,
        src_addr.to_string(),
        denom.to_string(),
        channels,
    ))
}
