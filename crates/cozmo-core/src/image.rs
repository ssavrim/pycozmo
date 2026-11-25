//! Run-length encoding and decoding of 128x32 display images (`pycozmo/image_encoder.py`).
//!
//! The decoder reproduces the behaviour of PyCozmo on malformed input too: the image is a flat buffer and indices
//! wrap like Python indices, so a malformed sequence fails (or not) at the same point.

use crate::error::{Error, Result};

/// Image width.
pub const WIDTH: usize = 128;
/// Image height.
pub const HEIGHT: usize = 32;

/// State of the decoder (`ImageDecoder`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Decoder {
    pub image: Vec<u8>,
    pub x: i64,
    pub y: i64,
    pub last_draw: bool,
    pub repeat_column_shift: bool,
}

impl Default for Decoder {
    fn default() -> Self {
        Decoder { image: vec![0; WIDTH * HEIGHT], x: 0, y: 0, last_draw: false, repeat_column_shift: false }
    }
}

fn py_index(len: usize, i: i64) -> Result<usize> {
    let j = if i < 0 { i + len as i64 } else { i };
    if j < 0 || j >= len as i64 {
        Err(Error::Index("bytearray index out of range".into()))
    } else {
        Ok(j as usize)
    }
}

impl Decoder {
    /// A decoder with a blank image.
    pub fn new() -> Self {
        Self::default()
    }

    fn draw(&mut self, x: i64, y: i64) -> Result<()> {
        if !(0..WIDTH as i64).contains(&x) || !(0..HEIGHT as i64).contains(&y) {
            return Err(Error::Assertion(String::new()));
        }
        self.image[(y * WIDTH as i64 + x) as usize] = 1;
        Ok(())
    }

    fn draw_or_skip(&mut self, draw: bool, cnt: i64, log: Option<&mut Vec<String>>, ext: &str) -> Result<()> {
        if draw {
            if let Some(l) = log {
                l.push(format!("Draw{ext} {cnt}"));
            }
            for _ in 0..cnt {
                self.draw(self.x, self.y)?;
                self.y += 1;
            }
        } else {
            if let Some(l) = log {
                l.push(format!("Skip{ext} {cnt}"));
            }
            self.y += cnt;
        }
        if self.y > 31 {
            self.repeat_column_shift = true;
            self.x += 1;
            self.y -= 32;
        } else {
            self.repeat_column_shift = false;
        }
        Ok(())
    }

    /// Executes one command byte. `log` receives the debug messages of PyCozmo.
    pub fn execute(&mut self, b: u8, mut log: Option<&mut Vec<String>>) -> Result<()> {
        let cmd = (b & 0xc0) >> 6;
        let mut cnt = i64::from(b & 0x3f);
        match cmd {
            0 => {
                // Skip column
                cnt += 1;
                if let Some(l) = log.as_deref_mut() {
                    l.push(format!("Skip column {cnt}"));
                }
                if self.last_draw {
                    self.x += 1;
                }
                self.x += cnt;
                self.y = 0;
                self.last_draw = false;
                self.repeat_column_shift = false;
            }
            1 => {
                // Repeat column
                cnt += 1;
                if let Some(l) = log.as_deref_mut() {
                    l.push(format!("Repeat column {cnt}"));
                }
                if !self.repeat_column_shift {
                    self.x += 1;
                }
                let len = self.image.len();
                for _ in 0..cnt {
                    let mut i = self.x;
                    for _ in 0..HEIGHT {
                        let src = self.image[py_index(len, i - 1)?];
                        let dst = py_index(len, i)?;
                        self.image[dst] = src;
                        i += WIDTH as i64;
                    }
                    self.x += 1;
                }
                self.y = 0;
                self.last_draw = false;
                self.repeat_column_shift = true;
            }
            _ => {
                // Draw / skip, or their extended variants.
                let draw = cnt & 0x01 != 0;
                cnt >>= 1;
                let draw2 = cnt & 0x01 != 0;
                cnt >>= 1;
                cnt += 1;
                if cmd == 2 {
                    self.draw_or_skip(draw || draw2, cnt, log, "")?;
                    self.last_draw = true;
                } else {
                    self.draw_or_skip(draw || draw2, cnt + 16, log, " extended")?;
                    self.last_draw = false;
                }
            }
        }
        Ok(())
    }

