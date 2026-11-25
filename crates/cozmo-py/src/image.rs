//! `pycozmo.image_encoder`: `ImageDecoder` and `ImageEncoder`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::PyByteArray;

use cozmo_core::image::{self, Decoder};

use crate::errors::err;

/// Decoder of display image sequences.
#[pyclass(module = "pycozmo.image_encoder", name = "ImageDecoder")]
pub struct ImageDecoder {
    #[pyo3(get, set)]
    buffer: PyObject,
    state: Decoder,
    #[pyo3(get, set)]
    debug: bool,
}

#[pymethods]
impl ImageDecoder {
    #[new]
    fn new(buffer: PyObject) -> Self {
        ImageDecoder { buffer, state: Decoder::new(), debug: false }
    }

    #[getter]
    fn image<'py>(&self, py: Python<'py>) -> Bound<'py, PyByteArray> {
        PyByteArray::new(py, &self.state.image)
    }
    #[setter]
    fn set_image(&mut self, v: Vec<u8>) {
        self.state.image = v;
    }
    #[getter]
    fn x(&self) -> i64 {
        self.state.x
    }
    #[setter]
    fn set_x(&mut self, v: i64) {
        self.state.x = v;
    }
    #[getter]
    fn y(&self) -> i64 {
        self.state.y
    }
    #[setter]
    fn set_y(&mut self, v: i64) {
        self.state.y = v;
    }
    #[getter]
    fn last_draw(&self) -> bool {
        self.state.last_draw
    }
    #[setter]
    fn set_last_draw(&mut self, v: bool) {
        self.state.last_draw = v;
    }
    #[getter]
    fn repeat_column_shift(&self) -> bool {
        self.state.repeat_column_shift
    }
    #[setter]
    fn set_repeat_column_shift(&mut self, v: bool) {
        self.state.repeat_column_shift = v;
    }

    fn decode<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyByteArray>> {
        let buf: PyBackedBytes = self.buffer.extract(py)?;
        let mut log = Vec::new();
        let res = self.state.decode(&buf, if self.debug { Some(&mut log) } else { None });
        if self.debug {
            let print = py.import("builtins")?.getattr("print")?;
            for line in log {
                print.call1((line,))?;
            }
        }
        res.map_err(err)?;
        Ok(self.image(py))
    }
}

/// Encoder of 128x32 binary images (Pillow images of mode "1").
#[pyclass(module = "pycozmo.image_encoder", name = "ImageEncoder")]
pub struct ImageEncoder {
    pixels: Vec<bool>,
}

#[pymethods]
impl ImageEncoder {
    #[new]
    fn new(im: &Bound<'_, PyAny>) -> PyResult<Self> {
        let (w, h): (i64, i64) = im.getattr("size")?.extract()?;
        if w != 128 || h != 32 {
            return Err(PyValueError::new_err(format!(
                "Invalid image dimensions. Only 128x32 images are supported. {w}x{h} given."
            )));
        }
        let mode: String = im.getattr("mode")?.extract()?;
        if mode != "1" {
            return Err(PyValueError::new_err("Invalid pixel format. Only binary images are supported."));
        }
        let data: PyBackedBytes = im.call_method0("tobytes")?.extract()?;
        Ok(ImageEncoder { pixels: image::unpack_1bit(&data, 128, 32).map_err(err)? })
    }

    fn encode<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyByteArray>> {
        let buf = image::encode(&self.pixels).map_err(err)?;
        Ok(PyByteArray::new(py, &buf))
    }
}
