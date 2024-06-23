use localic_std::errors::LocalError;
use reqwest::Error as ReqwestError;
use std::io::Error as IoError;
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
    Serialization,
    #[error("failed to query container with cmd `{0}`")]
    ContainerCmd(String),
}

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
