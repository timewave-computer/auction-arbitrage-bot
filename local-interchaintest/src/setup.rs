use super::ARTIFACTS_PATH;
use local_ictest_e2e::{
    utils::{file_system, test_context::TestContext},
    ACC_0_KEY, NEUTRON_CHAIN, WASM_EXTENSION,
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
