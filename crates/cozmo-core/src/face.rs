//! Procedural face rendering (`ProceduralFace.render()` of `pycozmo/procedural_face.py`).
//!
//! PyCozmo draws with Pillow. To produce the same pixels, this module implements the Pillow operations that the
//! renderer uses, for binary images: filled rectangles, chords and pie slices (the ellipse rasterizer of Pillow's
//! `Draw.c`), rotation with nearest neighbour resampling (`Image.rotate()` and the affine transform of
//! `Geometry.c`), nearest neighbour resizing and pasting with a mask.

use crate::error::{Error, Result};

/// Binary image, one byte (0 or 1) per pixel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bitmap {
    pub w: i32,
    pub h: i32,
    pub px: Vec<u8>,
}

impl Bitmap {
    pub fn new(w: i32, h: i32) -> Self {
        Bitmap { w, h, px: vec![0; (w.max(0) as usize) * (h.max(0) as usize)] }
    }

    #[inline]
    fn get(&self, x: i32, y: i32) -> u8 {
        self.px[(y * self.w + x) as usize]
    }

    #[inline]
    fn set(&mut self, x: i32, y: i32, v: u8) {
        let i = (y * self.w + x) as usize;
        self.px[i] = v;
    }

    fn hline(&mut self, mut x0: i32, y0: i32, mut x1: i32) {
        if y0 < 0 || y0 >= self.h {
            return;
        }
        if x0 < 0 {
            x0 = 0;
        } else if x0 >= self.w {
            return;
        }
        if x1 < 0 {
            return;
        } else if x1 >= self.w {
            x1 = self.w - 1;
        }
        for x in x0..=x1 {
            self.set(x, y0, 1);
        }
    }

    /// `ImageDraw.rectangle(xy, fill=1)`.
    pub fn fill_rectangle(&mut self, x0: f64, y0: f64, x1: f64, y1: f64) {
        let (x0, mut y0, x1, mut y1) = (x0 as i32, y0 as i32, x1 as i32, y1 as i32);
        if y0 > y1 {
            std::mem::swap(&mut y0, &mut y1);
        }
        if y0 < 0 {
            y0 = 0;
        } else if y0 >= self.h {
            return;
        }
        if y1 < 0 {
            return;
        } else if y1 > self.h {
            y1 = self.h;
        }
        for y in y0..=y1 {
            self.hline(x0, y, x1);
        }
    }

    /// `ImageDraw.chord(xy, start, end, fill=1)`.
    pub fn fill_chord(&mut self, x0: f64, y0: f64, x1: f64, y1: f64, start: f32, end: f32) {
        let (x0, y0, x1, y1) = (x0 as i32, y0 as i32, x1 as i32, y1 as i32);
        let (al, ar) = normalize_angles(start, end);
        if al + 360.0 == ar {
            self.fill_ellipse(x0, y0, x1, y1);
            return;
        }
        if al == ar {
            return;
        }
        self.clip_ellipse(x0, y0, x1, y1, Shape::Chord, x1 - x0 + y1 - y0 + 1, al, ar);
    }

    /// `ImageDraw.pieslice(xy, start, end, fill=1)`.
    pub fn fill_pieslice(&mut self, x0: f64, y0: f64, x1: f64, y1: f64, start: f32, end: f32) {
        let (x0, y0, x1, y1) = (x0 as i32, y0 as i32, x1 as i32, y1 as i32);
        let (al, ar) = normalize_angles(start, end);
        if al + 360.0 == ar {
            self.fill_ellipse(x0, y0, x1, y1);
            return;
        }
        if al == ar {
            return;
        }
        self.clip_ellipse(x0, y0, x1, y1, Shape::Pie, x1 + y1 - x0 - y0, al, ar);
    }

