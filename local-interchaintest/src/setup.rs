use super::ARTIFACTS_PATH;
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
pub fn create_factory(test_ctx: &mut TestContext) -> Result<String, SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr;
    let code_id =
        neutron
            .contract_codes
            .get("astroport_factory")
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;

    let mut contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        format!("{ARTIFACTS_PATH}/astroport_factory.wasm"),
        code_id,
        None,
    );
    let ca = contract_a.instantiate(
        ACC_0_KEY,
        serde_json::json!({
            "pair_configs": [],
            "token_code_id": 0,
            "owner": {acc_0_addr},
            "whitelist_code_id": 0}),
        "astroport_factory",
        None,
        "",
    );

    Ok(ca)
}

pub fn create_pool(
    test_ctx: &mut TestContext,
    denom_a: impl AsRef<str>,
    denom_b: impl AsRef<str>,
) -> Result<String, SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let code_id =
        neutron
            .contract_codes
            .get("astroport_factory")
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;

    let mut contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        format!("{ARTIFACTS_PATH}/astroport_factory.wasm"),
        code_id,
        None,
    );

    let factory_addr_str =
        contract_a
            .contract_addr
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;
    let denom_a_str = denom_a.as_ref();
    let denom_b_str = denom_b.as_ref();

    let res = contract_a.execute(
        ACC_0_KEY,
        serde_json::json!({
        "create_pair": {
        "pair_type": {
            "xyk": {}
        },
        "asset_infos": [
        {
            "native_token": {
                "denom": denom_a_str
            }
        },
        {
            "native_token": {
                "denom": denom_b_str
            }
        }]}}),
        "",
    )?;

    println!("{:?}", res);

    Ok(String::new())
}

/// Creates pools with random prices.
/// Includes at least one pool that may be arbitraged.
pub fn create_pools(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let atom_denom = test_ctx
        .get_ibc_denoms()
        .src(GAIA_CHAIN)
        .dest(NEUTRON_CHAIN)
        .get();
    create_pool(test_ctx, "untrn", atom_denom)?;

    Ok(())
}
