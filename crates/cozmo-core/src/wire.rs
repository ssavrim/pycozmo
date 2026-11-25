//! Little-endian binary reader and writer of the Cozmo protocol.
//!
//! Equivalent of `BinaryReader` and `BinaryWriter` in `pycozmo/protocol_utils.py`, restricted to the formats that the
//! protocol uses.

use crate::error::{Error, Result};

/// Reads values from a byte buffer, keeping track of the position.
#[derive(Debug, Clone)]
pub struct Reader<'a> {
    buf: &'a [u8],
    pos: usize,
}

macro_rules! read_le {
    ($name:ident, $t:ty) => {
        #[doc = concat!("Reads a little-endian `", stringify!($t), "`.")]
        pub fn $name(&mut self) -> Result<$t> {
            const N: usize = std::mem::size_of::<$t>();
            let bytes = self.take(N)?;
            let mut a = [0u8; N];
            a.copy_from_slice(bytes);
            Ok(<$t>::from_le_bytes(a))
        }
    };
}

impl<'a> Reader<'a> {
    /// Creates a reader at the given offset.
    pub fn new(buf: &'a [u8], pos: usize) -> Self {
        Reader { buf, pos }
    }

    /// The whole buffer.
    pub fn buffer(&self) -> &'a [u8] {
        self.buf
    }

    /// Size of the whole buffer.
    pub fn len(&self) -> usize {
        self.buf.len()
    }

    /// Whether the buffer is empty.
    pub fn is_empty(&self) -> bool {
        self.buf.is_empty()
    }

    /// Current position.
    pub fn tell(&self) -> usize {
        self.pos
    }

    /// Sets the position. Like PyCozmo, a position after the end is accepted; the next read then fails.
    pub fn seek(&mut self, pos: usize) {
        self.pos = pos;
    }

    /// Takes `n` bytes.
    pub fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        match self.pos.checked_add(n) {
            Some(end) if end <= self.buf.len() => {
                let s = &self.buf[self.pos..end];
                self.pos = end;
                Ok(s)
            }
            _ => Err(Error::Index(format!(
                "Buffer not large enough to read serialized message. Received {} bytes.",
                self.buf.len()
            ))),
        }
    }

    read_le!(read_u8, u8);
    read_le!(read_i8, i8);
    read_le!(read_u16, u16);
    read_le!(read_i16, i16);
    read_le!(read_u32, u32);
    read_le!(read_i32, i32);
    read_le!(read_f32, f32);
    read_le!(read_f64, f64);

    /// Reads a boolean stored as a signed byte.
    pub fn read_bool(&mut self) -> Result<bool> {
        Ok(self.read_i8()? != 0)
    }

    /// Reads a string of `len` bytes of UTF-8.
    pub fn read_utf8(&mut self, len: usize) -> Result<String> {
        let bytes = self.take(len)?;
        String::from_utf8(bytes.to_vec()).map_err(|e| Error::Value(format!("'utf-8' codec can't decode bytes: {e}")))
    }
}

/// Accumulates encoded values.
#[derive(Debug, Clone, Default)]
pub struct Writer {
    buf: Vec<u8>,
}

macro_rules! write_le {
    ($name:ident, $t:ty) => {
        #[doc = concat!("Writes a little-endian `", stringify!($t), "`.")]
        pub fn $name(&mut self, v: $t) {
            self.buf.extend_from_slice(&v.to_le_bytes());
        }
    };
}

impl Writer {
    /// Creates an empty writer.
    pub fn new() -> Self {
        Writer { buf: Vec::new() }
    }

    /// Creates an empty writer with a capacity.
    pub fn with_capacity(n: usize) -> Self {
        Writer { buf: Vec::with_capacity(n) }
    }

    /// Number of bytes written.
    pub fn len(&self) -> usize {
        self.buf.len()
    }

    /// Whether nothing was written.
    pub fn is_empty(&self) -> bool {
        self.buf.is_empty()
    }

    /// The written bytes.
    pub fn into_bytes(self) -> Vec<u8> {
        self.buf
    }

    /// The written bytes.
    pub fn as_bytes(&self) -> &[u8] {
        &self.buf
    }

    /// Writes raw bytes.
    pub fn write_bytes(&mut self, b: &[u8]) {
        self.buf.extend_from_slice(b);
    }

    write_le!(write_u8, u8);
    write_le!(write_i8, i8);
    write_le!(write_u16, u16);
    write_le!(write_i16, i16);
    write_le!(write_u32, u32);
    write_le!(write_i32, i32);
    write_le!(write_f64, f64);

    /// Writes a boolean as a signed byte.
    pub fn write_bool(&mut self, v: bool) {
        self.write_i8(v as i8);
    }

    /// Writes a 64-bit float as a 32-bit one, failing like Python's `struct.pack("f")` when it does not fit.
    pub fn write_f32(&mut self, v: f64) -> Result<()> {
        let f = v as f32;
        if f.is_infinite() && v.is_finite() {
            return Err(Error::Overflow("float too large to pack with f format".into()));
        }
        self.buf.extend_from_slice(&f.to_le_bytes());
        Ok(())
    }

    /// Writes an integer with a `struct` format character, checking its range like `struct.pack()`.
    pub fn write_int(&mut self, fmt: char, v: i64) -> Result<()> {
        let (min, max): (i64, i64) = match fmt {
            'B' => (0, 0xff),
            'b' => (-0x80, 0x7f),
            'H' => (0, 0xffff),
            'h' => (-0x8000, 0x7fff),
            'L' => (0, 0xffff_ffff),
            'l' => (-0x8000_0000, 0x7fff_ffff),
            _ => return Err(Error::Pack(format!("bad char in struct format: {fmt}"))),
        };
        if v < min || v > max {
            return Err(Error::Pack(format!("'{fmt}' format requires {min} <= number <= {max}")));
        }
        match fmt {
            'B' => self.write_u8(v as u8),
            'b' => self.write_i8(v as i8),
            'H' => self.write_u16(v as u16),
            'h' => self.write_i16(v as i16),
            'L' => self.write_u32(v as u32),
            _ => self.write_i32(v as i32),
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip() {
        let mut w = Writer::new();
        w.write_u8(1);
        w.write_i16(-2);
        w.write_u32(0xdead_beef);
        w.write_f32(1.5).unwrap();
        w.write_f64(-0.25);
        w.write_bool(true);
        let b = w.into_bytes();
        let mut r = Reader::new(&b, 0);
        assert_eq!(r.read_u8().unwrap(), 1);
        assert_eq!(r.read_i16().unwrap(), -2);
        assert_eq!(r.read_u32().unwrap(), 0xdead_beef);
        assert_eq!(r.read_f32().unwrap(), 1.5);
        assert_eq!(r.read_f64().unwrap(), -0.25);
        assert!(r.read_bool().unwrap());
        assert!(matches!(r.read_u8(), Err(Error::Index(_))));
    }

    #[test]
    fn float_overflow_and_int_range() {
        let mut w = Writer::new();
        assert!(matches!(w.write_f32(1e39), Err(Error::Overflow(_))));
        assert!(w.write_f32(f64::INFINITY).is_ok());
        assert_eq!(w.write_int('B', 256), Err(Error::Pack("'B' format requires 0 <= number <= 255".into())));
        assert!(w.write_int('l', -5).is_ok());
    }

    #[test]
    fn seek_past_end_fails_on_read() {
        let b = [1u8, 2];
        let mut r = Reader::new(&b, 0);
        r.seek(5);
        assert!(r.read_u8().is_err());
    }
}