    fn fill_ellipse(&mut self, x0: i32, y0: i32, x1: i32, y1: i32) {
        let a = x1 - x0;
        let b = y1 - y0;
        if a < 0 || b < 0 {
            return;
        }
        let mut st = EllipseState::new(a, b, a + b);
        while let Some((l, y, r)) = st.next() {
            self.hline(x0 + (l + a) / 2, y0 + (y + b) / 2, x0 + (r + a) / 2);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn clip_ellipse(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, shape: Shape, w: i32, al: f32, ar: f32) {
        let a = x1 - x0;
        let b = y1 - y0;
        if a < 0 || b < 0 {
            return;
        }
        let mut st = EllipseState::new(a, b, w);
        let tree = match shape {
            Shape::Chord => chord_tree(a, b, al, ar),
            Shape::Pie => pie_tree(a, b, al, ar),
        };
        while let Some((l, y, r)) = st.next() {
            let ev = tree.clip(l, y, r);
            for pair in ev.chunks(2) {
                if let [(sx, _), (ex, _)] = pair {
                    self.hline(x0 + (sx + a) / 2, y0 + (y + b) / 2, x0 + (ex + a) / 2);
                }
            }
        }
    }

    /// `Image.rotate(angle, resample=NEAREST, expand=expand)`.
    pub fn rotate(&self, angle: f64, expand: bool) -> Bitmap {
        self.rotated(angle, expand).into_owned()
    }

    /// Like `rotate()`, without copying when the angle is a multiple of 360 degrees.
    pub fn rotated(&self, angle: f64, expand: bool) -> std::borrow::Cow<'_, Bitmap> {
        use std::borrow::Cow;
        let angle = py_mod(angle, 360.0);
        if angle == 0.0 {
            return Cow::Borrowed(self);
        }
        Cow::Owned(self.rotated_owned(angle, expand))
    }

    fn rotated_owned(&self, angle: f64, expand: bool) -> Bitmap {
        let (w, h) = (self.w, self.h);
        if angle == 180.0 {
            return self.transpose(Rotation::R180);
        }
        if (angle == 90.0 || angle == 270.0) && (expand || w == h) {
            return self.transpose(if angle == 90.0 { Rotation::R90 } else { Rotation::R270 });
        }
        let (cx, cy) = (w as f64 / 2.0, h as f64 / 2.0);
        let rad = -(angle * (std::f64::consts::PI / 180.0));
        let mut m = [round15(rad.cos()), round15(rad.sin()), 0.0, round15(-rad.sin()), round15(rad.cos()), 0.0];
        let tr = |m: &[f64; 6], x: f64, y: f64| (m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]);
        let (c, f) = tr(&m, -cx, -cy);
        m[2] = c + cx;
        m[5] = f + cy;
        let (mut nw, mut nh) = (w, h);
        if expand {
            let pts = [(0.0, 0.0), (w as f64, 0.0), (w as f64, h as f64), (0.0, h as f64)];
            let xs: Vec<f64> = pts.iter().map(|(x, y)| tr(&m, *x, *y).0).collect();
            let ys: Vec<f64> = pts.iter().map(|(x, y)| tr(&m, *x, *y).1).collect();
            let max = |v: &[f64]| v.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            let min = |v: &[f64]| v.iter().cloned().fold(f64::INFINITY, f64::min);
            nw = (max(&xs).ceil() - min(&xs).floor()) as i32;
            nh = (max(&ys).ceil() - min(&ys).floor()) as i32;
            let (c, f) = tr(&m, -((nw - w) as f64) / 2.0, -((nh - h) as f64) / 2.0);
            m[2] = c;
            m[5] = f;
        }
        let mut out = Bitmap::new(nw, nh);
        out.affine_from(self, &m);
        out
    }

    fn transpose(&self, r: Rotation) -> Bitmap {
        let (w, h) = (self.w, self.h);
        if let Rotation::R180 = r {
            // A half turn of a row-major image reverses its pixels.
            return Bitmap { w, h, px: self.px.iter().rev().copied().collect() };
        }
        let mut out = match r {
            Rotation::R180 => Bitmap::new(w, h),
            _ => Bitmap::new(h, w),
        };
        for y in 0..h {
            for x in 0..w {
                let v = self.get(x, y);
                match r {
                    Rotation::R90 => out.set(y, w - 1 - x, v),
                    Rotation::R180 => out.set(w - 1 - x, h - 1 - y, v),
                    Rotation::R270 => out.set(h - 1 - y, x, v),
                }
            }
        }
        out
    }

