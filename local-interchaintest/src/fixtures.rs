use super::{error::SetupError, ARTIFACTS_PATH};
use local_ictest_e2e::{utils::test_context::TestContext, NEUTRON_CHAIN};
use localic_std::modules::cosmwasm::CosmWasm;
use std::path::PathBuf;

/// Get a new CosmWasm instance for a contract identified by a name.
pub fn use_contract(
    test_ctx: &mut TestContext,
    name: impl AsRef<str>,
) -> Result<CosmWasm, SetupError> {
    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    let code_id = neutron
        .contract_codes
        .get(name.as_ref())
        .ok_or(SetupError::MissingContract(String::from(name.as_ref())))?;

    let name_str = name.as_ref();

    Ok(CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!("{ARTIFACTS_PATH}/{name_str}.wasm"))),
        Some(*code_id),
        None,
    ))
}

/// Gets the deployed astroport factory for this run.
pub fn use_astroport_factory(test_ctx: &mut TestContext) -> Result<CosmWasm, SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let code_id =
        neutron
            .contract_codes
            .get("astroport_factory")
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;
    let contract_addr =
        neutron
            .contract_addrs
            .get("astroport_factory")
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;

    Ok(CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/astroport_factory.wasm"
        ))),
        Some(*code_id),
        Some(contract_addr.clone()),
    ))
}

/// Gets the deployed auctions manager for this run.
pub fn use_auctions_manager(test_ctx: &mut TestContext) -> Result<CosmWasm, SetupError> {
    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    let code_id =
        neutron
            .contract_codes
            .get("auctions_manager")
            .ok_or(SetupError::MissingContract(String::from(
                "auctions_manager",
            )))?;
    let addr =
        neutron
            .contract_addrs
            .get("auctions_manager")
            .ok_or(SetupError::MissingContract(String::from(
                "auctions_manager",
            )))?;

    Ok(CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/auctions_manager.wasm"
        ))),
        Some(*code_id),
        Some(addr.to_owned()),
    ))
}
