use super::{ARTIFACTS_PATH, OSMOSIS_CHAIN, OSMOSIS_POOLFILE_PATH};
use local_ictest_e2e::{
    utils::{file_system, test_context::TestContext},
    ACC_0_KEY, GAIA_CHAIN, NEUTRON_CHAIN, WASM_EXTENSION,
};
use localic_std::{errors::LocalError, modules::cosmwasm::CosmWasm};
use std::{
    ffi::OsStr,
    fs::{self},
    io::Error as IoError,
    path::PathBuf,
    thread,
    time::Duration,
};
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
    #[error("failed to construct JSON")]
    SerializationError,
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
            println!("preparing to upload contract {:?}", maybe_abs_path);

            let path = maybe_abs_path?;
            let neutron_local_chain = test_ctx.get_mut_chain(NEUTRON_CHAIN);

            let mut cw = CosmWasm::new(&neutron_local_chain.rb);

            println!("uploading contract {:?}", path);
            let code_id = cw.store(ACC_0_KEY, &path)?;

            let id = path
                .file_stem()
                .ok_or(SetupError::PathFmt)?
                .to_str()
                .ok_or(SetupError::PathFmt)?;
            neutron_local_chain
                .contract_codes
                .insert(id.to_string(), code_id);

            thread::sleep(Duration::from_secs(5));

            Ok(())
        })
}

/// Instantiates the auction manager.
pub fn create_auction_manager(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

    let code_id =
        neutron
            .contract_codes
            .get("auctions_manager")
            .ok_or(SetupError::MissingContract(String::from(
                "auctions_manager",
            )))?;
    let auction_code_id = neutron
        .contract_codes
        .get("auction")
        .ok_or(SetupError::MissingContract(String::from("auction")))?;

    let mut contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/auctions_manager.wasm"
        ))),
        Some(*code_id),
        None,
    );

    let contract = contract_a.instantiate(
        ACC_0_KEY,
        serde_json::json!({
            "auction_code_id": auction_code_id,
            "min_auction_amount": [],
            "server_addr": acc_0_addr,
        })
        .to_string()
        .as_str(),
        "auction_manager",
        None,
        "",
    )?;

    neutron
        .contract_addrs
        .insert("auctions_manager".to_owned(), contract.address);

    Ok(())
}

/// Instantiates an individual auction.
pub fn create_auction(
    test_ctx: &mut TestContext,
    denom_a: impl AsRef<str>,
    denom_b: impl AsRef<str>,
) -> Result<(), SetupError> {
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

    let contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/auctions_manager.wasm"
        ))),
        Some(*code_id),
        Some(addr.to_owned()),
    );

    let _ = contract_a.execute(
        ACC_0_KEY,
        serde_json::json!(
        {
            "admin": {
                "new_auction": {
                    "msg": {
                        "pair": [denom_a.as_ref(), denom_b.as_ref()],
                        "auction_strategy": {
                            "start_price_perc": 5000,
                            "end_price_perc": 5000
                        },
                        "chain_halt_config": {
                            "cap": "14400",
                            "block_avg": "3"
                        },
                        "price_freshness_strategy": {
                            "limit": "3",
                            "multipliers": [["2", "2"], ["1", "1.5"]]
                        }
                    }
                }
        }})
        .to_string()
        .as_str(),
        "",
    )?;

    Ok(())
}

/// Instantiates all testing auctions.
pub fn create_auctions(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let atom_denom = test_ctx
        .get_ibc_denoms()
        .src(GAIA_CHAIN)
        .dest(NEUTRON_CHAIN)
        .get();

    create_auction(test_ctx, "untrn", &atom_denom)?;
    create_auction(test_ctx, &atom_denom, "untrn")?;

    Ok(())
}

/// Instantiates the token registry.
pub fn create_token_registry(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

    let code_id = neutron
        .contract_codes
        .get("astroport_native_coin_registry")
        .ok_or(SetupError::MissingContract(String::from(
            "astroport_native_coin_registry",
        )))?;

    let mut contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/astroport_native_coin_registry.wasm"
        ))),
        Some(*code_id),
        None,
    );

    let contract = contract_a.instantiate(
        ACC_0_KEY,
        serde_json::json!({
            "owner": acc_0_addr,
        })
        .to_string()
        .as_str(),
        "astroport_native_coin_registry",
        None,
        "",
    )?;

    neutron.contract_addrs.insert(
        "astroport_native_coin_registry".to_owned(),
        contract.address,
    );

    Ok(())
}