    /// `ImagingTransformAffine()` with nearest neighbour resampling, into `self` (filled with 0 first).
    fn affine_from(&mut self, src: &Bitmap, a: &[f64; 6]) {
        if a[1] == 0.0 && a[3] == 0.0 {
            self.scale_from(src, a);
            return;
        }
        let (x1, y1) = (self.w, self.h);
        let check = |x: i32, y: i32| {
            (x as f64 * a[0] + y as f64 * a[1] + a[2]).abs() < 32768.0
                && (x as f64 * a[3] + y as f64 * a[4] + a[5]).abs() < 32768.0
        };
        if check(0, 0) && check(x1, y1) && check(0, y1) && check(x1, 0) {
            let fix = |v: f64| {
                let t = v * 65536.0 + 0.5;
                (if t < 0.0 { t.floor() } else { t }) as i32 as i64
            };
            let (a0, a1, a3, a4) = (fix(a[0]), fix(a[1]), fix(a[3]), fix(a[4]));
            let mut a2 = fix(a[2] + a[0] * 0.5 + a[1] * 0.5);
            let mut a5 = fix(a[5] + a[3] * 0.5 + a[4] * 0.5);
            for y in 0..y1 {
                let (mut xx, mut yy) = (a2, a5);
                for x in 0..x1 {
                    let xin = (xx >> 16) as i32;
                    if xin >= 0 && xin < src.w {
                        let yin = (yy >> 16) as i32;
                        if yin >= 0 && yin < src.h {
                            let v = src.get(xin, yin);
                            self.set(x, y, v);
                        }
                    }
                    xx += a0;
                    yy += a3;
                }
                a2 += a1;
                a5 += a4;
            }
            return;
        }
        let mut xo = a[2] + a[1] * 0.5 + a[0] * 0.5;
        let mut yo = a[5] + a[4] * 0.5 + a[3] * 0.5;
        for y in 0..y1 {
            let (mut xx, mut yy) = (xo, yo);
            for x in 0..x1 {
                let xin = coord(xx);
                if xin >= 0 && xin < src.w {
                    let yin = coord(yy);
                    if yin >= 0 && yin < src.h {
                        let v = src.get(xin, yin);
                        self.set(x, y, v);
                    }
                }
                xx += a[0];
                yy += a[3];
            }
            xo += a[1];
            yo += a[4];
        }
    }

    /// `ImagingScaleAffine()`.
    fn scale_from(&mut self, src: &Bitmap, a: &[f64; 6]) {
        let (x1, y1) = (self.w, self.h);
        let mut xintab = vec![0i32; x1.max(0) as usize];
        let mut xo = a[2] + a[0] * 0.5;
        let mut yo = a[5] + a[4] * 0.5;
        let (mut xmin, mut xmax) = (x1, 0);
        for x in 0..x1 {
            let xin = coord(xo);
            if xin >= 0 && xin < src.w {
                xmax = x + 1;
                if x < xmin {
                    xmin = x;
                }
                xintab[x as usize] = xin;
            }
            xo += a[0];
        }
        for y in 0..y1 {
            let yi = coord(yo);
            if yi >= 0 && yi < src.h {
                for x in xmin..xmax {
                    let v = src.get(xintab[x as usize], yi);
                    self.set(x, y, v);
                }
            }
            yo += a[4];
        }
    }

    /// `Image.resize((w, h), NEAREST)`. Fails like Pillow for sizes below 1.
    pub fn resize(&self, w: i64, h: i64) -> Result<Bitmap> {
        if w == self.w as i64 && h == self.h as i64 {
            return Ok(self.clone());
        }
        if w < 1 || h < 1 {
            return Err(Error::Value("height and width must be > 0".into()));
        }
        let (w, h) = (w as i32, h as i32);
        // The source box is given to Pillow as 32-bit floats.
        let a = [(self.w as f32) as f64 / w as f64, 0.0, 0.0, 0.0, (self.h as f32) as f64 / h as f64, 0.0];
        let mut out = Bitmap::new(w, h);
        out.scale_from(self, &a);
        Ok(out)
    }

    /// `Image.paste(src, (dx, dy), mask)`: copies the pixels of `src` where `mask` is set (everywhere without mask).
    pub fn paste(&mut self, src: &Bitmap, dx: i64, dy: i64, mask: Option<&Bitmap>) {
        let (mut xsize, mut ysize) = (src.w as i64, src.h as i64);
        let (mut dx0, mut dy0, mut sx0, mut sy0) = (dx, dy, 0i64, 0i64);
        if dx0 < 0 {
            xsize += dx0;
            sx0 = -dx0;
            dx0 = 0;
        }
        if dx0 + xsize > self.w as i64 {
            xsize = self.w as i64 - dx0;
        }
        if dy0 < 0 {
            ysize += dy0;
            sy0 = -dy0;
            dy0 = 0;
        }
        if dy0 + ysize > self.h as i64 {
            ysize = self.h as i64 - dy0;
        }
        if xsize <= 0 || ysize <= 0 {
            return;
        }
        let (xs, sw, dw) = (xsize as usize, src.w as usize, self.w as usize);
        for y in 0..ysize as usize {
            let s0 = (sy0 as usize + y) * sw + sx0 as usize;
            let d0 = (dy0 as usize + y) * dw + dx0 as usize;
            let srow = &src.px[s0..s0 + xs];
            let drow = &mut self.px[d0..d0 + xs];
            match mask {
                None => drow.copy_from_slice(srow),
                Some(m) => {
                    let mrow = &m.px[s0..s0 + xs];
                    for ((d, s), k) in drow.iter_mut().zip(srow).zip(mrow) {
                        if *k != 0 {
                            *d = *s;
                        }
                    }
                }
            }
        }
    }

