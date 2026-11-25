//! Python enumerations of the protocol.
//!
//! The protocol enumerations are created as standard `enum.Enum` classes when the extension is loaded, so that they
//! behave like the classes of the original `protocol_encoder.py`. `PacketType` and `FrameType` are taken from
//! `pycozmo.protocol_ast`.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyDict, PyList, PyTuple};

use cozmo_core::protocol::ENUMS;

struct Entry {
    cls: Py<PyAny>,
    members: HashMap<i64, Py<PyAny>>,
}

impl Entry {
    fn from_class(cls: Bound<'_, PyAny>) -> PyResult<Self> {
        let map = cls.getattr("_value2member_map_")?;
        let map = map.downcast::<PyDict>()?;
        let mut members = HashMap::new();
        for (k, v) in map.iter() {
            if let Ok(k) = k.extract::<i64>() {
                members.insert(k, v.unbind());
            }
        }
        Ok(Entry { cls: cls.unbind(), members })
    }

    fn member(&self, py: Python<'_>, v: i64) -> PyObject {
        match self.members.get(&v) {
            Some(m) => m.clone_ref(py),
            None => self.cls.bind(py).call1((v,)).map(|o| o.unbind()).unwrap_or_else(|_| py.None()),
        }
    }
}

static PROTOCOL: GILOnceCell<Vec<Entry>> = GILOnceCell::new();
static PACKET_TYPE: GILOnceCell<Entry> = GILOnceCell::new();
static FRAME_TYPE: GILOnceCell<Entry> = GILOnceCell::new();

/// Creates the protocol enumerations and adds them to the module.
pub fn init(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    let enum_cls = py.import("enum")?.getattr("Enum")?;
    let mut entries = Vec::with_capacity(ENUMS.len());
    for (name, members) in ENUMS {
        let items = members
            .iter()
            .map(|(n, v)| PyTuple::new(py, [n.into_pyobject(py)?.into_any(), v.into_pyobject(py)?.into_any()]))
            .collect::<PyResult<Vec<_>>>()?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("module", "pycozmo.protocol_encoder")?;
        kwargs.set_item("qualname", *name)?;
        let cls = enum_cls.call((*name, PyList::new(py, items)?), Some(&kwargs))?;
        m.add(*name, &cls)?;
        entries.push(Entry::from_class(cls)?);
    }
    let _ = PROTOCOL.set(py, entries);
    let ast = py.import("pycozmo.protocol_ast")?;
    let _ = PACKET_TYPE.set(py, Entry::from_class(ast.getattr("PacketType")?)?);
    let _ = FRAME_TYPE.set(py, Entry::from_class(ast.getattr("FrameType")?)?);
    Ok(())
}

/// Member of protocol enumeration `idx` (index in `ENUMS`) with value `v`.
pub fn member(py: Python<'_>, idx: usize, v: i64) -> PyObject {
    PROTOCOL.get(py).expect("enumerations are created at import")[idx].member(py, v)
}

/// Class of protocol enumeration `idx`.
pub fn class(py: Python<'_>, idx: usize) -> Bound<'_, PyAny> {
    PROTOCOL.get(py).expect("enumerations are created at import")[idx].cls.bind(py).clone()
}

/// `PacketType` member.
pub fn packet_type(py: Python<'_>, v: i64) -> PyObject {
    PACKET_TYPE.get(py).expect("created at import").member(py, v)
}

/// `PacketType` class.
pub fn packet_type_class(py: Python<'_>) -> Bound<'_, PyAny> {
    PACKET_TYPE.get(py).expect("created at import").cls.bind(py).clone()
}

/// `FrameType` member.
pub fn frame_type(py: Python<'_>, v: i64) -> PyObject {
    FRAME_TYPE.get(py).expect("created at import").member(py, v)
}
