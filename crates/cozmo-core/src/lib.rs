//! Core of the Rust port of PyCozmo: protocol messages, frame layer and offline algorithms.
//!
//! This crate has no Python dependency. The Python binding is the `cozmo-py` crate.

#![forbid(unsafe_code)]

pub mod error;
pub mod frame;
pub mod protocol;
pub mod wire;

pub use error::{Error, Result};
