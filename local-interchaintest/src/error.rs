use super::setup::SetupError;
use localic_std::errors::LocalError;
use reqwest::Error as ReqwestError;
use thiserror::Error;

/// The top-level testing error.
#[derive(Error, Debug)]
pub enum Error {
    #[error("setup failure")]
    Setup(#[from] SetupError),
    #[error("the chain `{0}` is not recognized")]
    UnrecognizedChain(String),
    #[error("local interchain failure")]
    LocalIc(#[from] LocalError),
    #[error("failed to lookup channel")]
    ChannelLookup,
    #[error("an HTTP request failed")]
    Http(#[from] ReqwestError),
}
