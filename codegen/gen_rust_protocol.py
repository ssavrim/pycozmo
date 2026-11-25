#!/usr/bin/env python3
"""
Generates the Rust protocol code from pycozmo/protocol_declaration.py, the way pycozmo/protocol_generator.py generates
the Python encoder.

Outputs:
- crates/cozmo-core/src/protocol/messages.rs: enums, structures and messages with encoding and decoding (pure Rust);
- crates/cozmo-py/src/protocol_gen.rs: the PyO3 classes of pycozmo.protocol_encoder.

Usage: python3 codegen/gen_rust_protocol.py   (from the repository root)

The declaration is loaded without importing the pycozmo package, so that the generator does not need the compiled
extension.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_OUT = os.path.join(ROOT, "crates", "cozmo-core", "src", "protocol", "messages.rs")
PY_OUT = os.path.join(ROOT, "crates", "cozmo-py", "src", "protocol_gen.rs")

#: Largest number of array fields in one message (see `Packet::kinds` in the binding).
MAX_ARRAYS = 8


def load_declaration():
    pkg = types.ModuleType("_decl")
    pkg.__path__ = [os.path.join(ROOT, "pycozmo")]
    sys.modules["_decl"] = pkg
    for name in ("protocol_ast", "protocol_declaration"):
        spec = importlib.util.spec_from_file_location("_decl." + name, os.path.join(ROOT, "pycozmo", name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_decl." + name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_decl.protocol_declaration"]


D = load_declaration()

# struct format, Rust type, minimum, maximum, size
INTS = {
    "UInt8Argument": ("B", "u8", 0, 255, 1),
    "UInt16Argument": ("H", "u16", 0, 65535, 2),
    "UInt32Argument": ("L", "u32", 0, 4294967295, 4),
    "Int8Argument": ("b", "i8", -128, 127, 1),
    "Int16Argument": ("h", "i16", -32768, 32767, 2),
    "Int32Argument": ("l", "i32", -2147483648, 2147483647, 4),
}
READ = {"u8": "read_u8", "u16": "read_u16", "u32": "read_u32", "i8": "read_i8", "i16": "read_i16",
        "i32": "read_i32"}
WRITE = {"u8": "write_u8", "u16": "write_u16", "u32": "write_u32", "i8": "write_i8", "i16": "write_i16",
         "i32": "write_i32"}


class Prim:
    """ A scalar wire type: float, double, bool or integer. """

    def __init__(self, arg):
        k = type(arg).__name__
        self.kind = k
        if k == "FloatArgument":
            self.rt, self.size, self.fmt = "f64", 4, "f"
        elif k == "DoubleArgument":
            self.rt, self.size, self.fmt = "f64", 8, "d"
        elif k == "BoolArgument":
            self.rt, self.size, self.fmt = "bool", 1, "b"
        elif k in INTS:
            self.fmt, self.rt, self.min, self.max, self.size = INTS[k]
        else:
            raise NotImplementedError(k)

    @property
    def is_int(self):
        return self.kind in INTS

    def read(self):
        if self.kind == "FloatArgument":
            return "f64::from(r.read_f32()?)"
        if self.kind == "DoubleArgument":
            return "r.read_f64()?"
        if self.kind == "BoolArgument":
            return "r.read_bool()?"
        return "r.{}()?".format(READ[self.rt])

    def write(self, expr):
        if self.kind == "FloatArgument":
            return "w.write_f32({})?;".format(expr)
        if self.kind == "DoubleArgument":
            return "w.write_f64({});".format(expr)
        if self.kind == "BoolArgument":
            return "w.write_bool({});".format(expr)
        return "w.{}({});".format(WRITE[self.rt], expr)

    def literal(self, v):
        if self.rt == "f64":
            return repr(float(v)) + "_f64"
        if self.rt == "bool":
            return "true" if v else "false"
        return "{}_{}".format(int(v), self.rt)

    def py_elem(self, name_expr, obj):
        """ Binding: converts a Python element. """
        if self.kind in ("FloatArgument", "DoubleArgument"):
            return "conv::float({}, {})".format(obj, name_expr)
        if self.kind == "BoolArgument":
            return "conv::boolean({})".format(obj)
        return "conv::int({}, {}, {}, {}).map(|x| x as {})".format(obj, name_expr, self.min, self.max, self.rt)


def rust_name(name):
    return "r#" + name if name in ("type", "match", "ref", "move", "loop", "fn", "mod", "use", "self", "in") else name


class Field:
    def __init__(self, arg, ordinal):
        self.arg = arg
        self.name = arg.name
        self.rname = rust_name(arg.name)
        self.k = type(arg).__name__
        self.array_ordinal = None
        if self.k in ("FArrayArgument", "VArrayArgument"):
            if isinstance(arg.data_type, D.Struct):
                self.elem_struct = arg.data_type.name
                self.elem = None
            else:
                self.elem_struct = None
                self.elem = Prim(arg.data_type)
                self.array_ordinal = ordinal
            if self.k == "VArrayArgument":
                self.len_prim = Prim(arg.length_type)
                self.maxlen = {"B": 255, "H": 65535}[self.len_prim.fmt]
        elif self.k == "StringArgument":
            self.len_prim = Prim(arg.length_type)
            self.maxlen = {"B": 255, "H": 65535}[self.len_prim.fmt]
        elif self.k == "EnumArgument":
            self.enum = arg.enum_type.name
            self.prim = Prim(arg.data_type)
        else:
            self.prim = Prim(arg)

    # ---- core

    def rtype(self):
        if self.k in ("FArrayArgument", "VArrayArgument"):
            return "Vec<{}>".format(self.elem_struct or self.elem.rt)
        if self.k == "StringArgument":
            return "String"
        if self.k == "EnumArgument":
            return self.enum
        return self.prim.rt

    def default_expr(self, err="Error::Value"):
        a = self.arg
        if self.k == "FArrayArgument":
            if self.elem_struct:
                items = []
            else:
                items = [self.elem.literal(x) for x in a.default]
            if len(a.default) != a.length:
                return "return Err({}(\"{} must be a sequence of length {}. Got a sequence of length {}.\".into()))"\
                    .format(err, self.name, a.length, len(a.default))
            return "vec![{}]".format(", ".join(items))
        if self.k == "VArrayArgument":
            return "vec![{}]".format(", ".join(self.elem.literal(x) for x in a.default))
        if self.k == "StringArgument":
            return "String::from({})".format(rust_str(a.default))
        if self.k == "EnumArgument":
            return "{}::from_value({})?".format(self.enum, int(a.default))
        return self.prim.literal(a.default)

    def len_expr(self):
        if self.k == "FArrayArgument":
            if self.elem_struct:
                return "self.{}.iter().map(|e| e.encoded_len()).sum::<usize>()".format(self.rname)
            return str(self.arg.length * self.elem.size)
        if self.k == "VArrayArgument":
            if self.elem_struct:
                raise NotImplementedError
            return "{} + self.{}.len() * {}".format(self.len_prim.size, self.rname, self.elem.size)
        if self.k == "StringArgument":
            return "{} + self.{}.len()".format(self.len_prim.size, self.rname)
        return str(self.prim.size)

    def encode(self):
        n = self.rname
        if self.k == "FArrayArgument":
            if self.elem_struct:
                return ("if self.{n}.len() != {L} {{ return Err(Error::Value(\"The given fixed-length sequence has "
                        "the wrong length.\".into())); }}\n        for e in &self.{n} {{ e.encode(w)?; }}").format(
                    n=n, L=self.arg.length)
            return ("check_len(self.{n}.len(), {L})?;\n        for e in &self.{n} {{ {w} }}").format(
                n=n, L=self.arg.length, w=self.elem.write("*e"))
        if self.k == "VArrayArgument":
            return ("w.write_int('{lf}', self.{n}.len() as i64)?;\n        for e in &self.{n} {{ {w} }}").format(
                lf=self.len_prim.fmt, n=n, w=self.elem.write("*e"))
        if self.k == "StringArgument":
            return ("w.write_int('{lf}', self.{n}.len() as i64)?;\n        w.write_bytes(self.{n}.as_bytes());").format(
                lf=self.len_prim.fmt, n=n)
        if self.k == "EnumArgument":
            return "w.write_int('{}', self.{}.value())?;".format(self.prim.fmt, n)
        return self.prim.write("self." + n)

    def decode(self):
        if self.k == "FArrayArgument":
            if self.elem_struct:
                return "(0..{}).map(|_| {}::decode(r)).collect::<Result<Vec<_>>>()?".format(
                    self.arg.length, self.elem_struct)
            return "(0..{}).map(|_| Ok({})).collect::<Result<Vec<_>>>()?".format(self.arg.length, self.elem.read())
        if self.k == "VArrayArgument":
            return "{{ let n = {}; (0..n).map(|_| Ok({})).collect::<Result<Vec<_>>>()? }}".format(
                self.len_prim.read(), self.elem.read())
        if self.k == "StringArgument":
            return "{{ let n = {}; r.read_utf8(n as usize)? }}".format(self.len_prim.read())
        if self.k == "EnumArgument":
            return "{}::from_value(i64::from({}))?".format(self.enum, self.prim.read())
        return self.prim.read()


def rust_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fields_of(struct):
    res = []
    n_arrays = 0
    for a in struct.arguments:
        f = Field(a, n_arrays)
        if f.array_ordinal is not None:
            n_arrays += 1
        res.append(f)
    assert n_arrays <= MAX_ARRAYS, struct.name
    return res


def doc(text):
    return "/// {}\n".format(text.replace("\n", " ")) if text else ""


def gen_core():
    o = []
    w = o.append
    w("// Generated by codegen/gen_rust_protocol.py from pycozmo/protocol_declaration.py. Do not modify.\n")
    w("//\n// Cozmo protocol messages, based on protocol version {}.\n\n".format(D.FIRMWARE_VERSION))
    w("#![allow(non_camel_case_types, clippy::all)]\n\n")
    w("use crate::error::{Error, Result};\nuse crate::protocol::PacketType;\nuse crate::wire::{Reader, Writer};\n\n")
    w("fn check_len(actual: usize, expected: usize) -> Result<()> {\n"
      "    if actual != expected {\n"
      "        return Err(Error::Pack(format!(\"pack expected {expected} items for packing (got {actual})\")));\n"
      "    }\n    Ok(())\n}\n\n")

    # Enums
    for e in D.PROTOCOL.enums:
        seen = {}
        for m in e.members:
            seen.setdefault(m.value, m.name)
        variants = [m for m in e.members if seen[m.value] == m.name]
        w(doc(e.description))
        w("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]\npub enum {} {{\n".format(e.name))
        for m in variants:
            if m.description:
                w("    " + doc(m.description))
            w("    {},\n".format(m.name))
        w("}}\n\nimpl {} {{\n".format(e.name))
        w("    pub const NAME: &'static str = \"{}\";\n".format(e.name))
        w("    /// Members (name, value), aliases included, in declaration order.\n")
        w("    pub const MEMBERS: &'static [(&'static str, i64)] = &[{}];\n".format(
            ", ".join('("{}", {})'.format(m.name, m.value) for m in e.members)))
        w("    /// Numeric value.\n    pub fn value(self) -> i64 {\n        match self {\n")
        for m in variants:
            w("            Self::{} => {},\n".format(m.name, m.value))
        w("        }\n    }\n")
        w("    /// Converts a numeric value.\n    pub fn from_value(v: i64) -> Result<Self> {\n        match v {\n")
        for m in variants:
            w("            {} => Ok(Self::{}),\n".format(m.value, m.name))
        w("            _ => Err(Error::Value(format!(\"{{v}} is not a valid {}\"))),\n".format(e.name))
        w("        }\n    }\n}\n\n")

    w("/// All enumerations: name and members.\n")
    w("pub const ENUMS: &[(&str, &[(&str, i64)])] = &[\n")
    for e in D.PROTOCOL.enums:
        w("    ({0}::NAME, {0}::MEMBERS),\n".format(e.name))
    w("];\n\n")

    for s in D.PROTOCOL.structs + D.PROTOCOL.packets:
        is_packet = isinstance(s, D.Packet)
        fs = fields_of(s)
        w(doc(s.description))
        w("#[derive(Debug, Clone, PartialEq)]\npub struct {} {{\n".format(s.name))
        for f in fs:
            if f.arg.description:
                w("    " + doc(f.arg.description))
            w("    pub {}: {},\n".format(f.rname, f.rtype()))
        w("}}\n\nimpl {} {{\n".format(s.name))
        w("    pub const NAME: &'static str = \"{}\";\n".format(s.name))
        w("    /// Field names, in wire order.\n")
        w("    pub const FIELDS: &'static [&'static str] = &[{}];\n".format(
            ", ".join('"{}"'.format(f.name) for f in fs)))
        if is_packet:
            w("    pub const ID: Option<u8> = {};\n".format(
                "Some(0x{:02x})".format(s.id) if s.id is not None else "None"))
            w("    pub const PACKET_TYPE: PacketType = PacketType::{};\n".format(s.type.name.capitalize()))
        w("    /// The message with the declared default values (fails like PyCozmo when a default is invalid).\n")
        w("    pub fn try_default() -> Result<Self> {\n")
        bad = [f for f in fs if f.default_expr().startswith("return ")]
        if bad:
            w("        " + bad[0].default_expr()[len("return "):] + "\n    }\n")
        else:
            w("        Ok(Self {{ {} }})\n    }}\n".format(
                ", ".join("{}: {}".format(f.rname, f.default_expr()) for f in fs)))
        w("    /// Size of the encoded message.\n    pub fn encoded_len(&self) -> usize {\n")
        w("        {}\n    }}\n".format(" + ".join(f.len_expr() for f in fs) if fs else "0"))
        w("    /// Encodes the message.\n    pub fn encode(&self, w: &mut Writer) -> Result<()> {\n")
        if not fs:
            w("        let _ = w;\n")
        for f in fs:
            w("        {}\n".format(f.encode()))
        w("        Ok(())\n    }\n")
        w("    /// Decodes the message.\n    pub fn decode(r: &mut Reader<'_>) -> Result<Self> {\n")
        if not fs:
            w("        let _ = r;\n")
        # PyCozmo reads all the fields, then converts the enumerations (in the constructor).
        for f in fs:
            if f.k == "EnumArgument":
                w("        let {} = i64::from({});\n".format(f.rname, f.prim.read()))
            else:
                w("        let {} = {};\n".format(f.rname, f.decode()))
        for f in fs:
            if f.k == "EnumArgument":
                w("        let {0} = {1}::from_value({0})?;\n".format(f.rname, f.enum))
        w("        Ok(Self {{ {} }})\n    }}\n}}\n\n".format(", ".join(f.rname for f in fs)))

    pkts = D.PROTOCOL.packets
    w("/// All declared messages.\n#[derive(Debug, Clone, PartialEq)]\npub enum Message {\n")
    for p in pkts:
        w("    {0}({0}),\n".format(p.name))
    w("}\n\nimpl Message {\n")
    for meth, body in (("name(&self) -> &'static str", "{}::NAME"),
                       ("id(&self) -> Option<u8>", "{}::ID"),
                       ("packet_type(&self) -> PacketType", "{}::PACKET_TYPE"),
                       ("fields(&self) -> &'static [&'static str]", "{}::FIELDS")):
        w("    pub fn {} {{\n        match self {{\n".format(meth))
        for p in pkts:
            w("            Message::{}(_) => {},\n".format(p.name, body.format(p.name)))
        w("        }\n    }\n")
    w("    pub fn encoded_len(&self) -> usize {\n        match self {\n")
    for p in pkts:
        w("            Message::{}(m) => m.encoded_len(),\n".format(p.name))
    w("        }\n    }\n")
    w("    pub fn encode(&self, w: &mut Writer) -> Result<()> {\n        match self {\n")
    for p in pkts:
        w("            Message::{}(m) => m.encode(w),\n".format(p.name))
    w("        }\n    }\n")
    w("    /// Decodes the message with a given identifier, or returns `None` if it is not declared.\n")
    w("    pub fn decode_by_id(id: u8, r: &mut Reader<'_>) -> Option<Result<Message>> {\n        Some(match id {\n")
    for p in sorted((p for p in pkts if p.id), key=lambda p: p.id):
        w("            0x{:02x} => {}::decode(r).map(Message::{}),\n".format(p.id, p.name, p.name))
    w("            _ => return None,\n        })\n    }\n}\n\n")

    w("/// Declared messages by identifier.\npub const PACKETS_BY_ID: &[(u8, &str)] = &[\n")
    for p in sorted((p for p in pkts if p.id), key=lambda p: p.id):
        w("    (0x{:02x}, \"{}\"),\n".format(p.id, p.name))
    w("];\n\n")
    groups = {}
    for p in pkts:
        if p.group and p.id:
            groups.setdefault(p.group, []).append(p)
    w("/// Message identifiers by group.\npub const PACKETS_BY_GROUP: &[(&str, &[u8])] = &[\n")
    for g, ps in sorted(groups.items()):
        w("    (\"{}\", &[{}]),\n".format(g, ", ".join("0x{:02x}".format(p.id) for p in sorted(ps, key=lambda p: p.id))))
    w("];\n")
    w("\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n")
    w("    /// Every message whose declared defaults are valid encodes to its length and decodes to itself.\n")
    w("    #[test]\n    fn defaults_round_trip() {\n        let mut n = 0;\n")
    for p in pkts:
        w("        if let Ok(m) = {0}::try_default() {{\n"
          "            let mut w = Writer::new();\n            m.encode(&mut w).unwrap();\n"
          "            assert_eq!(w.len(), m.encoded_len(), \"{0}\");\n"
          "            let b = w.into_bytes();\n"
          "            assert_eq!({0}::decode(&mut Reader::new(&b, 0)).unwrap(), m, \"{0}\");\n"
          "            n += 1;\n        }}\n".format(p.name))
    w("        assert!(n > 100, \"{n}\");\n    }\n\n")
    w("    #[test]\n    fn identifiers_are_unique_and_sorted() {\n")
    w("        assert!(PACKETS_BY_ID.windows(2).all(|p| p[0].0 < p[1].0));\n")
    w("        for (id, name) in PACKETS_BY_ID {\n")
    w("            let msg = Message::decode_by_id(*id, &mut Reader::new(&[0u8; 2048], 0)).unwrap();\n")
    w("            if let Ok(m) = msg {\n                assert_eq!(m.name(), *name);\n                assert_eq!(m.id(), Some(*id));\n            }\n")
    w("        }\n    }\n}\n")
    return "".join(o)


def enum_index(name):
    return [e.name for e in D.PROTOCOL.enums].index(name)


def py_getter(f, src):
    """ Expression converting field `f` of `src` (a reference to the core struct) to a Python object. """
    n = "{}.{}".format(src, f.rname)
    if f.k in ("FArrayArgument", "VArrayArgument"):
        if f.elem_struct:
            return "conv::struct_list(py, &{}, |s| Py{}::wrap(py, s.clone()))".format(n, f.elem_struct)
        return "conv::prim_list(py, &{}, kinds[{}])".format(n, f.array_ordinal)
    if f.k == "StringArgument":
        return "Ok({}.as_str().into_pyobject(py)?.into_any().unbind())".format(n)
    if f.k == "EnumArgument":
        return "Ok(enums::member(py, {}, {}.value()))".format(enum_index(f.enum), n)
    return "Ok({}.into_pyobject(py)?.to_owned().into_any().unbind())".format(n)


def py_setter_value(f, obj):
    """ Statement(s) converting Python object `obj` to the field value; sets `val` (and `kind` for arrays). """
    nm = '"{}"'.format(f.name)
    if f.k in ("FArrayArgument", "VArrayArgument"):
        fixed = "Some({})".format(f.arg.length) if f.k == "FArrayArgument" else "None"
        maxlen = f.maxlen if f.k == "VArrayArgument" else 0
        if f.elem_struct:
            return "let val = conv::struct_seq::<Py{}>({}, {}, {}, {})?;".format(
                f.elem_struct, obj, nm, fixed, maxlen)
        return "let (val, kind) = conv::prim_seq({}, {}, {}, {}, |n, e| {})?;".format(
            obj, nm, fixed, maxlen, f.elem.py_elem("&n", "e"))
    if f.k == "StringArgument":
        return "let val = conv::string({}, {}, {})?;".format(obj, nm, f.maxlen)
    if f.k == "EnumArgument":
        return "let val = {}::from_value(conv::enum_value({}, {}, {}, {})?).map_err(err)?;".format(
            f.enum, obj, nm, f.prim.min, f.prim.max)
    if f.prim.kind in ("FloatArgument", "DoubleArgument"):
        return "let val = conv::float({}, {})?;".format(obj, nm)
    if f.prim.kind == "BoolArgument":
        return "let val = conv::boolean({})?;".format(obj)
    return "let val = conv::int({}, {}, {}, {})? as {};".format(obj, nm, f.prim.min, f.prim.max, f.prim.rt)


def py_init_value(f, obj):
    """ Like py_setter_value, but for the constructor: enums are converted with the enum class first. """
    if f.k == "EnumArgument":
        return "let val = {}::from_value(conv::enum_init(py, {}, {}, \"{}\", {}, {})?).map_err(err)?;".format(
            f.enum, enum_index(f.enum), obj, f.name, f.prim.min, f.prim.max)
    return py_setter_value(f, obj)


def gen_py():
    o = []
    w = o.append
    w("// Generated by codegen/gen_rust_protocol.py from pycozmo/protocol_declaration.py. Do not modify.\n\n")
    w("#![allow(clippy::all, unused_mut, unused_variables)]\n\n")
    w("use pyo3::prelude::*;\nuse pyo3::pybacked::PyBackedBytes;\nuse pyo3::types::{PyDict, PyTuple, PyType};\n\n")
    w("use cozmo_core::protocol::{self as core, Body, Message};\n")
    w("use cozmo_core::protocol::*;\n")
    w("use cozmo_core::wire::{Reader, Writer};\nuse cozmo_core::Error;\n\n")
    w("use crate::conv::{self, ArrKind};\nuse crate::enums;\nuse crate::errors::err;\n")
    w("use crate::protocol::{Packet, Struct, with_reader};\n\n")

    # Structs
    for s in D.PROTOCOL.structs:
        fs = fields_of(s)
        assert all(f.array_ordinal is None for f in fs)
        cls = "Py" + s.name
        w("#[pyclass(extends=Struct, subclass, module=\"pycozmo.protocol_encoder\", name=\"{}\")]\n".format(s.name))
        w("#[derive(Clone)]\npub struct {} {{\n    pub inner: core::{},\n}}\n\n".format(cls, s.name))
        w("impl {} {{\n".format(cls))
        w("    pub fn wrap(py: Python<'_>, inner: core::{}) -> PyResult<PyObject> {{\n".format(s.name))
        w("        Ok(Py::new(py, PyClassInitializer::from(Struct).add_subclass({} {{ inner }}))?.into_any())\n"
          .format(cls))
        w("    }\n}\n\n")
        w("impl conv::Wrapped for {} {{\n    type Inner = core::{};\n    const NAME: &'static str = \"{}\";\n"
          "    fn inner(&self) -> &Self::Inner {{\n        &self.inner\n    }}\n}}\n\n".format(cls, s.name, s.name))
        w("#[pymethods]\nimpl " + cls + " {\n")
        w("    #[new]\n    #[pyo3(signature = (*args, **kwargs))]\n")
        w("    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>)"
          " -> PyResult<(Self, Struct)> {\n")
        w(parse_args(s.name, fs))
        w("        let kinds = [ArrKind::List; 8];\n")
        for f in fs:
            w("        let {0} = match {0} {{ Some(o) => {{ {1} val }} None => {2} }};\n".format(
                f.rname, py_init_value(f, "o"), default_py(f)))
        w("        Ok(({} {{ inner: core::{} {{ {} }} }}, Struct))\n    }}\n".format(
            cls, s.name, ", ".join(f.rname for f in fs)))
        for f in fs:
            w("    #[getter]\n    fn {}(&self, py: Python<'_>) -> PyResult<PyObject> {{\n".format(f.rname))
            w("        let kinds = [ArrKind::List; 8];\n        {}\n    }}\n".format(py_getter(f, "self.inner")))
            w("    #[setter]\n    fn set_{}(&mut self, value: &Bound<'_, PyAny>) -> PyResult<()> {{\n".format(f.name))
            w("        {}\n        self.inner.{} = val;\n        Ok(())\n    }}\n".format(
                py_setter_value(f, "value"), f.rname))
        w("    fn __len__(&self) -> usize {\n        self.inner.encoded_len()\n    }\n")
        w("    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {\n")
        w("        crate::protocol::repr_fields(slf.as_any(), core::{}::FIELDS)\n    }}\n".format(s.name))
        w("    fn to_bytes(&self) -> PyResult<Vec<u8>> {\n        let mut w = Writer::new();\n"
          "        self.inner.encode(&mut w).map_err(err)?;\n        Ok(w.into_bytes())\n    }\n")
        w("    fn __bytes__(&self) -> PyResult<Vec<u8>> {\n        self.to_bytes()\n    }\n")
        w("    #[classmethod]\n    fn from_bytes(_cls: &Bound<'_, PyType>, py: Python<'_>, buffer: PyBackedBytes)"
          " -> PyResult<PyObject> {\n")
        w("        let mut r = Reader::new(&buffer, 0);\n")
        w("        Self::wrap(py, core::{}::decode(&mut r).map_err(err)?)\n    }}\n".format(s.name))
        w("    #[classmethod]\n    fn from_reader(_cls: &Bound<'_, PyType>, py: Python<'_>, reader: &Bound<'_, PyAny>)"
          " -> PyResult<PyObject> {\n")
        w("        let v = with_reader(reader, |r| core::{}::decode(r))?;\n        Self::wrap(py, v)\n    }}\n}}\n\n"
          .format(s.name))

    # Packets
    for p in D.PROTOCOL.packets:
        fs = fields_of(p)
        cls = "Py" + p.name
        w("#[pyclass(extends=Packet, subclass, module=\"pycozmo.protocol_encoder\", name=\"{}\")]\n".format(p.name))
        w("pub struct {};\n\n".format(cls))
        w("#[pymethods]\nimpl {} {{\n".format(cls))
        w("    #[new]\n    #[pyo3(signature = (*args, **kwargs))]\n")
        w("    fn new(py: Python<'_>, args: &Bound<'_, PyTuple>, kwargs: Option<&Bound<'_, PyDict>>)"
          " -> PyResult<PyClassInitializer<Self>> {\n")
        w(parse_args(p.name, fs))
        w("        let mut kinds = [ArrKind::List; 8];\n")
        for f in fs:
            if getattr(f, "elem_struct", None):
                fixed = "Some({})".format(f.arg.length) if f.k == "FArrayArgument" else "None"
                maxlen = f.maxlen if f.k == "VArrayArgument" else 0
                w("        let ({0}, struct_objs) = match {0} {{ Some(o) => {{ let (v, objs) = conv::struct_seq_objs::<Py{1}>"
                  "(o, \"{2}\", {3}, {4})?; (v, Some(objs)) }} None => {5} }};\n".format(
                      f.rname, f.elem_struct, f.name, fixed, maxlen,
                      default_py(f) if default_py(f).startswith("return") else "({}, None)".format(default_py(f))))
                continue
            setk = "kinds[{}] = kind; ".format(f.array_ordinal) if f.array_ordinal is not None else ""
            w("        let {0} = match {0} {{ Some(o) => {{ {1} {2}val }} None => {3} }};\n".format(
                f.rname, py_init_value(f, "o"), setk, default_py(f)))
        w("        let m = core::{} {{ {} }};\n".format(p.name, ", ".join(f.rname for f in fs)))
        w("        let mut pkt = Packet::from_message(Message::{}(m), kinds);\n".format(p.name))
        for f in fs:
            if getattr(f, "elem_struct", None):
                w("        pkt.struct_objs = struct_objs;\n")
        w("        Ok(PyClassInitializer::from(Struct).add_subclass(pkt).add_subclass({}))\n    }}\n".format(cls))
        assert sum(1 for f in fs if getattr(f, "elem_struct", None)) <= 1, p.name
        for f in fs:
            if getattr(f, "elem_struct", None):
                w("    #[getter]\n    fn {}(mut slf: PyRefMut<'_, Self>, py: Python<'_>) -> PyResult<PyObject> {{\n"
                  .format(f.rname))
                w("        let base: &mut Packet = slf.as_mut();\n")
                w("        if base.struct_objs.is_none() {\n")
                w("            let m = match &base.inner.body {{ Body::Message(Message::{}(m)) => m, _ => return Err("
                  "crate::protocol::mismatch()) }};\n".format(p.name))
                w("            let objs = m.{}.iter().map(|s| Py{}::wrap(py, s.clone())).collect::<PyResult<Vec<_>>>()?;\n"
                  .format(f.rname, f.elem_struct))
                w("            base.struct_objs = Some(objs);\n        }\n")
                w("        let objs = base.struct_objs.as_ref().map(|v| v.iter().map(|o| o.clone_ref(py)).collect::<Vec<_>>())"
                  ".unwrap_or_default();\n")
                w("        Ok(pyo3::types::PyList::new(py, objs)?.into_any().unbind())\n    }\n")
                w("    #[setter]\n    fn set_{}(mut slf: PyRefMut<'_, Self>, value: &Bound<'_, PyAny>) -> PyResult<()> {{\n"
                  .format(f.name))
                fixed = "Some({})".format(f.arg.length) if f.k == "FArrayArgument" else "None"
                maxlen = f.maxlen if f.k == "VArrayArgument" else 0
                w("        let (val, objs) = conv::struct_seq_objs::<Py{}>(value, \"{}\", {}, {})?;\n".format(
                    f.elem_struct, f.name, fixed, maxlen))
                w("        let base: &mut Packet = slf.as_mut();\n        base.struct_objs = Some(objs);\n")
                w("        match &mut base.inner.body {{ Body::Message(Message::{}(m)) => m.{} = val, _ => return Err("
                  "crate::protocol::mismatch()) }}\n        Ok(())\n    }}\n".format(p.name, f.rname))
                continue
            w("    #[getter]\n    fn {}(slf: PyRef<'_, Self>, py: Python<'_>) -> PyResult<PyObject> {{\n".format(
                f.rname))
            w("        let base: &Packet = slf.as_ref();\n        let kinds = base.kinds;\n")
            w("        let m = match &base.inner.body {{ Body::Message(Message::{}(m)) => m, _ => return Err("
              "crate::protocol::mismatch()) }};\n".format(p.name))
            w("        {}\n    }}\n".format(py_getter(f, "m")))
            w("    #[setter]\n    fn set_{}(mut slf: PyRefMut<'_, Self>, value: &Bound<'_, PyAny>) -> PyResult<()> {{\n"
              .format(f.name))
            w("        {}\n".format(py_setter_value(f, "value")))
            w("        let base: &mut Packet = slf.as_mut();\n")
            if f.array_ordinal is not None:
                w("        base.kinds[{}] = kind;\n".format(f.array_ordinal))
            w("        match &mut base.inner.body {{ Body::Message(Message::{}(m)) => m.{} = val, _ => return Err("
              "crate::protocol::mismatch()) }}\n        Ok(())\n    }}\n".format(p.name, f.rname))
        w("    #[classmethod]\n    fn from_bytes(_cls: &Bound<'_, PyType>, py: Python<'_>, buffer: PyBackedBytes)"
          " -> PyResult<PyObject> {\n")
        w("        let mut r = Reader::new(&buffer, 0);\n")
        w("        wrap_packet(py, core::Packet::new(Message::{}(core::{}::decode(&mut r).map_err(err)?)))\n    }}\n"
          .format(p.name, p.name))
        w("    #[classmethod]\n    fn from_reader(_cls: &Bound<'_, PyType>, py: Python<'_>, reader: &Bound<'_, PyAny>)"
          " -> PyResult<PyObject> {\n")
        w("        let v = with_reader(reader, |r| core::{}::decode(r))?;\n".format(p.name))
        w("        wrap_packet(py, core::Packet::new(Message::{}(v)))\n    }}\n}}\n\n".format(p.name))

    # Decoded array kinds.
    w("/// Shapes of the arrays of a decoded message, as PyCozmo produces them: `bytes` for byte arrays longer than\n"
      "/// one element, lists otherwise.\n")
    w("pub fn decoded_kinds(msg: &Message) -> [ArrKind; 8] {\n    let mut k = [ArrKind::List; 8];\n    match msg {\n")
    for p in D.PROTOCOL.packets:
        fs = [f for f in fields_of(p) if f.array_ordinal is not None and f.elem.rt == "u8"]
        if not fs:
            continue
        w("        Message::{}(m) => {{\n".format(p.name))
        for f in fs:
            w("            if m.{}.len() > 1 {{ k[{}] = ArrKind::Bytes; }}\n".format(f.rname, f.array_ordinal))
        w("        }\n")
    w("        _ => {}\n    }\n    k\n}\n\n")

    w("/// Copies the values of the Python structures that a packet references into its core message (PyCozmo keeps\n"
      "/// references to the structure objects, so a change to one of them changes the encoded packet).\n")
    w("pub fn sync_structs(py: Python<'_>, msg: &mut Message, objs: &[Py<PyAny>]) -> PyResult<()> {\n    match msg {\n")
    for p in D.PROTOCOL.packets:
        for f in fields_of(p):
            if getattr(f, "elem_struct", None):
                w("        Message::{}(m) => {{ m.{} = objs.iter().map(|o| Ok(o.bind(py).downcast::<Py{}>()?.borrow().inner.clone()))"
                  ".collect::<PyResult<Vec<_>>>()?; }}\n".format(p.name, f.rname, f.elem_struct))
    w("        _ => {}\n    }\n    Ok(())\n}\n\n")

    # Wrapping.
    w("/// Wraps a core packet in the Python class of its message.\n")
    w("pub fn wrap_packet(py: Python<'_>, pkt: core::Packet) -> PyResult<PyObject> {\n")
    w("    let msg = match &pkt.body {\n        Body::Message(m) => m,\n"
      "        Body::Unknown(_) => return crate::protocol::wrap_unknown(py, pkt),\n    };\n")
    w("    let kinds = decoded_kinds(msg);\n    let init = PyClassInitializer::from(Struct);\n    match msg {\n")
    for p in D.PROTOCOL.packets:
        w("        Message::{0}(_) => Ok(Py::new(py, init.add_subclass(Packet::from_packet(pkt, kinds))"
          ".add_subclass(Py{0}))?.into_any()),\n".format(p.name))
    w("    }\n}\n\n")

    # Registration.
    w("/// Adds the generated classes to a module.\n")
    w("pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {\n")
    for s in D.PROTOCOL.structs + D.PROTOCOL.packets:
        w("    m.add_class::<Py{}>()?;\n".format(s.name))
    w("    Ok(())\n}\n")
    return "".join(o)


def parse_args(name, fs):
    """ Constructor arguments: like a Python __init__ with keyword arguments that default to the declared values. """
    o = "        let a = conv::parse_args(\"{}\", args, kwargs, core::{}::FIELDS)?;\n".format(name, name)
    for i, f in enumerate(fs):
        o += "        let {}: Option<&Bound<'_, PyAny>> = a[{}].as_ref();\n".format(f.rname, i)
    return o


def default_py(f):
    """ Default value in the binding constructor: like core try_default(), with Python exceptions. """
    expr = f.default_expr()
    if f.k == "EnumArgument":
        return "{}::from_value({}).map_err(err)?".format(f.enum, int(f.arg.default))
    if expr.startswith("return Err("):
        return "return Err(err(" + expr[len("return Err("):-1] + "))"
    return expr


PY_SHIM = os.path.join(ROOT, "pycozmo", "protocol_encoder.py")


def gen_shim():
    names = [e.name for e in D.PROTOCOL.enums] + [x.name for x in D.PROTOCOL.structs + D.PROTOCOL.packets]
    o = ['"""\n\nCozmo protocol packet encoder classes, based on protocol version {}.\n\n'.format(D.FIRMWARE_VERSION),
         "The classes are implemented in Rust (crates/cozmo-core and crates/cozmo-py) and generated from\n",
         "protocol_declaration.py by codegen/gen_rust_protocol.py . This module re-exports them.\n\n",
         'Do not modify.\n\n"""\n\n',
         "from .protocol_ast import PacketType  # noqa: F401\n",
         "from .protocol_base import Struct, Packet  # noqa: F401\n",
         "from . import _cozmo\n",
         "from ._cozmo import (  # noqa: F401\n"]
    for n in names:
        o.append("    {},\n".format(n))
    o.append(")\n\n\n")
    o.append("PACKETS_BY_ID = {pkt_id: globals()[name] for pkt_id, name in _cozmo.PACKETS_BY_ID}\n\n")
    o.append("PACKETS_BY_GROUP = {group: set(ids) for group, ids in _cozmo.PACKETS_BY_GROUP}\n")
    return "".join(o)


def main():
    with open(PY_SHIM, "w") as f:
        f.write(gen_shim())
    core = gen_core()
    py = gen_py()
    with open(CORE_OUT, "w") as f:
        f.write(core)
    with open(PY_OUT, "w") as f:
        f.write(py)
    rustfmt = shutil.which("rustfmt")
    if rustfmt:
        subprocess.run([rustfmt, "--edition", "2021", "--config-path", os.path.join(ROOT, "rustfmt.toml"),
                        CORE_OUT, PY_OUT], check=True)
    print("Wrote {} ({} lines) and {} ({} lines).".format(
        os.path.relpath(CORE_OUT, ROOT), core.count("\n"), os.path.relpath(PY_OUT, ROOT), py.count("\n")))


if __name__ == "__main__":
    main()
