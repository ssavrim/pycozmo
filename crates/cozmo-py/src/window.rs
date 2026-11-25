//! `pycozmo.window`: `BaseWindow`, `ReceiveWindow` and `SendWindow`.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};

use cozmo_core::window::Window;

use crate::errors::err;

fn slots_to_py(py: Python<'_>, w: &Window<Py<PyAny>>) -> PyResult<PyObject> {
    let items: Vec<PyObject> =
        w.slots.iter().map(|s| s.as_ref().map(|o| o.clone_ref(py)).unwrap_or(py.None())).collect();
    Ok(PyList::new(py, items)?.into_any().unbind())
}

fn data(o: &Bound<'_, PyAny>) -> Option<Py<PyAny>> {
    if o.is_none() {
        None
    } else {
        Some(o.clone().unbind())
    }
}

/// Base communication window class.
#[pyclass(subclass, module = "pycozmo.window", name = "BaseWindow")]
pub struct BaseWindow {
    pub w: Window<Py<PyAny>>,
}

#[pymethods]
impl BaseWindow {
    #[new]
    #[pyo3(signature = (seq_bits, size=None, max_seq=None))]
    fn new(seq_bits: i64, size: Option<i64>, max_seq: Option<i64>) -> PyResult<Self> {
        Ok(BaseWindow { w: Window::new(seq_bits, size, max_seq).map_err(err)? })
    }

    #[getter]
    fn size(&self) -> i64 {
        self.w.size
    }
    #[setter]
    fn set_size(&mut self, v: i64) {
        self.w.size = v;
    }
    #[getter]
    fn expected_seq(&self) -> i64 {
        self.w.expected_seq
    }
    #[setter]
    fn set_expected_seq(&mut self, v: i64) {
        self.w.expected_seq = v;
    }
    #[getter]
    fn max_seq(&self) -> i64 {
        self.w.max_seq
    }
    #[setter]
    fn set_max_seq(&mut self, v: i64) {
        self.w.max_seq = v;
    }
    #[getter]
    fn window(&self, py: Python<'_>) -> PyResult<PyObject> {
        slots_to_py(py, &self.w)
    }
    #[setter]
    fn set_window(&mut self, v: Vec<Bound<'_, PyAny>>) {
        self.w.slots = v.iter().map(data).collect();
    }

    fn is_valid_seq(&self, seq: i64) -> bool {
        self.w.is_valid_seq(seq)
    }

    fn reset(&mut self) {
        self.w.reset_base();
    }
}

/// Receive communication window class.
#[pyclass(extends = BaseWindow, subclass, module = "pycozmo.window", name = "ReceiveWindow")]
pub struct ReceiveWindow;

#[pymethods]
impl ReceiveWindow {
    #[new]
    #[pyo3(signature = (seq_bits, size=None, max_seq=None))]
    fn new(seq_bits: i64, size: Option<i64>, max_seq: Option<i64>) -> PyResult<(Self, BaseWindow)> {
        Ok((ReceiveWindow, BaseWindow::new(seq_bits, size, max_seq)?))
    }

    #[getter]
    fn last_seq(slf: PyRef<'_, Self>) -> i64 {
        slf.as_ref().w.last_seq
    }
    #[setter]
    fn set_last_seq(mut slf: PyRefMut<'_, Self>, v: i64) {
        slf.as_mut().w.last_seq = v;
    }

    fn is_out_of_order(slf: PyRef<'_, Self>, seq: i64) -> bool {
        slf.as_ref().w.recv_is_out_of_order(seq)
    }

    fn exists(slf: PyRef<'_, Self>, seq: i64) -> bool {
        slf.as_ref().w.exists(seq)
    }

    fn put(mut slf: PyRefMut<'_, Self>, seq: i64, data: &Bound<'_, PyAny>) {
        slf.as_mut().w.recv_put(seq, self::data(data));
    }

    fn get(mut slf: PyRefMut<'_, Self>, py: Python<'_>) -> PyObject {
        slf.as_mut().w.recv_get().unwrap_or(py.None())
    }

    fn reset(mut slf: PyRefMut<'_, Self>) {
        slf.as_mut().w.recv_reset();
    }
}

/// Send communication window class.
#[pyclass(extends = BaseWindow, subclass, module = "pycozmo.window", name = "SendWindow")]
pub struct SendWindow;

#[pymethods]
impl SendWindow {
    #[new]
    #[pyo3(signature = (seq_bits, size=None, max_seq=None))]
    fn new(seq_bits: i64, size: Option<i64>, max_seq: Option<i64>) -> PyResult<(Self, BaseWindow)> {
        Ok((SendWindow, BaseWindow::new(seq_bits, size, max_seq)?))
    }

    #[getter]
    fn next_seq(slf: PyRef<'_, Self>) -> i64 {
        slf.as_ref().w.next_seq
    }
    #[setter]
    fn set_next_seq(mut slf: PyRefMut<'_, Self>, v: i64) {
        slf.as_mut().w.next_seq = v;
    }

    fn is_out_of_order(slf: PyRef<'_, Self>, seq: i64) -> bool {
        slf.as_ref().w.send_is_out_of_order(seq)
    }

    fn is_full(slf: PyRef<'_, Self>) -> bool {
        slf.as_ref().w.is_full()
    }

    fn put(mut slf: PyRefMut<'_, Self>, py: Python<'_>, data: &Bound<'_, PyAny>) -> PyResult<i64> {
        match slf.as_mut().w.send_put(self::data(data)) {
            Some(seq) => Ok(seq),
            None => {
                let cls = py.import("pycozmo.exception")?.getattr("NoSpace")?;
                Err(PyErr::from_value(cls.call1(("Send window full.",))?))
            }
        }
    }

    fn acknowledge(mut slf: PyRefMut<'_, Self>, seq: i64) {
        slf.as_mut().w.acknowledge(seq);
    }

    fn get(slf: PyRef<'_, Self>, py: Python<'_>) -> PyResult<PyObject> {
        let items = slf
            .as_ref()
            .w
            .send_get()
            .into_iter()
            .map(|(seq, d)| {
                let d = d.map(|o| o.clone_ref(py)).unwrap_or(py.None());
                PyTuple::new(py, [seq.into_pyobject(py)?.into_any().unbind(), d])
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(PyList::new(py, items)?.into_any().unbind())
    }

    fn reset(mut slf: PyRefMut<'_, Self>) {
        slf.as_mut().w.send_reset();
    }
}