    /// Decodes a sequence. On error, the state is the one reached at the failing command, like in PyCozmo.
    pub fn decode(&mut self, buf: &[u8], mut log: Option<&mut Vec<String>>) -> Result<()> {
        for (i, b) in buf.iter().enumerate() {
            if let Some(l) = log.as_deref_mut() {
                l.push(format!("{i}: "));
            }
            self.execute(*b, log.as_deref_mut())?;
        }
        Ok(())
    }
}

/// Decodes a sequence into a 128x32 image of 0 and 1 values.
pub fn decode(buf: &[u8]) -> Result<Vec<u8>> {
    let mut d = Decoder::new();
    d.decode(buf, None)?;
    Ok(d.image)
}

/// Encoder state (`ImageEncoder`).
struct Encoder<'a> {
    px: &'a [bool],
    buffer: Vec<u8>,
    last_col: Vec<u8>,
    cur_col: Vec<u8>,
    skip_cols: i64,
    repeat_cols: i64,
    x: usize,
    y: usize,
}

impl Encoder<'_> {
    fn px(&self, x: usize, y: usize) -> bool {
        self.px[y * WIDTH + x]
    }

    fn encode_seq(&mut self, color: bool, cnt: u8) -> Result<Option<u8>> {
        let assertion = || Error::Assertion(String::new());
        Ok(if color {
            if cnt <= 15 {
                Some(0x80 + (cnt << 2) + 0x01)
            } else {
                if !(15..=31).contains(&cnt) {
                    return Err(assertion());
                }
                Some(0xc0 + ((cnt - 16) << 2) + 0x01)
            }
        } else if cnt <= 15 {
            Some(0x80 + (cnt << 2))
        } else if cnt < 31 {
            Some(0xc0 + ((cnt - 16) << 2))
        } else {
            if cnt != 31 {
                return Err(assertion());
            }
            self.skip_cols += 1;
            None
        })
    }

    fn count_color(&mut self, color: bool) -> u8 {
        let mut cnt = 0;
        if self.y < HEIGHT {
            while self.px(self.x, self.y) == color {
                cnt += 1;
                self.y += 1;
                if self.y > 31 {
                    self.x += 1;
                    self.y = 0;
                    break;
                }
            }
        } else {
            self.x += 1;
            self.y = 0;
        }
        cnt
    }

    fn drop_trailing_skip(&mut self) {
        if let Some(&cmd) = self.buffer.last() {
            if (cmd & 0xc3 == 0x80) || (cmd & 0xc3 == 0xc0) {
                self.buffer.pop();
            }
        }
    }

    fn flush_skip_cols(&mut self) {
        if self.skip_cols != 0 {
            self.repeat_cols = 0;
            self.last_col.clear();
            // Remove skips right before column skips.
            self.drop_trailing_skip();
        }
        while self.skip_cols >= 64 {
            self.buffer.push(63);
            self.skip_cols -= 64;
        }
        if self.skip_cols != 0 {
            self.buffer.push((self.skip_cols - 1) as u8);
            self.skip_cols = 0;
        }
    }

    fn flush_repeat_cols(&mut self) {
        if self.repeat_cols != 0 {
            // Remove skips right before column repeats.
            self.drop_trailing_skip();
        }
        while self.repeat_cols >= 64 {
            self.buffer.push(0x40 + 0x3f);
            self.repeat_cols -= 64;
        }
        if self.repeat_cols != 0 {
            self.buffer.push((0x40 + self.repeat_cols - 1) as u8);
            self.repeat_cols = 0;
        }
    }

    fn encode(mut self) -> Result<Vec<u8>> {
        while self.x < WIDTH && self.y < HEIGHT {
            let color = self.px(self.x, self.y);
            self.y += 1;
            let cnt = self.count_color(color);
            if let Some(mut cmd) = self.encode_seq(color, cnt)? {
                if self.y == 0 {
                    self.flush_skip_cols();
                    if (cmd & 0xc3 == 0x81) || (cmd & 0xc3 == 0xc1) {
                        cmd += 1;
                    }
                }
                self.cur_col.push(cmd);
            }
            // Column repetition.
            if self.y == 0 {
                if self.skip_cols == 0 {
                    if self.cur_col == self.last_col {
                        self.repeat_cols += 1;
                    } else {
                        self.flush_repeat_cols();
                        self.buffer.extend_from_slice(&self.cur_col);
                        self.last_col = self.cur_col.clone();
                    }
                } else {
                    self.flush_repeat_cols();
                }
                self.cur_col.clear();
            }
        }
        if self.y == 0 {
            self.flush_skip_cols();
            self.flush_repeat_cols();
        }
        Ok(self.buffer)
    }
}

