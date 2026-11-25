//! Base classes of `pycozmo.protocol_base`: `Struct`, `Packet` and the classes of messages that are not declared.

use std::borrow::Cow;

use pyo3::exceptions::{PyNotImplementedError, PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::{PyBytes, PyType};

use cozmo_core::protocol::{self as core, Body, Message, PacketType};
use cozmo_core::wire::Reader;

use crate::conv::ArrKind;
use crate::enums;
use crate::errors::err;

/// Base class of structures and packets.
#[pyclass(subclass, module = "pycozmo.protocol_base", name = "Struct")]
pub struct Struct;

#[pymethods]
impl Struct {
    /// Writes the encoded object to a `pycozmo.protocol_utils.BinaryWriter`.
    fn to_writer(slf: &Bound<'_, Self>, writer: &Bound<'_, PyAny>) -> PyResult<()> {
        let b = slf.call_method0("to_bytes")?;
        writer.call_method1("write_bytes", (b,))?;
        Ok(())
    }
}

/// Base class of packets. Holds the core packet; the subclasses only add the properties of their fields.
#[pyclass(extends = Struct, subclass, module = "pycozmo.protocol_base", name = "Packet")]
pub struct Packet {
    pub inner: core::Packet,
    /// Shapes of the array fields (see `ArrKind`).
    pub kinds: [ArrKind; 8],
    /// Python objects of the array of structures of the message, when it has one and it was set or read.
    pub struct_objs: Option<Vec<Py<PyAny>>>,
}

impl Packet {
    pub fn from_message(m: Message, kinds: [ArrKind; 8]) -> Self {
        Packet { inner: core::Packet::new(m), kinds, struct_objs: None }
    }

    pub fn from_packet(p: core::Packet, kinds: [ArrKind; 8]) -> Self {
        Packet { inner: p, kinds, struct_objs: None }
    }

    /// The core packet, with the current values of the structures it references.
    pub fn effective(&self, py: Python<'_>) -> PyResult<Cow<'_, core::Packet>> {
        match (&self.struct_objs, &self.inner.body) {
            (Some(objs), Body::Message(_)) => {
                let mut p = self.inner.clone();
                if let Body::Message(m) = &mut p.body {
                    crate::protocol_gen::sync_structs(py, m, objs)?;
                }
                Ok(Cow::Owned(p))
            }
            _ => Ok(Cow::Borrowed(&self.inner)),
        }
    }
}

pub fn mismatch() -> PyErr {
    PyRuntimeError::new_err("the content of the packet does not match its class")
}

fn packet_type_value(value: &Bound<'_, PyAny>) -> PyResult<PacketType> {
    let member = enums::packet_type_class(value.py()).call1((value,))?;
    PacketType::from_value(member.getattr("value")?.extract::<i64>()?).map_err(err)
}

/// `{type}(field=value, ...)` like the generated `__repr__()` of PyCozmo.
pub fn repr_fields(obj: &Bound<'_, PyAny>, fields: &[&str]) -> PyResult<String> {
    let name = obj.get_type().name()?;
    let mut parts = Vec::with_capacity(fields.len());
    for f in fields {
        parts.push(format!("{f}={}", obj.getattr(*f)?.str()?));
    }
    Ok(format!("{name}({})", parts.join(", ")))
}

/// Decodes with a `pycozmo.protocol_utils.BinaryReader` and moves it after the decoded data.
pub fn with_reader<T>(
    reader: &Bound<'_, PyAny>,
    f: impl FnOnce(&mut Reader<'_>) -> cozmo_core::Result<T>,
) -> PyResult<T> {
    let buf: PyBackedBytes = reader.getattr("buffer")?.extract()?;
    let pos: usize = reader.call_method0("tell")?.extract()?;
    let mut r = Reader::new(&buf, pos);
    let v = f(&mut r).map_err(err)?;
    reader.call_method1("seek_set", (r.tell(),))?;
    Ok(v)
}

fn hex_dump(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect::<Vec<_>>().join(":")
}

fn hex2(v: i64) -> String {
    if v < 0 {
        format!("-{:02x}", v.unsigned_abs())
    } else {
        format!("{v:02x}")
    }
}

#[pymethods]
impl Packet {
    #[getter(r#type)]
    fn get_type(&self, py: Python<'_>) -> PyObject {
        enums::packet_type(py, self.inner.ptype.value())
    }

    #[setter(r#type)]
    fn set_type(&mut self, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.inner.ptype = packet_type_value(value)?;
        Ok(())
    }

    #[getter]
    fn id(&self) -> Option<i64> {
        self.inner.id
    }

    #[setter]
    fn set_id(&mut self, value: Option<i64>) {
        self.inner.id = value;
    }

    #[getter]
    fn seq(&self) -> i64 {
        self.inner.seq
    }

    #[setter]
    fn set_seq(&mut self, value: i64) {
        self.inner.seq = value;
    }

    #[getter]
    fn ack(&self) -> i64 {
        self.inner.ack
    }

    #[setter]
    fn set_ack(&mut self, value: i64) {
        self.inner.ack = value;
    }

    fn is_oob(&self) -> bool {
        self.inner.is_oob()
    }

    fn is_from_robot(&self) -> bool {
        self.inner.is_from_robot()
    }

    fn is_from_engine(&self) -> bool {
        !self.inner.is_from_robot()
    }

    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        Ok(self.effective(py)?.payload_len())
    }

    fn to_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let b = self.effective(py)?.payload_bytes().map_err(err)?;
        Ok(PyBytes::new(py, &b))
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        let fields = match &slf.borrow().inner.body {
            Body::Message(m) => m.fields(),
            Body::Unknown(_) => &[],
        };
        repr_fields(slf.as_any(), fields)
    }
}

/// Packet of a message that is not declared.
#[pyclass(extends = Packet, subclass, module = "pycozmo.protocol_base", name = "UnknownPacket")]
pub struct UnknownPacket;

fn to_bytes_vec(value: &Bound<'_, PyAny>) -> PyResult<Vec<u8>> {
    if let Ok(b) = value.downcast::<PyBytes>() {
        return Ok(b.as_bytes().to_vec());
    }
    let b = value.py().import("builtins")?.getattr("bytes")?.call1((value,))?;
    Ok(b.downcast::<PyBytes>()?.as_bytes().to_vec())
}

fn unknown_init(ptype: PacketType, data: Vec<u8>, id: Option<i64>) -> PyClassInitializer<UnknownPacket> {
    PyClassInitializer::from(Struct)
        .add_subclass(Packet {
            inner: core::Packet::unknown(ptype, id, data),
            kinds: [ArrKind::List; 8],
            struct_objs: None,
        })
        .add_subclass(UnknownPacket)
}

#[pymethods]
impl UnknownPacket {
    #[new]
    #[pyo3(signature = (packet_type, data, packet_id=None))]
    fn new(
        packet_type: &Bound<'_, PyAny>,
        data: &Bound<'_, PyAny>,
        packet_id: Option<i64>,
    ) -> PyResult<PyClassInitializer<Self>> {
        Ok(unknown_init(packet_type_value(packet_type)?, to_bytes_vec(data)?, packet_id))
    }

    #[getter]
    fn data<'py>(slf: PyRef<'_, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let base: &Packet = slf.as_ref();
        match &base.inner.body {
            Body::Unknown(d) => Ok(PyBytes::new(py, d)),
            Body::Message(_) => Err(mismatch()),
        }
    }

    #[setter]
    fn set_data(mut slf: PyRefMut<'_, Self>, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let v = to_bytes_vec(value)?;
        let base: &mut Packet = slf.as_mut();
        base.inner.body = Body::Unknown(v);
        Ok(())
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        let name = slf.get_type().name()?;
        let r = slf.borrow();
        let base: &Packet = r.as_ref();
        let data = match &base.inner.body {
            Body::Unknown(d) => hex_dump(d),
            Body::Message(_) => String::new(),
        };
        Ok(format!("{name}({}, {data})", hex2(base.inner.ptype.value())))
    }

    #[classmethod]
    fn from_bytes(_cls: &Bound<'_, PyType>, _buffer: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        // The size is not known.
        Err(PyNotImplementedError::new_err(()))
    }

    #[classmethod]
    fn from_reader(_cls: &Bound<'_, PyType>, _reader: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        // The size is not known.
        Err(PyNotImplementedError::new_err(()))
    }
}