    /// Like `paste()` of a black image of the size of `mask`, with `mask`: clears the pixels where `mask` is set.
    pub fn clear_where(&mut self, mask: &Bitmap, dx: i64, dy: i64) {
        let (mut xsize, mut ysize) = (mask.w as i64, mask.h as i64);
        let (mut dx0, mut dy0, mut sx0, mut sy0) = (dx, dy, 0i64, 0i64);
        if dx0 < 0 {
            xsize += dx0;
            sx0 = -dx0;
            dx0 = 0;
        }
        if dx0 + xsize > self.w as i64 {
            xsize = self.w as i64 - dx0;
        }
        if dy0 < 0 {
            ysize += dy0;
            sy0 = -dy0;
            dy0 = 0;
        }
        if dy0 + ysize > self.h as i64 {
            ysize = self.h as i64 - dy0;
        }
        if xsize <= 0 || ysize <= 0 {
            return;
        }
        let (xs, mw, dw) = (xsize as usize, mask.w as usize, self.w as usize);
        for y in 0..ysize as usize {
            let s0 = (sy0 as usize + y) * mw + sx0 as usize;
            let d0 = (dy0 as usize + y) * dw + dx0 as usize;
            for (d, k) in self.px[d0..d0 + xs].iter_mut().zip(&mask.px[s0..s0 + xs]) {
                if *k != 0 {
                    *d = 0;
                }
            }
        }
    }

    /// Pixels packed like Pillow's mode "1" (`Image.tobytes()`): rows of bytes, most significant bit first.
    pub fn pack_1bit(&self) -> Vec<u8> {
        let stride = (self.w as usize).div_ceil(8);
        let mut out = vec![0u8; stride * self.h as usize];
        for y in 0..self.h {
            for x in 0..self.w {
                if self.get(x, y) != 0 {
                    out[y as usize * stride + x as usize / 8] |= 0x80 >> (x % 8);
                }
            }
        }
        out
    }
}

#[derive(Clone, Copy)]
enum Rotation {
    R90,
    R180,
    R270,
}

#[derive(Clone, Copy)]
enum Shape {
    Chord,
    Pie,
}

/// `COORD()` of Pillow.
fn coord(v: f64) -> i32 {
    if v < 0.0 {
        -1
    } else {
        v as i32
    }
}

/// Python's `round(x, 15)`, correctly rounded through the decimal representation like CPython.
fn round15(x: f64) -> f64 {
    format!("{x:.15}").parse().unwrap_or(x)
}

/// Python's float `%`.
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r != 0.0 {
        if (m < 0.0) != (r < 0.0) {
            r + m
        } else {
            r
        }
    } else {
        0.0f64.copysign(m)
    }
}

