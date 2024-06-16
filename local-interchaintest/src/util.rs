use super::{error::Error, API_URL, OSMOSIS_CHAIN, OSMOSIS_CHAIN_ID};
use local_ictest_e2e::{
    utils::{test_context::LocalChain, types::ConfigChain},
    GAIA_CHAIN, GAIA_CHAIN_ID, NEUTRON_CHAIN, NEUTRON_CHAIN_ID, STRIDE_CHAIN, STRIDE_CHAIN_ID,
};
use localic_std::{relayer::Relayer, transactions::ChainRequestBuilder};

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
