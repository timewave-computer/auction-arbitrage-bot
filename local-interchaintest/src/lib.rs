/// Utilities for deploying contracts
pub mod setup;

/// Utilities for managing test contexts.
pub mod util;

/// Top-level testing error.
pub mod error;

/// The API URL of local-ic
pub const API_URL: &str = "http://localhost:42069";

/// The path to the local-ic chain config
pub const CHAIN_CONFIG_PATH: &str = "chains/neutron_osmosis_gaia.json";

/// The path to deployed contract wasm
pub const ARTIFACTS_PATH: &str = "../contracts";

/// The local interchain chain ID for osmosis
pub const OSMOSIS_CHAIN_ID: &str = "localosmosis-1";

/// The name of the osmosis chain
pub const OSMOSIS_CHAIN: &str = "osmosis";

/// Path to the host machine's osmosis pool file
pub const OSMOSIS_POOLFILE_PATH: &str = "/var/cosmos-chain/localosmosis-1/pool_file.json";
