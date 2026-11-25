//! `pycozmo.frame.Frame`.

use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::{PyBytes, PyList, PyType};

use cozmo_core::frame as core_frame;
use cozmo_core::protocol::{FrameType, Packet as CorePacket};

use crate::enums;
use crate::errors::err;
use crate::protocol::borrow_packets;
use crate::protocol_gen::wrap_packet;

/// Cozmo protocol frame.
#[pyclass(module = "pycozmo.frame", name = "Frame")]
pub struct Frame {
    #[pyo3(get, set, name = "type")]
    ftype: PyObject,
    #[pyo3(get, set)]
    first_seq: PyObject,
    #[pyo3(get, set)]
    seq: PyObject,
    #[pyo3(get, set)]
    ack: PyObject,
    #[pyo3(get, set)]
    pkts: PyObject,
}

fn build(py: Python<'_>, d: core_frame::Decoded) -> PyResult<Frame> {
    if !d.skipped.is_empty() {
        let logger = py.import("logging")?.call_method1("getLogger", ("pycozmo.protocol",))?;
        for s in &d.skipped {
            logger.call_method1("debug", (format!("Failed to decode packet. Ignoring. {s}"),))?;
        }
    }
    let f = d.frame;
    let pkts = f.pkts.into_iter().map(|p| wrap_packet(py, p)).collect::<PyResult<Vec<_>>>()?;
    Ok(Frame {
        ftype: enums::frame_type(py, f.ftype.value()),
        first_seq: f.first_seq.into_pyobject(py)?.into_any().unbind(),
        seq: f.seq.into_pyobject(py)?.into_any().unbind(),
        ack: f.ack.into_pyobject(py)?.into_any().unbind(),
        pkts: PyList::new(py, pkts)?.into_any().unbind(),
    })
}

#[pymethods]
impl Frame {
    #[new]
    fn new(type_id: PyObject, first_seq: PyObject, seq: PyObject, ack: PyObject, pkts: PyObject) -> Self {
        Frame { ftype: type_id, first_seq, seq, ack, pkts }
    }

    fn to_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let ftype = FrameType::from_value(self.ftype.bind(py).getattr("value")?.extract::<i64>()?).map_err(err)?;
        let first_seq: i64 = self.first_seq.extract(py)?;
        let seq: i64 = self.seq.extract(py)?;
        let ack: i64 = self.ack.extract(py)?;
        let borrowed = borrow_packets(self.pkts.bind(py))?;
        let effective = borrowed.iter().map(|p| p.effective(py)).collect::<PyResult<Vec<_>>>()?;
        let refs: Vec<&CorePacket> = effective.iter().map(|p| p.as_ref()).collect();
        let b = core_frame::encode(ftype, first_seq, seq, ack, &refs).map_err(err)?;
        Ok(PyBytes::new(py, &b))
    }

    fn to_writer(&self, py: Python<'_>, writer: &Bound<'_, PyAny>) -> PyResult<()> {
        writer.call_method1("write_bytes", (self.to_bytes(py)?,))?;
        Ok(())
    }

    #[classmethod]
    fn from_bytes(_cls: &Bound<'_, PyType>, py: Python<'_>, buffer: PyBackedBytes) -> PyResult<Frame> {
        let d = core_frame::Frame::decode(&buffer).map_err(err)?;
        build(py, d)
    }

    #[classmethod]
    fn from_reader(_cls: &Bound<'_, PyType>, py: Python<'_>, reader: &Bound<'_, PyAny>) -> PyResult<Frame> {
        let buf: PyBackedBytes = reader.getattr("buffer")?.extract()?;
        let (d, end) = core_frame::Frame::decode_with_end(&buf).map_err(err)?;
        reader.call_method1("seek_set", (end,))?;
        build(py, d)
    }
}
