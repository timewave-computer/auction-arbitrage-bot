use super::{
    error::SetupError, fixtures, ARTIFACTS_PATH, NEUTRON_TOKENFACTORY_TOKENS, OSMOSIS_CHAIN,
    OSMOSIS_DOCKER_CONTAINER_ID, OSMOSIS_POOLFILE_PATH, REMOTE_OSMOSIS_POOLFILE_PATH,
};
use local_ictest_e2e::{
    utils::{file_system, test_context::TestContext},
    ACC_0_KEY, NEUTRON_CHAIN, WASM_EXTENSION,
};
use localic_std::modules::cosmwasm::CosmWasm;
use std::{
    ffi::OsStr,
    fs::{self, OpenOptions},
    io::Write,
    process::Command,
    thread,
    time::Duration,
};

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

            thread::sleep(Duration::from_secs(5));

            Ok(())
        })
}

/// Creates 4 token on Neutron via the token factory module.
pub fn create_tokens(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    NEUTRON_TOKENFACTORY_TOKENS
        .iter()
        .map(|token| create_token(test_ctx, token))
        .collect::<Result<_, _>>()
}

/// Creates a token on Neutron via the token factory module.
pub fn create_token(test_ctx: &mut TestContext, subdenom: &str) -> Result<(), SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let _ = neutron.rb.tx(
        format!("tokenfactory create-denom {subdenom}").as_str(),
        true,
    )?;

    Ok(())
}

/// Instantiates the auction manager.
pub fn create_auction_manager(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let mut contract_a: CosmWasm = fixtures::use_contract(test_ctx, "auction_manager")?;

    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

    let auction_code_id = neutron
        .contract_codes
        .get("auction")
        .ok_or(SetupError::MissingContract(String::from("auction")))?;

    println!("instantiating contract {:?}", contract_a.code_id);

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

    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    neutron
        .contract_addrs
        .insert("auctions_manager".to_owned(), contract.address);

    Ok(())
}

