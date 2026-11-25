//! Python extension `pycozmo._cozmo`: the binding of `cozmo-core`.
//!
//! The Python modules of the `pycozmo` package re-export these classes and functions (see `RUST-REWRITE.md`).

#![forbid(unsafe_code)]

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};

mod anim;
mod conv;
mod emotions;
mod enums;
mod errors;
mod face;
mod frame;
mod image;
mod protocol;
mod protocol_gen;
mod window;

#[pymodule]
fn _cozmo(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    enums::init(py, m)?;
    m.add_class::<protocol::Struct>()?;
    m.add_class::<protocol::Packet>()?;
    m.add_class::<protocol::UnknownPacket>()?;
    m.add_class::<protocol::UnknownCommand>()?;
    m.add_class::<protocol::UnknownEvent>()?;
    protocol_gen::register(m)?;
    m.add_class::<frame::Frame>()?;
    m.add_class::<window::BaseWindow>()?;
    m.add_class::<window::ReceiveWindow>()?;
    m.add_class::<window::SendWindow>()?;
    m.add_class::<image::ImageDecoder>()?;
    m.add_class::<image::ImageEncoder>()?;
    m.add_class::<emotions::DecayGraph>()?;
    m.add_class::<emotions::EmotionType>()?;
    m.add_function(wrap_pyfunction!(anim::anim_register, m)?)?;
    m.add_function(wrap_pyfunction!(face::render_procedural_face, m)?)?;
    m.add_function(wrap_pyfunction!(anim::anim_decode_clips, m)?)?;
    m.add_function(wrap_pyfunction!(anim::anim_encode_clips, m)?)?;

    use cozmo_core::protocol as p;
    m.add("PACKETS_BY_ID", PyList::new(py, p::PACKETS_BY_ID)?)?;
    let groups = p::PACKETS_BY_GROUP
        .iter()
        .map(|(g, ids)| PyTuple::new(py, [g.into_pyobject(py)?.into_any(), PyList::new(py, *ids)?.into_any()]))
        .collect::<PyResult<Vec<_>>>()?;
    m.add("PACKETS_BY_GROUP", PyList::new(py, groups)?)?;
    m.add("FRAME_ID", pyo3::types::PyBytes::new(py, p::FRAME_ID))?;
    Ok(())
}
