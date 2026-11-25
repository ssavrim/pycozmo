//! Mapping of core errors to Python exceptions.

use cozmo_core::Error;
use pyo3::exceptions::{
    PyAssertionError, PyIndexError, PyNotImplementedError, PyOverflowError, PyUnboundLocalError, PyValueError,
};
use pyo3::prelude::*;

/// Converts a core error to the Python exception that PyCozmo raises for the same condition.
pub fn err(e: Error) -> PyErr {
    match e {
        Error::Index(m) => PyIndexError::new_err(m),
        Error::Value(m) => PyValueError::new_err(m),
        Error::Overflow(m) => PyOverflowError::new_err(m),
        Error::Assertion(m) => {
            if m.is_empty() {
                PyAssertionError::new_err(())
            } else {
                PyAssertionError::new_err(m)
            }
        }
        Error::NotImplemented(m) => PyNotImplementedError::new_err(m),
        Error::UnboundLocal(m) => PyUnboundLocalError::new_err(m),
        Error::Pack(m) => Python::with_gil(|py| match py.import("struct").and_then(|s| s.getattr("error")) {
            Ok(cls) => match cls.call1((m.clone(),)) {
                Ok(inst) => PyErr::from_value(inst),
                Err(e) => e,
            },
            Err(e) => e,
        }),
    }
}
