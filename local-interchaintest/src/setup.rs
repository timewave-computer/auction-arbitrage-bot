use super::{ARTIFACTS_PATH, OSMOSIS_CHAIN};
use local_ictest_e2e::{
    utils::{file_system, test_context::TestContext},
    ACC_0_KEY, GAIA_CHAIN, NEUTRON_CHAIN, WASM_EXTENSION,
};
use localic_std::{errors::LocalError, modules::cosmwasm::CosmWasm};
use std::{ffi::OsStr, fs, io::Error as IoError};
use thiserror::Error;

/// Errors that may have occurred while deploying contracts.
#[derive(Error, Debug)]
pub enum SetupError {
    #[error("failed to format file path")]
    PathFmt,
    #[error("local interchain failure")]
    LocalInterchain(#[from] LocalError),
    #[error("IO failure")]
    Io(#[from] IoError),
    #[error("the contract `{0}` is missing")]
    MissingContract(String),
}

/// Deploys all neutron contracts to the test context.
pub fn deploy_neutron_contracts(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    file_system::read_artifacts(ARTIFACTS_PATH)?
        .into_iter()
        .filter(|dir_ent| {
            dir_ent.path().extension().and_then(OsStr::to_str) == Some(WASM_EXTENSION)
        })
        .map(|ent| ent.path())
        .map(fs::canonicalize)
        .try_for_each(|maybe_abs_path| {
            let path = maybe_abs_path?;
            let neutron_local_chain = test_ctx.get_mut_chain(NEUTRON_CHAIN);

            let mut cw = CosmWasm::new(&neutron_local_chain.rb);
            let code_id = cw.store(ACC_0_KEY, &path)?;

            let id = path
                .file_stem()
                .ok_or(SetupError::PathFmt)?
                .to_str()
                .ok_or(SetupError::PathFmt)?;
            neutron_local_chain
                .contract_codes
                .insert(id.to_string(), code_id);

            Ok(())
        })
}

/// Instantiates the astroport factory.
pub fn create_token_factory(test_ctx: &mut TestContext) -> Result<String, SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let code_id =
        neutron
            .contract_codes
            .get("astroport_factory")
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;
    let instantiate_acc = test_ctx.get_admin_addr().src(NEUTRON_CHAIN).get();
    let instantiate_args = format!(
        r#"{{ "pair_configs": [], "token_code_id": 0, "owner": "{instantiate_acc}", "whitelist_code_id": 0, "coin_registry_address": ""}}"#
    );

    println!("{}", instantiate_args);
    println!(
        "neutrond tx wasm instantiate {code_id} '{instantiate_args}' --from acc0 --output=json"
    );

    let resp = neutron.rb.exec(
        &format!(
            "neutrond tx wasm instantiate {code_id} '{instantiate_args}' --from acc0 --output=json"
        ),
        true,
    );

    println!("{:?}", resp);

    Ok(String::new())
}

pub fn create_pool(
    test_ctx: &mut TestContext,
    factory_addr: impl AsRef<str>,
    denom_a: impl AsRef<str>,
    denom_b: impl AsRef<str>,
) -> Result<String, SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let factory_addr_str = factory_addr.as_ref();
    let denom_a_str = denom_a.as_ref();
    let denom_b_str = denom_b.as_ref();

    let send_args =
        format!("{{ 'create_pair': {{'asset_infos': [{{'native_token': {{'denom': {denom_a_str}}}}}, {{'native_token': {{'denom': {denom_b_str}}}}}]}}}}");

    let _ = neutron.rb.exec(
        &format!("neutrond tx wasm execute {factory_addr_str} {send_args}"),
        true,
    );

    Ok(String::new())
}

/// Creates pools with random prices.
/// Includes at least one pool that may be arbitraged.
pub fn create_pools(
    test_ctx: &mut TestContext,
    factory_addr: impl AsRef<str>,
) -> Result<(), SetupError> {
    let atom_denom = test_ctx
        .get_ibc_denoms()
        .src(GAIA_CHAIN)
        .dest(NEUTRON_CHAIN)
        .get();
    create_pool(test_ctx, factory_addr, "untrn", atom_denom)?;

    Ok(())
}
