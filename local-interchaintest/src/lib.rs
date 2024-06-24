/// Utilities for deploying contracts
pub mod setup;

/// Utilities for managing test contexts.
pub mod util;

/// Test environment scaffolding.
pub mod fixtures;

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
pub const OSMOSIS_POOLFILE_PATH: &str = "/tmp/pool_file.json";

/// Path to the remoe machine's osmosis pool file
pub const REMOTE_OSMOSIS_POOLFILE_PATH: &str = "/var/cosmos-chain/localosmosis-1/pool_file.json";

/// Container id of the local osmosis node
pub const OSMOSIS_DOCKER_CONTAINER_ID: &str = "localosmosis-1-val-0-neutron_osmosis_gaiaic";

/// Subdenoms of tokenfactory tokens created for testing
pub const NEUTRON_TOKENFACTORY_TOKENS: [&str; 4] =
    ["bruhtoken", "amogustoken", "skibidicoin", "gyattoken"];
