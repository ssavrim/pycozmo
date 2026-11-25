//! `ProceduralFace.render()` of `pycozmo.procedural_face`.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use cozmo_core::face::{self, EyeLayout};

use crate::errors::err;

/// Renders a procedural face. `eyes` gives, for each eye, its x offset, its corner radius and the (y offset,
/// angle offset) of its two lids. Returns the image as packed 1-bit rows (Pillow mode "1").
#[pyfunction]
#[allow(clippy::type_complexity)]
pub fn render_procedural_face<'py>(
    py: Python<'py>,
    params: Vec<f64>,
    width: i32,
    height: i32,
    eyes: [(f64, f64, [(f64, f64); 2]); 2],
) -> PyResult<Bound<'py, PyBytes>> {
    let layouts = eyes.map(|(x_offset, corner_radius, lids)| EyeLayout { x_offset, corner_radius, lids });
    let im = py.allow_threads(|| face::render(&params, width, height, &layouts)).map_err(err)?;
    Ok(PyBytes::new(py, &im.pack_1bit()))
}