fn unknown_repr(slf: &Bound<'_, PyAny>) -> PyResult<String> {
    let name = slf.get_type().name()?;
    let id: Option<i64> = slf.getattr("id")?.extract()?;
    let data: Vec<u8> = slf.getattr("data")?.extract::<PyBackedBytes>()?.to_vec();
    Ok(format!("{name}({}, {})", hex2(id.unwrap_or(0)), hex_dump(&data)))
}

/// Command that is not declared.
#[pyclass(extends = UnknownPacket, subclass, module = "pycozmo.protocol_base", name = "UnknownCommand")]
pub struct UnknownCommand;

#[pymethods]
impl UnknownCommand {
    #[new]
    #[pyo3(signature = (packet_id, data=None))]
    fn new(packet_id: i64, data: Option<&Bound<'_, PyAny>>) -> PyResult<PyClassInitializer<Self>> {
        let data = match data {
            Some(d) => to_bytes_vec(d)?,
            None => Vec::new(),
        };
        Ok(unknown_init(PacketType::Command, data, Some(packet_id)).add_subclass(UnknownCommand))
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        unknown_repr(slf.as_any())
    }
}

/// Event that is not declared.
#[pyclass(extends = UnknownPacket, subclass, module = "pycozmo.protocol_base", name = "UnknownEvent")]
pub struct UnknownEvent;

#[pymethods]
impl UnknownEvent {
    #[new]
    #[pyo3(signature = (packet_id, data=None))]
    fn new(packet_id: i64, data: Option<&Bound<'_, PyAny>>) -> PyResult<PyClassInitializer<Self>> {
        let data = match data {
            Some(d) => to_bytes_vec(d)?,
            None => Vec::new(),
        };
        Ok(unknown_init(PacketType::Event, data, Some(packet_id)).add_subclass(UnknownEvent))
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        unknown_repr(slf.as_any())
    }
}

/// Wraps a core packet of a message that is not declared.
pub fn wrap_unknown(py: Python<'_>, pkt: core::Packet) -> PyResult<PyObject> {
    let ptype = pkt.ptype;
    let base = PyClassInitializer::from(Struct)
        .add_subclass(Packet { inner: pkt, kinds: [ArrKind::List; 8], struct_objs: None })
        .add_subclass(UnknownPacket);
    Ok(match ptype {
        PacketType::Command => Py::new(py, base.add_subclass(UnknownCommand))?.into_any(),
        PacketType::Event => Py::new(py, base.add_subclass(UnknownEvent))?.into_any(),
        _ => Py::new(py, base)?.into_any(),
    })
}

/// Borrows the core packets of a sequence of Python packets.
pub fn borrow_packets<'py>(seq: &Bound<'py, PyAny>) -> PyResult<Vec<PyRef<'py, Packet>>> {
    let mut out = Vec::new();
    for item in seq.try_iter()? {
        let item = item?;
        let p = item
            .downcast::<Packet>()
            .map_err(|_| PyTypeError::new_err(format!("expected a packet, got a {}", crate::conv::type_name(&item))))?;
        out.push(p.borrow());
    }
    Ok(out)
}
