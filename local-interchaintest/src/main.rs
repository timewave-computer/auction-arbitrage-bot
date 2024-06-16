use local_ictest_e2e::{
    utils::{
        file_system,
        test_context::{
            find_pairwise_ccv_channel_ids, find_pairwise_transfer_channel_ids, TestContext,
        },
        types::ChainsVec,
    },
    GAIA_CHAIN, NEUTRON_CHAIN,
};
use local_interchaintest::{error::Error, setup, util, API_URL, CHAIN_CONFIG_PATH, OSMOSIS_CHAIN};
use localic_std::polling;
use reqwest::blocking::Client;
use std::collections::HashMap;

fn main() {
    let client = Client::new();
    polling::poll_for_start(&client, API_URL, 300).expect("failed to poll client");

    let configured_chains =
        file_system::read_json_file(CHAIN_CONFIG_PATH).expect("failed to read chain config file");
    let mut test_ctx = setup_context(configured_chains).expect("failed to construct test context");

    // Deploy all required neutron contracts
    setup::deploy_neutron_contracts(&mut test_ctx).expect("failed to deploy contracts");
}

fn setup_context(configured_chains: ChainsVec) -> Result<TestContext, Error> {
    let mut chains = HashMap::new();

    configured_chains
        .chains
        .into_iter()
        .map(util::local_chain_from_config_chain)
        .try_for_each(|maybe_local_chain| {
            let local_chain = maybe_local_chain?;

            chains.insert(
                util::chain_name_from_chain_id(&local_chain.rb.chain_id)?.to_owned(),
                local_chain,
            );

            Ok::<(), Error>(())
        })?;

    let ntrn_channels = chains
        .get(NEUTRON_CHAIN)
        .ok_or(Error::UnrecognizedChain(NEUTRON_CHAIN.to_owned()))?
        .channels
        .clone();
    let gaia_channels = chains
        .get(GAIA_CHAIN)
        .ok_or(Error::UnrecognizedChain(GAIA_CHAIN.to_owned()))?
        .channels
        .clone();
    let osmo_channels = chains
        .get(OSMOSIS_CHAIN)
        .ok_or(Error::UnrecognizedChain(OSMOSIS_CHAIN.to_owned()))?
        .channels
        .clone();

    let mut connection_ids = HashMap::new();

    let (ntrn_to_gaia_consumer_channel, gaia_to_ntrn_provider_channel) =
        find_pairwise_ccv_channel_ids(&gaia_channels, &ntrn_channels)
            .map_err(|_| Error::ChannelLookup)?;

    connection_ids.insert(
        (NEUTRON_CHAIN.to_string(), GAIA_CHAIN.to_string()),
        ntrn_to_gaia_consumer_channel.connection_id,
    );
    connection_ids.insert(
        (GAIA_CHAIN.to_string(), NEUTRON_CHAIN.to_string()),
        gaia_to_ntrn_provider_channel.connection_id,
    );

    let (ntrn_to_osmosis_transfer_channel, osmosis_to_ntrn_transfer_channel) =
        find_pairwise_transfer_channel_ids(&ntrn_channels, &osmo_channels)
            .map_err(|_| Error::ChannelLookup)?;

    connection_ids.insert(
        (NEUTRON_CHAIN.to_string(), OSMOSIS_CHAIN.to_string()),
        ntrn_to_osmosis_transfer_channel.connection_id,
    );
    connection_ids.insert(
        (OSMOSIS_CHAIN.to_string(), NEUTRON_CHAIN.to_string()),
        osmosis_to_ntrn_transfer_channel.connection_id,
    );

    let mut transfer_channel_ids = HashMap::new();
    transfer_channel_ids.insert(
        (NEUTRON_CHAIN.to_string(), OSMOSIS_CHAIN.to_string()),
        ntrn_to_osmosis_transfer_channel.channel_id.to_string(),
    );
    transfer_channel_ids.insert(
        (OSMOSIS_CHAIN.to_string(), NEUTRON_CHAIN.to_string()),
        osmosis_to_ntrn_transfer_channel.channel_id.to_string(),
    );

    let mut ccv_channel_ids = HashMap::new();
    ccv_channel_ids.insert(
        (GAIA_CHAIN.to_string(), NEUTRON_CHAIN.to_string()),
        gaia_to_ntrn_provider_channel.channel_id,
    );
    ccv_channel_ids.insert(
        (NEUTRON_CHAIN.to_string(), GAIA_CHAIN.to_string()),
        ntrn_to_gaia_consumer_channel.channel_id,
    );

    let mut ibc_denoms = HashMap::new();

    Ok(TestContext {
        chains,
        transfer_channel_ids,
        ccv_channel_ids,
        connection_ids,
        ibc_denoms,
    })
}