/// Instantiates the astroport factory.
pub fn create_factory(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

    let code_id =
        neutron
            .contract_codes
            .get("astroport_factory")
            .ok_or(SetupError::MissingContract(String::from(
                "astroport_factory",
            )))?;
    let pair_code_id = neutron
        .contract_codes
        .get("astroport_pair")
        .ok_or(SetupError::MissingContract(String::from("astroport_pair")))?;
    let native_registry_addr = neutron
        .contract_addrs
        .get("astroport_native_coin_registry")
        .ok_or(SetupError::MissingContract(String::from(
            "astroport_native_coin_registry",
        )))?;

    let mut contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/astroport_factory.wasm"
        ))),
        Some(*code_id),
        None,
    );

    let contract = contract_a.instantiate(
        ACC_0_KEY,
        serde_json::json!({
            "pair_configs": [
                {
                    "code_id": pair_code_id,
                    "pair_type": {
                         "xyk": {}
                    },
                    "total_fee_bps": 100,
                    "maker_fee_bps": 10,
                    "is_disabled": false,
                    "is_generator_disabled": false
                }
            ],
            "token_code_id": 0,
            "owner": acc_0_addr,
            "whitelist_code_id": 0,
            "coin_registry_address": native_registry_addr
        })
        .to_string()
        .as_str(),
        "astroport_factory",
        None,
        "",
    )?;

    neutron
        .contract_addrs
        .insert("astroport_factory".to_owned(), contract.address);

    Ok(())
}

pub fn create_pool(
    test_ctx: &mut TestContext,
    denom_a: impl AsRef<str>,
    denom_b: impl AsRef<str>,
) -> Result<(), SetupError> {
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

    let contract_a = CosmWasm::new_from_existing(
        &neutron.rb,
        Some(PathBuf::from(format!(
            "{ARTIFACTS_PATH}/astroport_factory.wasm"
        ))),
        Some(*code_id),
        Some(contract_addr.clone()),
    );

    let denom_a_str = denom_a.as_ref();
    let denom_b_str = denom_b.as_ref();

    let _ = contract_a.execute(
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
        }]}})
        .to_string()
        .as_str(),
        "",
    )?;

    Ok(())
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

/// Creates an osmosis pool with the given denoms.
pub fn create_osmo_pool(
    test_ctx: &mut TestContext,
    denom_a: impl AsRef<str>,
    denom_b: impl AsRef<str>,
) -> Result<(), SetupError> {
    let osmosis = test_ctx.get_chain(OSMOSIS_CHAIN);

    let denom_a_str = denom_a.as_ref();
    let denom_b_str = denom_b.as_ref();

    // Osmosisd requires a JSON file to specify the
    // configuration of the pool being created
    let poolfile_str = format!(
        r#"{{\"weights\": \"1{denom_a_str},1{denom_b_str}\",\"initial-deposit\": \"1{denom_a_str},1{denom_b_str}\",\"swap-fee\": \"0.00\",\"exit-fee\": \"0.00\",\"future-governor\": \"168h\"}}"#
    );

    // Copy the poolfile to the container
    let _ = osmosis.rb.exec(
        format!("/bin/sh -c 'echo \"{poolfile_str}\" > {OSMOSIS_POOLFILE_PATH}'").as_str(),
        true,
    );

    // Create pool
    let _ = osmosis.rb.tx(
        format!("poolmanager create-pool  --pool-file {OSMOSIS_POOLFILE_PATH} --from {ACC_0_KEY}")
            .as_str(),
        true,
    )?;

    Ok(())
}

/// Creates all osmosis pools.
pub fn create_osmo_pools(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let ntrn_denom = test_ctx
        .get_ibc_denoms()
        .src(NEUTRON_CHAIN)
        .dest(OSMOSIS_CHAIN)
        .get();

    create_osmo_pool(test_ctx, "uosmo", ntrn_denom)?;

    Ok(())
}