/// Instantiates an individual auction.
pub fn create_auction(
    test_ctx: &mut TestContext,
    denom_a: &str,
    denom_b: &str,
) -> Result<(), SetupError> {
    // The auctions manager for this deployment
    let contract_a = fixtures::use_auctions_manager(test_ctx)?;

    println!("executing tx to contract {:?}", contract_a.contract_addr);

    let _ = contract_a.execute(
        ACC_0_KEY,
        serde_json::json!(
        {
            "admin": {
                "new_auction": {
                    "msg": {
                        "pair": [denom_a, denom_b],
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
    NEUTRON_TOKENFACTORY_TOKENS
        .iter()
        .map(|token| create_auction(test_ctx, "untrn", &token))
        .collect::<Result<_, _>>()
}

/// Instantiates the token registry.
pub fn create_token_registry(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

    let mut contract_a = fixtures::use_contract(test_ctx, "astroport_native_coin_registry")?;

    println!("instantiating contract {:?}", contract_a.code_id);

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

    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    neutron.contract_addrs.insert(
        "astroport_native_coin_registry".to_owned(),
        contract.address,
    );

    Ok(())
}

/// Instantiates the astroport factory.
pub fn create_factory(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

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

    let mut contract_a = fixtures::use_contract(test_ctx, "astroport_factory")?;

    println!("instantiating contract {:?}", contract_a.code_id);

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

    let neutron = test_ctx.get_mut_chain(NEUTRON_CHAIN);

    neutron
        .contract_addrs
        .insert("astroport_factory".to_owned(), contract.address);

    Ok(())
}

pub fn create_pool(
    test_ctx: &mut TestContext,
    denom_a: &str,
    denom_b: &str,
) -> Result<(), SetupError> {
    // Factory contract instance
    let contract_a = fixtures::use_astroport_factory(test_ctx)?;

    println!("executing tx to contract {:?}", contract_a.contract_addr);

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
                "denom": denom_a
            }
        },
        {
            "native_token": {
                "denom": denom_b
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
    NEUTRON_TOKENFACTORY_TOKENS
        .iter()
        .map(|token| create_pool(test_ctx, "untrn", &token))
        .collect::<Result<_, _>>()
}

/// Creates an osmosis pool with the given denoms.
pub fn create_osmo_pool(
    test_ctx: &mut TestContext,
    denom_a: &str,
    denom_b: &str,
) -> Result<(), SetupError> {
    let osmosis = test_ctx.get_chain(OSMOSIS_CHAIN);

    // Osmosisd requires a JSON file to specify the
    // configuration of the pool being created
    let poolfile_str = serde_json::json!({
        "weights": format!("1{denom_a},1{denom_b}"),
        "initial-deposit": format!("1{denom_a},1{denom_b}"),
        "swap-fee": "0.00",
        "exit-fee": "0.00",
        "future-governor": "168h"
    })
    .to_string();

    // Write the poolfile to a file
    let mut f = OpenOptions::new()
        .write(true)
        .create(true)
        .open(OSMOSIS_POOLFILE_PATH)?;
    f.write_all(poolfile_str.as_bytes())?;

    // Copy the poolfile to the container using docker cp
    println!("Copying poolfile from {OSMOSIS_POOLFILE_PATH} to {OSMOSIS_DOCKER_CONTAINER_ID}:{REMOTE_OSMOSIS_POOLFILE_PATH}");

    println!(
        "Result of copying poolfile: {:?}",
        Command::new("docker")
            .arg("cp")
            .arg(OSMOSIS_POOLFILE_PATH)
            .arg(format!(
                "{}:{}",
                OSMOSIS_DOCKER_CONTAINER_ID, REMOTE_OSMOSIS_POOLFILE_PATH,
            ))
            .output()?
    );

    fs::remove_file(OSMOSIS_POOLFILE_PATH)?;

    // Create pool
    osmosis.rb.tx(
        format!("tx poolmanager create-pool --pool-file {REMOTE_OSMOSIS_POOLFILE_PATH} --from {ACC_0_KEY} --fees 500uosmo")
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

    create_osmo_pool(test_ctx, "uosmo", ntrn_denom.as_str())?;

    Ok(())
}

/// Funds all pools
pub fn fund_pools(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    NEUTRON_TOKENFACTORY_TOKENS
        .iter()
        .map(|token| {
            fund_pool(
                test_ctx,
                "untrn",
                &token,
                (rand::random::<f64>() * 10000.0) as u128,
                (rand::random::<f64>() * 10000.0) as u128,
            )
        })
        .collect::<Result<_, _>>()
}

/// Provides liquidity for a specific pool.
pub fn fund_pool(
    test_ctx: &mut TestContext,
    denom_a: &str,
    denom_b: &str,
    amt_denom_a: u128,
    amt_denom_b: u128,
) -> Result<(), SetupError> {
    // Get the pool contract from the
    // pool factory
    let factory = fixtures::use_astroport_factory(test_ctx)?;

    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let acc_0_addr = neutron.admin_addr.clone();

    let pool_resp = factory.query(
        serde_json::json!({
            "pair": {
                "asset_infos": [
                    {
                        "native_token": {
                            "denom": denom_a,
                        },
                    },
                    {
                        "native_token": {
                            "denom": denom_b,
                        }
                    }
                ]
            }
        })
        .to_string()
        .as_str(),
    );

    let pool_addr = pool_resp
        .get("contract_addr")
        .and_then(|addr_json| addr_json.as_str())
        .ok_or(SetupError::MissingContract(String::from("astroport_pair")))?;

    // Get the instance from the address
    let mut pool_contract = fixtures::use_contract(test_ctx, "astroport_pair")?;
    pool_contract.contract_addr = Some(pool_addr.to_owned());

    // Provide liquidity
    pool_contract.execute(
        ACC_0_KEY,
        serde_json::json!({
            "provide_liquidity": {
                "assets": [
                    {
                        "info": {
                            "native_token": {
                                "denom": denom_a,
                            },
                        },
                        "amount": amt_denom_a.to_string(),
                    },
                    {
                        "info": {
                            "native_token": {
                                "denom": denom_b,
                            },
                        },
                        "amount": amt_denom_b.to_string(),
                    },
                ],
                "slippage_tolerance": "0.01",
                "auto_stake": false,
                "receiver": acc_0_addr,
            }
        })
        .to_string()
        .as_str(),
        "",
    )?;

    Ok(())
}

/// Funds all auctions.
pub fn fund_auctions(test_ctx: &mut TestContext) -> Result<(), SetupError> {
    NEUTRON_TOKENFACTORY_TOKENS
        .iter()
        .map(|token| {
            fund_auction(
                test_ctx,
                "untrn",
                &token,
                (rand::random::<f64>() * 10000.0) as u128,
            )
        })
        .collect::<Result<_, _>>()
}

/// Provides liquidity for a specific auction.
pub fn fund_auction(
    test_ctx: &mut TestContext,
    denom_a: &str,
    denom_b: &str,
    amt_denom_a: u128,
) -> Result<(), SetupError> {
    let manager = fixtures::use_auctions_manager(test_ctx)?;

    manager.execute(
        ACC_0_KEY,
        serde_json::json!({
            "auction_funds": {
                "pair": [denom_a, denom_b],
            },
        })
        .to_string()
        .as_str(),
        format!("--amount {amt_denom_a}{denom_a}").as_str(),
    )?;

    Ok(())
}

pub fn start_auction(
    test_ctx: &mut TestContext,
    denom_a: &str,
    denom_b: &str,
) -> Result<(), SetupError> {
    let manager = fixtures::use_auctions_manager(test_ctx)?;
    let neutron = test_ctx.get_chain(NEUTRON_CHAIN);

    let maybe_start_block = neutron
        .rb
        .query("block", true)
        .get("block")
        .and_then(|block| block.get("height"))
        .ok_or(SetupError::ContainerCmd(String::from("query block")))?;
    let start_block = maybe_start_block
        .as_str()
        .ok_or(SetupError::ContainerCmd(String::from("query block")))?
        .parse::<u128>()?;

    manager.execute(
        ACC_0_KEY,
        serde_json::json!({
            "server": {
                "open_auction": {
            "pair": [
            denom_a,
            denom_b
            ],
            "params": {
            "end_block": start_block + 1000,
            "start_block": start_block + 10,
            }
        }
            },
        })
        .to_string()
        .as_str(),
        "",
    )?;

    Ok(())
}