/// Encodes a 128x32 binary image given as one boolean per pixel, row by row.
pub fn encode(pixels: &[bool]) -> Result<Vec<u8>> {
    if pixels.len() != WIDTH * HEIGHT {
        return Err(Error::Value(format!(
            "Invalid image dimensions. Only 128x32 images are supported. {} pixels given.",
            pixels.len()
        )));
    }
    Encoder {
        px: pixels,
        buffer: Vec::new(),
        last_col: Vec::new(),
        cur_col: Vec::new(),
        skip_cols: 0,
        repeat_cols: 0,
        x: 0,
        y: 0,
    }
    .encode()
}

/// Unpacks a 1-bit image as Pillow stores it (`Image.tobytes()` of mode "1": rows of bytes, most significant bit
/// first).
pub fn unpack_1bit(data: &[u8], width: usize, height: usize) -> Result<Vec<bool>> {
    let stride = width.div_ceil(8);
    if data.len() < stride * height {
        return Err(Error::Value("Not enough image data.".into()));
    }
    let mut out = Vec::with_capacity(width * height);
    for y in 0..height {
        for x in 0..width {
            out.push(data[y * stride + x / 8] & (0x80 >> (x % 8)) != 0);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(mut f: impl FnMut(usize, usize) -> bool) -> Vec<bool> {
        (0..WIDTH * HEIGHT).map(|i| f(i % WIDTH, i / WIDTH)).collect()
    }

    fn round_trip(px: &[bool]) {
        let enc = encode(px).unwrap();
        let dec = decode(&enc).unwrap();
        let back: Vec<bool> = dec.iter().map(|v| *v != 0).collect();
        assert_eq!(back, px);
    }

    #[test]
    fn blank_and_full() {
        assert_eq!(encode(&image(|_, _| false)).unwrap(), vec![63, 63]);
        round_trip(&image(|_, _| true));
    }

    #[test]
    fn shapes_round_trip() {
        round_trip(&image(|x, y| x == y));
        round_trip(&image(|x, y| (x / 3 + y / 5) % 2 == 0));
        round_trip(&image(|x, _| x % 7 == 0));
        round_trip(&image(|x, y| x > 100 && y < 4));
        // Random noise is not always encoded losslessly, by PyCozmo either (see parity/image_diff.py).
    }

    #[test]
    fn malformed_input_fails_like_python() {
        // Drawing past the bottom of the last column.
        let mut d = Decoder::new();
        assert!(d.decode(&[63, 63, 0xff, 0xff], None).is_err());
        // Repeating from column 0 reads the last pixel of the image (Python index -1).
        assert!(decode(&[0x40]).is_ok());
    }

    #[test]
    fn unpack() {
        let px = unpack_1bit(&[0x80, 0x01], 16, 1).unwrap();
        assert!(px[0] && px[15] && !px[1]);
    }
}
