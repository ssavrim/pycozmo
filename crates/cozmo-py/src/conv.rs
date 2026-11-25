//! Conversion of Python values to protocol fields, with the validation rules and messages of
//! `pycozmo/protocol_utils.py` (`validate_integer()`, `validate_float()`, `validate_farray()`...).

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

/// How an array field was given, so that it is returned the same way: PyCozmo keeps `bytes` and `bytearray` values
/// as they are and turns other sequences into lists.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ArrKind {
    #[default]
    List,
    Bytes,
    ByteArray,
}

/// Element of a primitive array.
pub trait Elem: Copy {
    fn from_byte(b: u8) -> Self;
    fn to_byte(self) -> u8;
    fn to_py(self, py: Python<'_>) -> PyResult<PyObject>;
}

macro_rules! elem_int {
    ($($t:ty),*) => {$(
        impl Elem for $t {
            fn from_byte(b: u8) -> Self { b as $t }
            fn to_byte(self) -> u8 { self as u8 }
            fn to_py(self, py: Python<'_>) -> PyResult<PyObject> { Ok(self.into_pyobject(py)?.into_any().unbind()) }
        }
    )*};
}
elem_int!(u8, u16, u32, i8, i16, i32);

impl Elem for f64 {
    fn from_byte(b: u8) -> Self {
        f64::from(b)
    }
    fn to_byte(self) -> u8 {
        self as u8
    }
    fn to_py(self, py: Python<'_>) -> PyResult<PyObject> {
        Ok(PyFloat::new(py, self).into_any().unbind())
    }
}

impl Elem for bool {
    fn from_byte(b: u8) -> Self {
        b != 0
    }
    fn to_byte(self) -> u8 {
        self as u8
    }
    fn to_py(self, py: Python<'_>) -> PyResult<PyObject> {
        Ok(self.into_pyobject(py)?.to_owned().into_any().unbind())
    }
}

/// A Python class that wraps a core structure (for arrays of structures).
pub trait Wrapped: pyo3::PyClass {
    type Inner: Clone;
    const NAME: &'static str;
    fn inner(&self) -> &Self::Inner;
}

/// Name of the type of a value.
pub fn type_name(v: &Bound<'_, PyAny>) -> String {
    v.get_type().name().map(|n| n.to_string()).unwrap_or_else(|_| "object".into())
}

fn builtin<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    py.import("builtins")?.getattr(name)
}

/// `validate_integer()`: `int(value)` and a range check.
pub fn int(v: &Bound<'_, PyAny>, name: &str, min: i64, max: i64) -> PyResult<i64> {
    let py = v.py();
    let owned;
    let obj = if v.is_instance_of::<PyInt>() {
        v
    } else {
        match builtin(py, "int")?.call1((v,)) {
            Ok(o) => {
                owned = o;
                &owned
            }
            Err(e) if e.is_instance_of::<PyValueError>(py) => {
                return Err(PyValueError::new_err(format!("{name} must be an integer. Got a {}.", type_name(v))))
            }
            Err(e) => return Err(e),
        }
    };
    match obj.extract::<i64>() {
        Ok(x) if x >= min && x <= max => Ok(x),
        _ => Err(PyValueError::new_err(format!("{name} must be between {min} and {max}. Got {}.", obj.str()?))),
    }
}

/// `validate_float()`: `float(value)`.
pub fn float(v: &Bound<'_, PyAny>, name: &str) -> PyResult<f64> {
    if v.is_instance_of::<PyFloat>() || v.is_instance_of::<PyInt>() {
        return v.extract::<f64>();
    }
    let py = v.py();
    match builtin(py, "float")?.call1((v,)) {
        Ok(o) => o.extract::<f64>(),
        Err(e) if e.is_instance_of::<PyValueError>(py) => {
            Err(PyValueError::new_err(format!("{name} must be float. Got {}.", type_name(v))))
        }
        Err(e) => Err(e),
    }
}

/// `validate_bool()`: `bool(value)`.
pub fn boolean(v: &Bound<'_, PyAny>) -> PyResult<bool> {
    v.is_truthy()
}

/// `validate_string()`. Unlike PyCozmo, only `str` values are accepted (PyCozmo fails later, when encoding).
pub fn string(v: &Bound<'_, PyAny>, name: &str, max: usize) -> PyResult<String> {
    let s = v
        .downcast::<PyString>()
        .map_err(|_| PyTypeError::new_err(format!("{name} must be a str. Got a {}.", type_name(v))))?;
    let s = s.to_str()?;
    let n = s.chars().count();
    if n > max {
        return Err(PyValueError::new_err(format!(
            "{name} must be a string with less than or equal to {max}. Got a string of length {n}."
        )));
    }
    Ok(s.to_owned())
}

/// Enumeration setter of PyCozmo: `validate_integer(name, value.value, ...)`.
pub fn enum_value(v: &Bound<'_, PyAny>, name: &str, min: i64, max: i64) -> PyResult<i64> {
    let val = v.getattr("value")?;
    int(&val, name, min, max)
}

/// Enumeration in a constructor of PyCozmo: `EnumType(value)`, then the setter.
pub fn enum_init(py: Python<'_>, idx: usize, v: &Bound<'_, PyAny>, name: &str, min: i64, max: i64) -> PyResult<i64> {
    let member = crate::enums::class(py, idx).call1((v,))?;
    enum_value(&member, name, min, max)
}

fn sequence<'py>(v: &Bound<'py, PyAny>, name: &str) -> PyResult<Bound<'py, PyTuple>> {
    if let Ok(t) = v.downcast::<PyTuple>() {
        return Ok(t.clone());
    }
    let py = v.py();
    match builtin(py, "tuple")?.call1((v,)) {
        Ok(t) => Ok(t.downcast_into::<PyTuple>()?),
        Err(e) if e.is_instance_of::<PyValueError>(py) => {
            Err(PyValueError::new_err(format!("{name} must be a sequence. Got a {}.", type_name(v))))
        }
        Err(e) => Err(e),
    }
}