/// `normalize_angles()` of Pillow: 0 <= al < 360, al <= ar <= al + 360.
fn normalize_angles(al: f32, ar: f32) -> (f32, f32) {
    if ar - al >= 360.0 {
        (0.0, 360.0)
    } else {
        let (alf, arf) = (al as f64, ar as f64);
        let al2 = (if alf < 0.0 { 360.0 - ((-alf) % 360.0) } else { alf } % 360.0) as f32;
        let a = al2 as f64;
        let ar2 = (a + if arf < a { 360.0 - ((a - arf) % 360.0) } else { arf - a } % 360.0) as f32;
        (al2, ar2)
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Ellipse rasterization (Pillow's quarter_* and ellipse_* functions).

#[derive(Clone, Default)]
struct Quarter {
    cx: i32,
    cy: i32,
    ex: i32,
    ey: i32,
    a2: i64,
    b2: i64,
    a2b2: i64,
    finished: bool,
}

impl Quarter {
    fn new(a: i32, b: i32) -> Self {
        if a < 0 || b < 0 {
            return Quarter { finished: true, ..Default::default() };
        }
        let (a2, b2) = (a as i64 * a as i64, b as i64 * b as i64);
        Quarter { cx: a, cy: b % 2, ex: a % 2, ey: b, a2, b2, a2b2: a2 * b2, finished: false }
    }

    fn delta(&self, x: i64, y: i64) -> i64 {
        (self.a2 * y * y + self.b2 * x * x - self.a2b2).abs()
    }

    fn next(&mut self) -> Option<(i32, i32)> {
        if self.finished {
            return None;
        }
        let ret = (self.cx, self.cy);
        if self.cx == self.ex && self.cy == self.ey {
            self.finished = true;
        } else {
            let mut nx = self.cx;
            let mut ny = self.cy + 2;
            let mut nd = self.delta(nx as i64, ny as i64);
            if nx > 1 {
                let d = self.delta((self.cx - 2) as i64, (self.cy + 2) as i64);
                if nd > d {
                    nx = self.cx - 2;
                    ny = self.cy + 2;
                    nd = d;
                }
                let d = self.delta((self.cx - 2) as i64, self.cy as i64);
                if nd > d {
                    nx = self.cx - 2;
                    ny = self.cy;
                }
            }
            self.cx = nx;
            self.cy = ny;
        }
        Some(ret)
    }
}

struct EllipseState {
    o: Quarter,
    i: Quarter,
    py: i32,
    pl: i32,
    pr: i32,
    buf: Vec<(i32, i32, i32)>,
    finished: bool,
    leftmost: i32,
}

impl EllipseState {
    fn new(a: i32, b: i32, w: i32) -> Self {
        let leftmost = a % 2;
        let mut o = Quarter::new(a, b);
        let mut s = EllipseState {
            o: Quarter::default(),
            i: Quarter::default(),
            py: 0,
            pl: 0,
            pr: 0,
            buf: Vec::with_capacity(4),
            finished: true,
            leftmost,
        };
        if w >= 1 {
            if let Some((x, y)) = o.next() {
                s.pr = x;
                s.py = y;
                s.finished = false;
                s.i = Quarter::new(a - 2 * (w - 1), b - 2 * (w - 1));
                s.pl = leftmost;
            }
        }
        s.o = o;
        s
    }

    /// Next horizontal segment (x0, y, x1), in the doubled coordinates of Pillow.
    fn next(&mut self) -> Option<(i32, i32, i32)> {
        if self.buf.is_empty() {
            if self.finished {
                return None;
            }
            let y = self.py;
            let mut l = self.pl;
            let r = self.pr;
            let last = loop {
                match self.o.next() {
                    Some((_, cy)) if cy <= y => {}
                    other => break other,
                }
            };
            match last {
                None => self.finished = true,
                Some((cx, cy)) => {
                    self.pr = cx;
                    self.py = cy;
                }
            }
            let inner = loop {
                match self.i.next() {
                    Some((cx, cy)) if cy <= y => l = cx,
                    other => break other,
                }
            };
            self.pl = match inner {
                None => self.leftmost,
                Some((cx, _)) => cx,
            };
            if (l > 0 || l < r) && y > 0 {
                self.buf.push((if l == 0 { 2 } else { l }, y, r));
            }
            if y > 0 {
                self.buf.push((-r, y, -l));
            }
            if l > 0 || l < r {
                self.buf.push((if l == 0 { 2 } else { l }, -y, r));
            }
            self.buf.push((-r, -y, -l));
        }
        self.buf.pop()
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Clipping tree (Pillow's clip_tree_* functions).

enum Clip {
    Half { a: f64, b: f64, c: f64 },
    And(Box<Clip>, Box<Clip>),
    Or(Box<Clip>, Box<Clip>),
}

impl Clip {
    /// Ordered events (x, type) of the clipped segments, type 1 for a start and -1 for an end.
    fn clip(&self, x0: i32, y: i32, x1: i32) -> Vec<(i32, i32)> {
        match self {
            Clip::Half { a, b, c } => {
                let (mut x0, mut x1) = (x0, x1);
                let eps = 1e-9;
                let yf = y as f64;
                if a.abs() < eps {
                    if b * yf + c < -eps {
                        x0 = 1;
                        x1 = 0;
                    }
                } else {
                    let ix = -(b * yf + c) / a;
                    if a * x0 as f64 + b * yf + c < eps {
                        x0 = (x0 as f64).max(ix).round() as i32;
                    }
                    if a * x1 as f64 + b * yf + c < eps {
                        x1 = (x1 as f64).min(ix).round() as i32;
                    }
                }
                if x0 <= x1 {
                    vec![(x0, 1), (x1, -1)]
                } else {
                    vec![]
                }
            }
            Clip::And(l, r) | Clip::Or(l, r) => {
                let is_or = matches!(self, Clip::Or(..));
                let (l1, l2) = (l.clip(x0, y, x1), r.clip(x0, y, x1));
                let (mut i, mut j) = (0, 0);
                let (mut k1, mut k2) = (0, 0);
                let mut out: Vec<(i32, i32)> = Vec::new();
                while i < l1.len() || j < l2.len() {
                    let take1 = j >= l2.len()
                        || (i < l1.len() && (l1[i].0 < l2[j].0 || (l1[i].0 == l2[j].0 && l1[i].1 > l2[j].1)));
                    let t = if take1 {
                        k1 += l1[i].1;
                        i += 1;
                        l1[i - 1]
                    } else {
                        k2 += l2[j].1;
                        j += 1;
                        l2[j - 1]
                    };
                    let tail = out.last().map(|e| e.1);
                    let keep = if is_or {
                        (t.1 == 1 && (tail.is_none() || tail == Some(-1))) || (t.1 == -1 && k1 == 0 && k2 == 0)
                    } else {
                        (t.1 == 1 && (tail.is_none() || tail == Some(-1)) && k1 > 0 && k2 > 0)
                            || (t.1 == -1 && tail == Some(1) && (k1 == 0 || k2 == 0))
                    };
                    if keep {
                        out.push(t);
                    }
                }
                out
            }
        }
    }
}

fn rad(deg: f32) -> f64 {
    deg as f64 * std::f64::consts::PI / 180.0
}

fn chord_tree(a: i32, b: i32, al: f32, ar: f32) -> Clip {
    let (a, b) = (a as f64, b as f64);
    let (xl, xr) = (a * rad(al).cos(), a * rad(ar).cos());
    let (yl, yr) = (b * rad(al).sin(), b * rad(ar).sin());
    let ca = yr - yl;
    let cb = xl - xr;
    Clip::Half { a: ca, b: cb, c: -(ca * xl + cb * yl) }
}

fn pie_tree(a: i32, b: i32, al: f32, ar: f32) -> Clip {
    let (a, b) = (a as f64, b as f64);
    let (xl, xr) = (a * rad(al).cos(), a * rad(ar).cos());
    let (yl, yr) = (b * rad(al).sin(), b * rad(ar).sin());
    let lc = Clip::Half { a: -yl, b: xl, c: 0.0 };
    let rc = Clip::Half { a: yr, b: -xr, c: 0.0 };
    let root =
        if ar - al < 180.0 { Clip::And(Box::new(lc), Box::new(rc)) } else { Clip::Or(Box::new(lc), Box::new(rc)) };
    if ar - al < 90.0 {
        let spike = Clip::Half { a: (xl + xr) / 2.0, b: (yl + yr) / 2.0, c: 0.0 };
        Clip::And(Box::new(root), Box::new(spike))
    } else {
        root
    }
}

// ---------------------------------------------------------------------------------------------------------------------
// Procedural face.

pub const DEFAULT_WIDTH: i32 = 128;
pub const DEFAULT_HEIGHT: i32 = 64;
const DEFAULT_EYE_WIDTH: f64 = 28.0;
const DEFAULT_EYE_HEIGHT: f64 = 40.0;
const X_FACTOR: f64 = 0.55;
const Y_FACTOR: f64 = 0.25;

/// Number of face parameters: 5 for the face and 19 for each eye.
pub const NUM_PARAMS: usize = 5 + 19 + 19;

/// Python's `int()` of a float.
fn py_int(v: f64) -> Result<i64> {
    if v.is_nan() {
        return Err(Error::Value("cannot convert float NaN to integer".into()));
    }
    if v.is_infinite() {
        return Err(Error::Overflow("cannot convert float infinity to integer".into()));
    }
    Ok(v.trunc() as i64)
}

/// Geometry shared by the face, the eyes and the lids (`ProceduralBase`).
#[derive(Debug, Clone, Copy)]
struct Base {
    width: i32,
    height: i32,
    eye_height: f64,
    half_eye_width: f64,
    half_eye_height: f64,
    scale_factor_lid_height: f64,
    scale_factor_lid_bend: f64,
}

impl Base {
    fn new(width: i32, height: i32) -> Self {
        let eye_width = width as f64 * (DEFAULT_EYE_WIDTH / DEFAULT_WIDTH as f64);
        let eye_height = height as f64 * (DEFAULT_EYE_HEIGHT / DEFAULT_HEIGHT as f64);
        Base {
            width,
            height,
            eye_height,
            half_eye_width: eye_width / 2.0,
            half_eye_height: eye_height / 2.0,
            scale_factor_lid_height: 1.2 * eye_width,
            scale_factor_lid_bend: 1.2 * (eye_width / 2.0),
        }
    }
}

/// Per-eye settings that PyCozmo keeps as attributes (`ProceduralEye.x_offset` and `corner_radius`, and the
/// `y_offset` and `angle_offset` of its two lids).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EyeLayout {
    pub x_offset: f64,
    pub corner_radius: f64,
    pub lids: [(f64, f64); 2],
}

impl EyeLayout {
    /// The values that `ProceduralFace.__init__()` gives to the eye at `index` (0 left, 1 right).
    pub fn default_for(index: usize, width: i32, height: i32) -> Self {
        let base = Base::new(width, height);
        let eye_offset = (width as f64 / 5.0).trunc();
        EyeLayout {
            x_offset: if index == 0 { -eye_offset } else { eye_offset },
            corner_radius: width as f64 / 20.0 + height as f64 / 10.0,
            lids: [(-base.half_eye_height, 0.0), (base.half_eye_height + 1.0, 180.0)],
        }
    }
}

fn render_lid(base: &Base, p: &[f64], y_offset: f64, angle_offset: f64, im: &mut Bitmap) -> Result<()> {
    let (y, angle, bend) = (p[0], p[1], p[2]);
    let (w, h) = (base.width as f64, base.height as f64);
    let mut lid = Bitmap::new(base.width * 2, base.height * 2);
    let lid_height = py_int(base.eye_height * y)? as f64;
    let (x1, y1) = (w - base.scale_factor_lid_height, h - 1.0 - base.half_eye_height);
    let (x2, y2) = (w + base.scale_factor_lid_height, h - 1.0 + lid_height);
    if x2 >= x1 && y2 >= y1 {
        lid.fill_rectangle(x1, y1, x2, y2);
    }
    let bend_height = py_int(base.eye_height * (1.0 - y) * bend)? as f64;
    let (x3, y3) = (w - base.scale_factor_lid_bend, h - 1.0 + lid_height - bend_height);
    let (x4, y4) = (w + base.scale_factor_lid_bend, h - 1.0 + lid_height + bend_height);
    if x4 >= x3 && y4 >= y3 {
        lid.fill_chord(x3, y3, x4, y4, 0.0, 180.0);
    }
    let lid = lid.rotated(angle + angle_offset, false);
    let lx = py_int((im.w - lid.w) as f64 / 2.0)?;
    let ly = py_int((im.h - lid.h) as f64 / 2.0 + y_offset)?;
    // Pasting a black image with the lid as mask.
    im.clear_where(&lid, lx, ly);
    Ok(())
}

fn render_eye(base: &Base, p: &[f64], layout: &EyeLayout, im: &mut Bitmap) -> Result<()> {
    let (center_x, center_y, scale_x, scale_y, angle) = (p[0], p[1], p[2], p[3], p[4]);
    let (lir_x, lir_y, lor_x, lor_y) = (p[5], p[6], p[7], p[8]);
    let (uir_x, uir_y, uor_x, uor_y) = (p[9], p[10], p[11], p[12]);
    let cr = layout.corner_radius;
    let mut eye = Bitmap::new(base.width, base.height);
    let x1 = (eye.w / 2) as f64 - base.half_eye_width;
    let y1 = (eye.h / 2) as f64 - base.half_eye_height;
    let x2 = (eye.w / 2) as f64 + base.half_eye_width;
    let y2 = (eye.h / 2) as f64 + base.half_eye_height;
    let t = |v: f64| py_int(cr * v).map(|i| i as f64);

    let rect = |eye: &mut Bitmap, a: f64, b: f64, c: f64, d: f64| {
        if c >= a && d >= b {
            eye.fill_rectangle(a, b, c, d);
        }
    };
    let pie = |eye: &mut Bitmap, a: f64, b: f64, c: f64, d: f64, s: f32, e: f32| {
        if c >= a && d >= b {
            eye.fill_pieslice(a, b, c, d, s, e);
        }
    };
    // Inner, upper, outer, lower and center rectangles.
    rect(&mut eye, x2 - t(uir_x.max(lir_x))?, y1 + t(uir_y)?, x2, y2 - t(lir_y)?);
    rect(&mut eye, x1 + t(uor_x)?, y1, x2 - t(uir_x)?, y1 + t(uor_y.max(uir_y))?);
    rect(&mut eye, x1, y1 + t(uor_y)?, x1 + t(uor_x.max(lor_x))?, y2 - t(lor_y)?);
    rect(&mut eye, x1 + t(lor_x)?, y2 - t(lor_y.max(lir_y))?, x2 - t(lir_x)?, y2);
    rect(
        &mut eye,
        x1 + t(uor_x.max(lor_x))? - 2.0,
        y1 + t(uor_y.max(uir_y))? - 1.0,
        x2 - t(uir_y.max(lir_y))? + 2.0,
        y2 - t(lor_y.max(lir_y))? + 1.0,
    );
    // Corner pies: lower inner, upper inner, upper outer, lower outer.
    pie(&mut eye, x2 - 2.0 * t(lir_x)?, y2 - 2.0 * t(lir_y)?, x2, y2, 0.0, 90.0);
    pie(&mut eye, x2 - 2.0 * t(uir_x)?, y1, x2, y1 + 2.0 * t(uir_y)?, 270.0, 360.0);
    pie(&mut eye, x1, y1, x1 + 2.0 * t(uor_x)?, y1 + 2.0 * t(uor_y)?, 180.0, 270.0);
    pie(&mut eye, x1, y2 - 2.0 * t(lor_y)?, x1 + 2.0 * t(lor_x)?, y2, 90.0, 180.0);

    for (k, (y_offset, angle_offset)) in layout.lids.iter().enumerate() {
        render_lid(base, &p[13 + 3 * k..16 + 3 * k], *y_offset, *angle_offset, &mut eye)?;
    }

    let eye = eye.rotated(angle, true);
    let (nw, nh) = (py_int(eye.w as f64 * scale_x)?, py_int(eye.h as f64 * scale_y)?);
    let eye = if nw == eye.w as i64 && nh == eye.h as i64 {
        eye
    } else {
        match eye.resize(nw, nh) {
            Ok(e) => std::borrow::Cow::Owned(e),
            // Scale factors can be extremely small: nothing is drawn, like in PyCozmo.
            Err(_) => return Ok(()),
        }
    };
    let lx = py_int((im.w - eye.w) as f64 / 2.0 + center_x * X_FACTOR + layout.x_offset)?;
    let ly = py_int((im.h - eye.h) as f64 / 2.0 + center_y * Y_FACTOR)?;
    im.paste(&eye, lx, ly, Some(&eye));
    Ok(())
}

/// Renders a face like `ProceduralFace.render()`.
pub fn render(params: &[f64], width: i32, height: i32, eyes: &[EyeLayout; 2]) -> Result<Bitmap> {
    if params.len() < NUM_PARAMS {
        return Err(Error::Value("Procedural face parameters must be a list of 43 floating point values.".into()));
    }
    let base = Base::new(width, height);
    let im = Bitmap::new(width, height);
    let mut face = Bitmap::new(width, height);
    render_eye(&base, &params[5..24], &eyes[0], &mut face)?;
    render_eye(&base, &params[24..43], &eyes[1], &mut face)?;
    let (center_x, center_y, scale_x, scale_y, angle) = (params[0], params[1], params[2], params[3], params[4]);
    let face = face.rotate(angle, true);
    let face = match face.resize(py_int(face.w as f64 * scale_x)?, py_int(face.h as f64 * scale_y)?) {
        Ok(f) => f,
        Err(_) => return Ok(im),
    };
    let mut im = im;
    let lx = py_int((im.w - face.w) as f64 / 2.0 + center_x * X_FACTOR)?;
    let ly = py_int((im.h - face.h) as f64 / 2.0 + center_y * Y_FACTOR)?;
    im.paste(&face, lx, ly, None);
    Ok(im)
}

/// The default parameters of `ProceduralFace()`.
pub fn default_params() -> Vec<f64> {
    let eye = [0.0, 0.0, 1.0, 1.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
    let mut p = vec![0.0, 0.0, 1.0, 1.0, 0.0];
    p.extend_from_slice(&eye);
    p.extend_from_slice(&eye);
    p
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layouts() -> [EyeLayout; 2] {
        [EyeLayout::default_for(0, 128, 64), EyeLayout::default_for(1, 128, 64)]
    }

    #[test]
    fn default_face_has_two_symmetric_eyes() {
        let im = render(&default_params(), 128, 64, &layouts()).unwrap();
        let lit = im.px.iter().filter(|v| **v != 0).count();
        assert!(lit > 1000, "{lit}");
        // Left half and right half have pixels.
        let left = (0..64).flat_map(|y| (0..64).map(move |x| (x, y))).filter(|(x, y)| im.get(*x, *y) != 0).count();
        let right = lit - left;
        assert!(left > 0 && right > 0);
    }

    #[test]
    fn tiny_scale_renders_nothing() {
        let mut p = default_params();
        p[3] = 0.001;
        let im = render(&p, 128, 64, &layouts()).unwrap();
        assert!(im.px.iter().all(|v| *v == 0));
    }

    #[test]
    fn rotations_and_helpers() {
        let mut b = Bitmap::new(3, 2);
        b.set(0, 0, 1);
        let r = b.rotate(90.0, true);
        assert_eq!((r.w, r.h), (2, 3));
        assert_eq!(r.get(0, 2), 1);
        assert_eq!(py_mod(-30.0, 360.0), 330.0);
        assert_eq!(round15(0.1 + 0.2), 0.3);
        assert_eq!(normalize_angles(270.0, 360.0), (270.0, 360.0));
        assert!(py_int(f64::NAN).is_err());
        assert_eq!(Bitmap::new(9, 1).pack_1bit().len(), 2);
    }
}
