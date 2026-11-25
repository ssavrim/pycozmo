//! `pycozmo.emotions`: `DecayGraph` and `EmotionType`.

use pyo3::prelude::*;

use cozmo_core::emotions::{self as core, Emotion};

use crate::errors::err;

/// Piecewise linear decay graph.
#[pyclass(module = "pycozmo.emotions", name = "DecayGraph")]
pub struct DecayGraph {
    pub g: core::DecayGraph,
}

#[pymethods]
impl DecayGraph {
    #[new]
    fn new(nodes: &Bound<'_, PyAny>) -> PyResult<Self> {
        let mut xs = Vec::new();
        let mut ys = Vec::new();
        for node in nodes.try_iter()? {
            let node = node?;
            xs.push(node.getattr("x")?.extract::<f64>()?);
            ys.push(node.getattr("y")?.extract::<f64>()?);
        }
        Ok(DecayGraph { g: core::DecayGraph::new(xs, ys) })
    }

    #[getter]
    fn nodes_x(&self) -> Vec<f64> {
        self.g.xs.clone()
    }
    #[setter]
    fn set_nodes_x(&mut self, v: Vec<f64>) {
        self.g.xs = v;
    }
    #[getter]
    fn nodes_y(&self) -> Vec<f64> {
        self.g.ys.clone()
    }
    #[setter]
    fn set_nodes_y(&mut self, v: Vec<f64>) {
        self.g.ys = v;
    }

    fn evaluate(&self, x: f64) -> PyResult<f64> {
        self.g.evaluate(x).map_err(err)
    }
}

/// Emotion type: a value in [min, max] that decays along a graph.
#[pyclass(module = "pycozmo.emotions", name = "EmotionType")]
pub struct EmotionType {
    #[pyo3(get, set)]
    clock: PyObject,
    #[pyo3(get, set)]
    name: String,
    #[pyo3(get, set)]
    decay_graph: PyObject,
    #[pyo3(get, set)]
    repetition_penalty: PyObject,
    e: Emotion,
}

/// Runs `f` with an evaluator of `graph`: direct for a Rust `DecayGraph`, through `evaluate()` otherwise.
fn with_graph<T>(
    py: Python<'_>,
    graph: &PyObject,
    f: impl FnOnce(&mut dyn FnMut(f64) -> cozmo_core::Result<f64>) -> cozmo_core::Result<T>,
) -> PyResult<T> {
    let bound = graph.bind(py);
    if let Ok(g) = bound.downcast::<DecayGraph>() {
        let g = g.borrow();
        return f(&mut |x| g.g.evaluate(x)).map_err(err);
    }
    let mut py_err: Option<PyErr> = None;
    let res = f(&mut |x| match bound.call_method1("evaluate", (x,)).and_then(|v| v.extract::<f64>()) {
        Ok(v) => Ok(v),
        Err(e) => {
            py_err = Some(e);
            Err(cozmo_core::Error::Value(String::new()))
        }
    });
    match (res, py_err) {
        (_, Some(e)) => Err(e),
        (r, None) => r.map_err(err),
    }
}

impl EmotionType {
    fn now(&self, py: Python<'_>) -> PyResult<f64> {
        self.clock.bind(py).call0()?.extract::<f64>()
    }
}

#[pymethods]
impl EmotionType {
    #[new]
    #[pyo3(signature = (name, decay_graph, repetition_penalty, default_value=0.0, min_value=-1.0, max_value=1.0, clock=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        name: &Bound<'_, PyAny>,
        decay_graph: PyObject,
        repetition_penalty: PyObject,
        default_value: f64,
        min_value: f64,
        max_value: f64,
        clock: Option<PyObject>,
    ) -> PyResult<Self> {
        let clock = match clock {
            Some(c) => c,
            None => py.import("time")?.getattr("perf_counter")?.unbind(),
        };
        let last_update = clock.bind(py).call0()?.extract::<f64>()?;
        Ok(EmotionType {
            clock,
            name: name.str()?.to_string(),
            decay_graph,
            repetition_penalty,
            e: Emotion { min: min_value, max: max_value, age: 0.0, last_update, value: default_value },
        })
    }

    #[getter]
    fn min(&self) -> f64 {
        self.e.min
    }
    #[setter]
    fn set_min(&mut self, v: f64) {
        self.e.min = v;
    }
    #[getter]
    fn max(&self) -> f64 {
        self.e.max
    }
    #[setter]
    fn set_max(&mut self, v: f64) {
        self.e.max = v;
    }
    #[getter]
    fn age(&self) -> f64 {
        self.e.age
    }
    #[setter]
    fn set_age(&mut self, v: f64) {
        self.e.age = v;
    }
    #[getter]
    fn last_update(&self) -> f64 {
        self.e.last_update
    }
    #[setter]
    fn set_last_update(&mut self, v: f64) {
        self.e.last_update = v;
    }
    #[getter]
    fn value(&self) -> f64 {
        self.e.value
    }
    #[setter]
    fn set_value(&mut self, v: f64) {
        self.e.value = v;
    }

    /// Sets the value and restarts the decay.
    fn set(&mut self, py: Python<'_>, value: f64) -> PyResult<()> {
        let now = self.now(py)?;
        let e = &mut self.e;
        with_graph(py, &self.decay_graph, |g| e.set(value, now, g))
    }

    /// Adds to the value. The decay restarts when the value changes sign or is strengthened by a delta > 0.05.
    fn add(&mut self, py: Python<'_>, delta: f64) -> PyResult<()> {
        let now = self.now(py)?;
        let e = &mut self.e;
        with_graph(py, &self.decay_graph, |g| e.add(delta, now, g))
    }

    /// Decays the value over the time since the last update.
    fn update(&mut self, py: Python<'_>) -> PyResult<()> {
        let now = self.now(py)?;
        let e = &mut self.e;
        with_graph(py, &self.decay_graph, |g| e.update(now, g))
    }
}