fn check_length(name: &str, n: usize, fixed: Option<usize>, max: usize) -> PyResult<()> {
    match fixed {
        Some(len) if n != len => Err(PyValueError::new_err(format!(
            "{name} must be a sequence of length {len}. Got a sequence of length {n}."
        ))),
        None if n > max => Err(PyValueError::new_err(format!(
            "{name} must be a sequence with length less than or equal to {max}. Got a sequence of length {n}."
        ))),
        _ => Ok(()),
    }
}

/// `validate_farray()` (`fixed` is the length) or `validate_varray()` (`max` is the maximum length) for primitive
/// elements.
pub fn prim_seq<T: Elem>(
    v: &Bound<'_, PyAny>,
    name: &str,
    fixed: Option<usize>,
    max: usize,
    elem: impl Fn(String, &Bound<'_, PyAny>) -> PyResult<T>,
) -> PyResult<(Vec<T>, ArrKind)> {
    let raw = if let Ok(b) = v.downcast::<PyBytes>() {
        Some((b.as_bytes().to_vec(), ArrKind::Bytes))
    } else if let Ok(b) = v.downcast::<PyByteArray>() {
        Some((b.to_vec(), ArrKind::ByteArray))
    } else {
        None
    };
    if let Some((bytes, kind)) = raw {
        check_length(name, bytes.len(), fixed, max)?;
        // PyCozmo does not validate the elements of byte strings.
        return Ok((bytes.into_iter().map(T::from_byte).collect(), kind));
    }
    let t = sequence(v, name)?;
    check_length(name, t.len(), fixed, max)?;
    let mut out = Vec::with_capacity(t.len());
    for (i, e) in t.iter().enumerate() {
        out.push(elem(format!("('{name}', {i})"), &e)?);
    }
    Ok((out, ArrKind::List))
}

/// `validate_farray()` for structures: elements must be instances of the class.
pub fn struct_seq<C: Wrapped>(
    v: &Bound<'_, PyAny>,
    name: &str,
    fixed: Option<usize>,
    max: usize,
) -> PyResult<Vec<C::Inner>> {
    let t = sequence(v, name)?;
    check_length(name, t.len(), fixed, max)?;
    let mut out = Vec::with_capacity(t.len());
    for (i, e) in t.iter().enumerate() {
        match e.downcast::<C>() {
            Ok(c) => out.push(c.borrow().inner().clone()),
            Err(_) => {
                return Err(PyValueError::new_err(format!(
                    "('{name}', {i}) must be a {}. Got a {}.",
                    <C as Wrapped>::NAME,
                    type_name(&e)
                )))
            }
        }
    }
    Ok(out)
}

/// Values and Python objects of an array of structures.
pub type StructSeq<C> = (Vec<<C as Wrapped>::Inner>, Vec<Py<PyAny>>);

/// Like `struct_seq()`, and also returns the Python objects, which the packet keeps like PyCozmo does.
pub fn struct_seq_objs<C: Wrapped>(
    v: &Bound<'_, PyAny>,
    name: &str,
    fixed: Option<usize>,
    max: usize,
) -> PyResult<StructSeq<C>> {
    let vals = struct_seq::<C>(v, name, fixed, max)?;
    let objs = sequence(v, name)?.iter().map(|o| o.unbind()).collect();
    Ok((vals, objs))
}

/// A primitive array as PyCozmo returns it: a list, `bytes` or a `bytearray`.
pub fn prim_list<T: Elem>(py: Python<'_>, v: &[T], kind: ArrKind) -> PyResult<PyObject> {
    match kind {
        ArrKind::Bytes => {
            let b: Vec<u8> = v.iter().map(|e| e.to_byte()).collect();
            Ok(PyBytes::new(py, &b).into_any().unbind())
        }
        ArrKind::ByteArray => {
            let b: Vec<u8> = v.iter().map(|e| e.to_byte()).collect();
            Ok(PyByteArray::new(py, &b).into_any().unbind())
        }
        ArrKind::List => {
            let items = v.iter().map(|e| e.to_py(py)).collect::<PyResult<Vec<_>>>()?;
            Ok(PyList::new(py, items)?.into_any().unbind())
        }
    }
}

/// Matches constructor arguments to fields like a Python `__init__(self, field1=..., field2=...)`: positional or
/// keyword arguments, with the error messages of Python. Missing arguments are `None` (the declared default).
pub fn parse_args<'py>(
    cls: &str,
    args: &Bound<'py, PyTuple>,
    kwargs: Option<&Bound<'py, PyDict>>,
    fields: &[&str],
) -> PyResult<Vec<Option<Bound<'py, PyAny>>>> {
    let n = fields.len();
    if args.len() > n {
        let msg = if n == 0 {
            format!("{cls}.__init__() takes 1 positional argument but {} were given", args.len() + 1)
        } else {
            format!("{cls}.__init__() takes from 1 to {} positional arguments but {} were given", n + 1, args.len() + 1)
        };
        return Err(PyTypeError::new_err(msg));
    }
    let mut out: Vec<Option<Bound<'py, PyAny>>> = vec![None; n];
    for (i, a) in args.iter().enumerate() {
        out[i] = Some(a);
    }
    if let Some(kw) = kwargs {
        for (k, v) in kw.iter() {
            let key = k.downcast::<PyString>()?.to_str()?.to_owned();
            match fields.iter().position(|f| *f == key) {
                Some(i) if out[i].is_some() => {
                    return Err(PyTypeError::new_err(format!(
                        "{cls}.__init__() got multiple values for argument '{key}'"
                    )))
                }
                Some(i) => out[i] = Some(v),
                None => {
                    return Err(PyTypeError::new_err(format!(
                        "{cls}.__init__() got an unexpected keyword argument '{key}'"
                    )))
                }
            }
        }
    }
    Ok(out)
}
