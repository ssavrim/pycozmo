//! Error type of the core crate.
//!
//! The variants follow the exception classes that PyCozmo raises for the same conditions, so that the Python binding
//! can map them one to one (see `RUST-REWRITE.md`).

use std::fmt;

/// Errors of the core crate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Not enough data in a buffer, or an index outside of an image (Python `IndexError`).
    Index(String),
    /// An invalid value (Python `ValueError`).
    Value(String),
    /// A floating point value that does not fit its wire format (Python `OverflowError`).
    Overflow(String),
    /// A value that does not fit a binary field (Python `struct.error`).
    Pack(String),
    /// A broken invariant that PyCozmo checks with `assert` (Python `AssertionError`).
    Assertion(String),
    /// A case that PyCozmo does not implement (Python `NotImplementedError`).
    NotImplemented(String),
    /// A frame whose packets could not be checked because none was decoded (Python `UnboundLocalError`).
    UnboundLocal(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Index(m)
            | Error::Value(m)
            | Error::Overflow(m)
            | Error::Pack(m)
            | Error::Assertion(m)
            | Error::NotImplemented(m)
            | Error::UnboundLocal(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for Error {}

/// Result type of the core crate.
pub type Result<T> = std::result::Result<T, Error>;

/// Message of PyCozmo's `validate_integer()` for a value outside of its range.
pub fn range_message(name: &str, min: i64, max: i64, value: impl fmt::Display) -> String {
    format!("{name} must be between {min} and {max}. Got {value}.")
}

/// Checks that an integer is in a range, with the message of PyCozmo's `validate_integer()`.
pub fn check_range(name: &str, value: i64, min: i64, max: i64) -> Result<i64> {
    if value < min || value > max {
        Err(Error::Value(range_message(name, min, max, value)))
    } else {
        Ok(value)
    }
}
