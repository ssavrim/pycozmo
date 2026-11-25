//! Reading and writing of Cozmo animation clips in FlatBuffers format (`AnimClips` of `pycozmo/anim_encoder.py`,
//! schema `pycozmo/CozmoAnim/cozmo_anim.fbs`).
//!
//! The builder follows the algorithm of the Python `flatbuffers.Builder` (alignment, back-to-front layout, vtable
//! deduplication) and the order of the calls of `anim_encoder.py`, so that the output is byte for byte the same.

use std::collections::HashMap;

use crate::error::{Error, Result};

/// An animation keyframe.
#[derive(Debug, Clone, PartialEq)]
pub enum Keyframe {
    HeadAngle {
        trigger_time_ms: i64,
        duration_ms: i64,
        angle_deg: i64,
        variability_deg: i64,
    },
    LiftHeight {
        trigger_time_ms: i64,
        duration_ms: i64,
        height_mm: i64,
        variability_mm: i64,
    },
    RecordHeading {
        trigger_time_ms: i64,
    },
    TurnToRecordedHeading {
        trigger_time_ms: i64,
        duration_ms: i64,
        offset_deg: i64,
        speed_deg_per_sec: i64,
        accel_deg_per_sec_2: i64,
        decel_deg_per_sec_2: i64,
        tolerance_deg: i64,
        num_half_revs: i64,
        use_shortest_dir: bool,
    },
    /// `radius_mm` is the text of the radius ("STRAIGHT", "TURN_IN_PLACE", a number...).
    BodyMotion {
        trigger_time_ms: i64,
        duration_ms: i64,
        radius_mm: String,
        speed: i64,
    },
    /// Lights as (red, green, blue, ir), in the order left, front, middle, back, right.
    BackpackLights {
        trigger_time_ms: i64,
        duration_ms: i64,
        lights: [[f64; 4]; 5],
    },
    FaceAnimation {
        trigger_time_ms: i64,
        anim_name: String,
    },
    ProceduralFace {
        trigger_time_ms: i64,
        angle: f64,
        center_x: f64,
        center_y: f64,
        scale_x: f64,
        scale_y: f64,
        left_eye: Vec<f64>,
        right_eye: Vec<f64>,
    },
    RobotAudio {
        trigger_time_ms: i64,
        audio_event_ids: Vec<i64>,
        volume: f64,
        probabilities: Vec<f64>,
        has_alts: bool,
    },
    Event {
        trigger_time_ms: i64,
        event_id: String,
    },
}

/// Keyframe kinds, in the order of the `Keyframes` vectors built by `AnimClip.to_fb()`.
const VECTOR_ORDER: [usize; 10] = [0, 1, 2, 3, 5, 4, 6, 7, 8, 9];

impl Keyframe {
    /// Index of the kind in the order that `AnimClip.from_fb()` reads them.
    pub fn kind(&self) -> usize {
        match self {
            Keyframe::HeadAngle { .. } => 0,
            Keyframe::LiftHeight { .. } => 1,
            Keyframe::RecordHeading { .. } => 2,
            Keyframe::TurnToRecordedHeading { .. } => 3,
            Keyframe::BodyMotion { .. } => 4,
            Keyframe::BackpackLights { .. } => 5,
            Keyframe::FaceAnimation { .. } => 6,
            Keyframe::ProceduralFace { .. } => 7,
            Keyframe::RobotAudio { .. } => 8,
            Keyframe::Event { .. } => 9,
        }
    }
}

/// An animation clip.
#[derive(Debug, Clone, PartialEq)]
pub struct Clip {
    pub name: String,
    pub keyframes: Vec<Keyframe>,
}

// ---------------------------------------------------------------------------------------------------------------------
// Builder

/// FlatBuffers builder with the behaviour of the Python implementation.
pub struct Builder {
    bytes: Vec<u8>,
    head: usize,
    minalign: usize,
    current_vtable: Vec<usize>,
    object_end: usize,
    vtables: HashMap<Vec<usize>, usize>,
    vector_num_elems: usize,
}

fn bad_number(n: impl std::fmt::Display, t: &str) -> Error {
    Error::Type(format!("bad number {n} for type {t}"))
}

fn check(v: i64, min: i64, max: i64, t: &str) -> Result<i64> {
    if v < min || v > max {
        Err(bad_number(v, t))
    } else {
        Ok(v)
    }
}

fn f32_of(v: f64) -> Result<f32> {
    let f = v as f32;
    if f.is_infinite() && v.is_finite() {
        return Err(Error::Overflow("float too large to pack with f format".into()));
    }
    Ok(f)
}

impl Builder {
    pub fn new(initial_size: usize) -> Self {
        Builder {
            bytes: vec![0; initial_size],
            head: initial_size,
            minalign: 1,
            current_vtable: Vec::new(),
            object_end: 0,
            vtables: HashMap::new(),
            vector_num_elems: 0,
        }
    }

    fn offset(&self) -> usize {
        self.bytes.len() - self.head
    }

    fn grow(&mut self) {
        let old = self.bytes.len();
        let new_size = (old * 2).max(1);
        let mut b = vec![0; new_size];
        b[new_size - old..].copy_from_slice(&self.bytes);
        self.bytes = b;
    }

    fn pad(&mut self, n: usize) {
        for _ in 0..n {
            self.head -= 1;
            self.bytes[self.head] = 0;
        }
    }

    fn prep(&mut self, size: usize, additional: usize) {
        if size > self.minalign {
            self.minalign = size;
        }
        let align_size = (!(self.bytes.len() - self.head + additional)).wrapping_add(1) & (size - 1);
        let needed = align_size + size + additional;
        while self.head < needed {
            let old = self.bytes.len();
            self.grow();
            self.head += self.bytes.len() - old;
        }
        self.pad(align_size);
    }

    fn place(&mut self, b: &[u8]) {
        self.head -= b.len();
        self.bytes[self.head..self.head + b.len()].copy_from_slice(b);
    }

    fn prepend(&mut self, b: &[u8]) {
        self.prep(b.len(), 0);
        self.place(b);
    }

    pub fn prepend_f32(&mut self, v: f64) -> Result<()> {
        self.prepend(&f32_of(v)?.to_le_bytes());
        Ok(())
    }

    pub fn prepend_i64(&mut self, v: i64) {
        self.prepend(&v.to_le_bytes());
    }

    pub fn prepend_uoffset(&mut self, off: usize) -> Result<()> {
        self.prep(4, 0);
        if off > self.offset() {
            return Err(Error::Value("flatbuffers: Offset arithmetic error.".into()));
        }
        let off2 = (self.offset() - off + 4) as u32;
        self.place(&off2.to_le_bytes());
        Ok(())
    }

    pub fn start_object(&mut self, numfields: usize) {
        self.current_vtable = vec![0; numfields];
        self.object_end = self.offset();
    }

    fn slot(&mut self, o: usize) {
        self.current_vtable[o] = self.offset();
    }

    /// Scalar slot: written only when different from the default, like `PrependSlot()`.
    fn scalar_slot(&mut self, o: usize, bytes: &[u8], is_default: bool) {
        if !is_default {
            self.prepend(bytes);
            self.slot(o);
        }
    }

    pub fn u32_slot(&mut self, o: usize, v: i64, d: i64) -> Result<()> {
        let v = check(v, 0, u32::MAX as i64, "uint32")?;
        self.scalar_slot(o, &(v as u32).to_le_bytes(), v == d);
        Ok(())
    }

    pub fn u16_slot(&mut self, o: usize, v: i64, d: i64) -> Result<()> {
        let v = check(v, 0, u16::MAX as i64, "uint16")?;
        self.scalar_slot(o, &(v as u16).to_le_bytes(), v == d);
        Ok(())
    }

    pub fn i16_slot(&mut self, o: usize, v: i64, d: i64) -> Result<()> {
        let v = check(v, i16::MIN as i64, i16::MAX as i64, "int16")?;
        self.scalar_slot(o, &(v as i16).to_le_bytes(), v == d);
        Ok(())
    }

    pub fn u8_slot(&mut self, o: usize, v: i64, d: i64) -> Result<()> {
        let v = check(v, 0, u8::MAX as i64, "uint8")?;
        self.scalar_slot(o, &[v as u8], v == d);
        Ok(())
    }

    pub fn i8_slot(&mut self, o: usize, v: i64, d: i64) -> Result<()> {
        let v = check(v, i8::MIN as i64, i8::MAX as i64, "int8")?;
        self.scalar_slot(o, &(v as i8).to_le_bytes(), v == d);
        Ok(())
    }

    pub fn bool_slot(&mut self, o: usize, v: bool, d: bool) {
        self.scalar_slot(o, &[v as u8], v == d);
    }

    pub fn f32_slot(&mut self, o: usize, v: f64, d: f64) -> Result<()> {
        let f = f32_of(v)?;
        self.scalar_slot(o, &f.to_le_bytes(), v == d);
        Ok(())
    }

    pub fn uoffset_slot(&mut self, o: usize, x: usize) -> Result<()> {
        if x != 0 {
            self.prepend_uoffset(x)?;
            self.slot(o);
        }
        Ok(())
    }

    pub fn end_object(&mut self) -> usize {
        // Offset to the vtable, written below.
        self.prep(4, 0);
        self.place(&0i32.to_le_bytes());
        let object_offset = self.offset();

        let mut key = Vec::new();
        let mut trim = true;
        for &elem in self.current_vtable.iter().rev() {
            if elem == 0 {
                if trim {
                    continue;
                }
                key.push(0);
            } else {
                key.push(object_offset - elem);
                trim = false;
            }
        }
        let object_size = object_offset - self.object_end;
        key.push(object_size);

        match self.vtables.get(&key) {
            None => {
                let mut trailing = 0;
                let mut trim = true;
                let vt = std::mem::take(&mut self.current_vtable);
                for &elem in vt.iter().rev() {
                    let off = if elem == 0 {
                        if trim {
                            trailing += 1;
                            continue;
                        }
                        0
                    } else {
                        trim = false;
                        object_offset - elem
                    };
                    self.prepend(&(off as u16).to_le_bytes());
                }
                self.prepend(&(object_size as u16).to_le_bytes());
                let vbytes = (vt.len() - trailing + 2) * 2;
                self.prepend(&(vbytes as u16).to_le_bytes());
                let object_start = self.bytes.len() - object_offset;
                let rel = (self.offset() as i64 - object_offset as i64) as i32;
                self.bytes[object_start..object_start + 4].copy_from_slice(&rel.to_le_bytes());
                self.vtables.insert(key, self.offset());
            }
            Some(&vt2) => {
                let object_start = self.bytes.len() - object_offset;
                self.head = object_start;
                let rel = (vt2 as i64 - object_offset as i64) as i32;
                self.bytes[self.head..self.head + 4].copy_from_slice(&rel.to_le_bytes());
                self.current_vtable.clear();
            }
        }
        object_offset
    }

    pub fn start_vector(&mut self, elem_size: usize, num: usize, alignment: usize) {
        self.vector_num_elems = num;
        self.prep(4, elem_size * num);
        self.prep(alignment, elem_size * num);
    }

    pub fn end_vector(&mut self) -> usize {
        let n = self.vector_num_elems as u32;
        self.place(&n.to_le_bytes());
        self.offset()
    }

    pub fn create_string(&mut self, s: &str) -> usize {
        let x = s.as_bytes();
        self.prep(4, x.len() + 1);
        self.place(&[0]);
        self.place(x);
        self.vector_num_elems = x.len();
        self.end_vector()
    }

    fn offsets_vector(&mut self, offsets: &[usize]) -> Result<usize> {
        self.start_vector(4, offsets.len(), 4);
        for &o in offsets.iter().rev() {
            self.prepend_uoffset(o)?;
        }
        Ok(self.end_vector())
    }

    fn f32_vector(&mut self, values: &[f64]) -> Result<usize> {
        self.start_vector(4, values.len(), 4);
        for &v in values.iter().rev() {
            self.prepend_f32(v)?;
        }
        Ok(self.end_vector())
    }

    pub fn finish(mut self, root: usize) -> Result<Vec<u8>> {
        self.prep(self.minalign, 4);
        self.prepend_uoffset(root)?;
        Ok(self.bytes.split_off(self.head))
    }
}

fn write_keyframe(b: &mut Builder, kf: &Keyframe) -> Result<usize> {
    Ok(match kf {
        Keyframe::HeadAngle { trigger_time_ms, duration_ms, angle_deg, variability_deg } => {
            b.start_object(4);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.u32_slot(1, *duration_ms, 0)?;
            b.i8_slot(2, *angle_deg, 0)?;
            b.u8_slot(3, *variability_deg, 0)?;
            b.end_object()
        }
        Keyframe::LiftHeight { trigger_time_ms, duration_ms, height_mm, variability_mm } => {
            b.start_object(4);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.u32_slot(1, *duration_ms, 0)?;
            b.u8_slot(2, *height_mm, 0)?;
            b.u8_slot(3, *variability_mm, 0)?;
            b.end_object()
        }
        Keyframe::RecordHeading { trigger_time_ms } => {
            b.start_object(1);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.end_object()
        }
        Keyframe::TurnToRecordedHeading {
            trigger_time_ms,
            duration_ms,
            offset_deg,
            speed_deg_per_sec,
            accel_deg_per_sec_2,
            decel_deg_per_sec_2,
            tolerance_deg,
            num_half_revs,
            use_shortest_dir,
        } => {
            b.start_object(9);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.u32_slot(1, *duration_ms, 0)?;
            b.i16_slot(2, *offset_deg, 0)?;
            b.i16_slot(3, *speed_deg_per_sec, 0)?;
            b.i16_slot(4, *accel_deg_per_sec_2, 1000)?;
            b.i16_slot(5, *decel_deg_per_sec_2, 1000)?;
            b.u16_slot(6, *tolerance_deg, 2)?;
            b.u16_slot(7, *num_half_revs, 0)?;
            b.bool_slot(8, *use_shortest_dir, false);
            b.end_object()
        }
        Keyframe::BodyMotion { trigger_time_ms, duration_ms, radius_mm, speed } => {
            let s = b.create_string(radius_mm);
            b.start_object(4);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.u32_slot(1, *duration_ms, 0)?;
            b.uoffset_slot(2, s)?;
            b.i16_slot(3, *speed, 0)?;
            b.end_object()
        }
        Keyframe::BackpackLights { trigger_time_ms, duration_ms, lights } => {
            let mut v = [0usize; 5];
            for (i, l) in lights.iter().enumerate() {
                v[i] = b.f32_vector(l)?;
            }
            b.start_object(7);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.u32_slot(1, *duration_ms, 0)?;
            // Slots: Left 2, Right 3, Front 4, Middle 5, Back 6; added in the order left, front, middle, back, right.
            b.uoffset_slot(2, v[0])?;
            b.uoffset_slot(4, v[1])?;
            b.uoffset_slot(5, v[2])?;
            b.uoffset_slot(6, v[3])?;
            b.uoffset_slot(3, v[4])?;
            b.end_object()
        }
        Keyframe::FaceAnimation { trigger_time_ms, anim_name } => {
            let s = b.create_string(anim_name);
            b.start_object(2);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.uoffset_slot(1, s)?;
            b.end_object()
        }
        Keyframe::ProceduralFace {
            trigger_time_ms,
            angle,
            center_x,
            center_y,
            scale_x,
            scale_y,
            left_eye,
            right_eye,
        } => {
            let mut eyes = [0usize; 2];
            for (i, eye) in [left_eye, right_eye].iter().enumerate() {
                if eye.len() < 19 {
                    return Err(Error::Index("list index out of range".into()));
                }
                eyes[i] = b.f32_vector(&eye[..19])?;
            }
            b.start_object(8);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.f32_slot(1, *angle, 0.0)?;
            b.f32_slot(2, *center_x, 0.0)?;
            b.f32_slot(3, *center_y, 0.0)?;
            b.f32_slot(4, *scale_x, 1.0)?;
            b.f32_slot(5, *scale_y, 1.0)?;
            b.uoffset_slot(6, eyes[0])?;
            b.uoffset_slot(7, eyes[1])?;
            b.end_object()
        }
        Keyframe::RobotAudio { trigger_time_ms, audio_event_ids, volume, probabilities, has_alts } => {
            b.start_vector(8, audio_event_ids.len(), 8);
            for &id in audio_event_ids.iter().rev() {
                b.prepend_i64(id);
            }
            let ids = b.end_vector();
            let probs = b.f32_vector(probabilities)?;
            b.start_object(5);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.uoffset_slot(1, ids)?;
            b.f32_slot(2, *volume, 1.0)?;
            b.uoffset_slot(3, probs)?;
            b.bool_slot(4, *has_alts, true);
            b.end_object()
        }
        Keyframe::Event { trigger_time_ms, event_id } => {
            let s = b.create_string(event_id);
            b.start_object(2);
            b.u32_slot(0, *trigger_time_ms, 0)?;
            b.uoffset_slot(1, s)?;
            b.end_object()
        }
    })
}

fn write_clip(b: &mut Builder, clip: &Clip) -> Result<usize> {
    let mut by_kind: [Vec<usize>; 10] = Default::default();
    for kf in &clip.keyframes {
        let o = write_keyframe(b, kf)?;
        by_kind[kf.kind()].push(o);
    }
    let mut vectors = [0usize; 10];
    for k in VECTOR_ORDER {
        vectors[k] = b.offsets_vector(&by_kind[k])?;
    }
    // Keyframes slots: LiftHeight 0, ProceduralFace 1, HeadAngle 2, RobotAudio 3, BackpackLights 4,
    // FaceAnimation 5, Event 6, BodyMotion 7, RecordHeading 8, TurnToRecordedHeading 9.
    b.start_object(10);
    for (slot, kind) in [(2, 0), (0, 1), (8, 2), (9, 3), (7, 4), (4, 5), (5, 6), (1, 7), (3, 8), (6, 9)] {
        b.uoffset_slot(slot, vectors[kind])?;
    }
    let kfs = b.end_object();
    let name = b.create_string(&clip.name);
    b.start_object(2);
    b.uoffset_slot(0, name)?;
    b.uoffset_slot(1, kfs)?;
    Ok(b.end_object())
}

/// Encodes clips like `AnimClips.to_fb_stream()`.
pub fn encode_clips(clips: &[Clip]) -> Result<Vec<u8>> {
    let mut b = Builder::new(1024);
    let offsets = clips.iter().map(|c| write_clip(&mut b, c)).collect::<Result<Vec<_>>>()?;
    let v = b.offsets_vector(&offsets)?;
    b.start_object(1);
    b.uoffset_slot(0, v)?;
    let root = b.end_object();
    b.finish(root)
}

// ---------------------------------------------------------------------------------------------------------------------
// Reader

#[derive(Clone, Copy)]
struct Table<'a> {
    buf: &'a [u8],
    pos: usize,
    vtable: usize,
    vtsize: usize,
}

fn malformed() -> Error {
    Error::Value("Malformed animation data.".into())
}

fn rd<const N: usize>(buf: &[u8], pos: usize) -> Result<[u8; N]> {
    buf.get(pos..pos.checked_add(N).ok_or_else(malformed)?)
        .ok_or_else(malformed)
        .map(|s| s.try_into().unwrap_or([0; N]))
}

fn u32_at(buf: &[u8], pos: usize) -> Result<usize> {
    Ok(u32::from_le_bytes(rd::<4>(buf, pos)?) as usize)
}

impl<'a> Table<'a> {
    fn at(buf: &'a [u8], pos: usize) -> Result<Self> {
        let soff = i32::from_le_bytes(rd::<4>(buf, pos)?) as i64;
        let vtable = usize::try_from(pos as i64 - soff).map_err(|_| malformed())?;
        let vtsize = u16::from_le_bytes(rd::<2>(buf, vtable)?) as usize;
        Ok(Table { buf, pos, vtable, vtsize })
    }

    /// Offset of the field in the table, or 0 if absent.
    fn field(&self, slot: usize) -> Result<usize> {
        let vo = 4 + 2 * slot;
        if vo < self.vtsize {
            Ok(u16::from_le_bytes(rd::<2>(self.buf, self.vtable + vo)?) as usize)
        } else {
            Ok(0)
        }
    }

    fn scalar<const N: usize>(&self, slot: usize) -> Result<Option<[u8; N]>> {
        match self.field(slot)? {
            0 => Ok(None),
            o => rd::<N>(self.buf, self.pos + o).map(Some),
        }
    }

    fn u32(&self, slot: usize, d: i64) -> Result<i64> {
        Ok(self.scalar::<4>(slot)?.map(|b| u32::from_le_bytes(b) as i64).unwrap_or(d))
    }
    fn u16(&self, slot: usize, d: i64) -> Result<i64> {
        Ok(self.scalar::<2>(slot)?.map(|b| u16::from_le_bytes(b) as i64).unwrap_or(d))
    }
    fn i16(&self, slot: usize, d: i64) -> Result<i64> {
        Ok(self.scalar::<2>(slot)?.map(|b| i16::from_le_bytes(b) as i64).unwrap_or(d))
    }
    fn u8(&self, slot: usize, d: i64) -> Result<i64> {
        Ok(self.scalar::<1>(slot)?.map(|b| b[0] as i64).unwrap_or(d))
    }
    fn i8(&self, slot: usize, d: i64) -> Result<i64> {
        Ok(self.scalar::<1>(slot)?.map(|b| b[0] as i8 as i64).unwrap_or(d))
    }
    fn boolean(&self, slot: usize, d: bool) -> Result<bool> {
        Ok(self.scalar::<1>(slot)?.map(|b| b[0] != 0).unwrap_or(d))
    }
    fn f32(&self, slot: usize, d: f64) -> Result<f64> {
        Ok(self.scalar::<4>(slot)?.map(|b| f32::from_le_bytes(b) as f64).unwrap_or(d))
    }

    /// Position and length of a vector field.
    fn vector(&self, slot: usize) -> Result<Option<(usize, usize)>> {
        match self.field(slot)? {
            0 => Ok(None),
            o => {
                let p = self.pos + o;
                let v = p + u32_at(self.buf, p)?;
                Ok(Some((v + 4, u32_at(self.buf, v)?)))
            }
        }
    }

    fn string(&self, slot: usize) -> Result<Option<String>> {
        match self.vector(slot)? {
            None => Ok(None),
            Some((start, n)) => {
                let b = self.buf.get(start..start + n).ok_or_else(malformed)?;
                String::from_utf8(b.to_vec()).map(Some).map_err(|e| Error::Value(format!("invalid UTF-8: {e}")))
            }
        }
    }

    fn required_string(&self, slot: usize, name: &str) -> Result<String> {
        self.string(slot)?.ok_or_else(|| Error::Value(format!("Missing {name}.")))
    }

    fn f32s(&self, slot: usize) -> Result<Vec<f64>> {
        match self.vector(slot)? {
            None => Ok(Vec::new()),
            Some((start, n)) => {
                (0..n).map(|i| Ok(f32::from_le_bytes(rd::<4>(self.buf, start + 4 * i)?) as f64)).collect()
            }
        }
    }

    fn i64s(&self, slot: usize) -> Result<Vec<i64>> {
        match self.vector(slot)? {
            None => Ok(Vec::new()),
            Some((start, n)) => (0..n).map(|i| Ok(i64::from_le_bytes(rd::<8>(self.buf, start + 8 * i)?))).collect(),
        }
    }

    fn tables(&self, slot: usize) -> Result<Vec<Table<'a>>> {
        match self.vector(slot)? {
            None => Ok(Vec::new()),
            Some((start, n)) => (0..n)
                .map(|i| {
                    let p = start + 4 * i;
                    Table::at(self.buf, p + u32_at(self.buf, p)?)
                })
                .collect(),
        }
    }

    fn table(&self, slot: usize) -> Result<Option<Table<'a>>> {
        match self.field(slot)? {
            0 => Ok(None),
            o => {
                let p = self.pos + o;
                Table::at(self.buf, p + u32_at(self.buf, p)?).map(Some)
            }
        }
    }
}

fn read_keyframe(kind: usize, t: &Table<'_>) -> Result<Keyframe> {
    Ok(match kind {
        0 => Keyframe::HeadAngle {
            trigger_time_ms: t.u32(0, 0)?,
            duration_ms: t.u32(1, 0)?,
            angle_deg: t.i8(2, 0)?,
            variability_deg: t.u8(3, 0)?,
        },
        1 => Keyframe::LiftHeight {
            trigger_time_ms: t.u32(0, 0)?,
            duration_ms: t.u32(1, 0)?,
            height_mm: t.u8(2, 0)?,
            variability_mm: t.u8(3, 0)?,
        },
        2 => Keyframe::RecordHeading { trigger_time_ms: t.u32(0, 0)? },
        3 => Keyframe::TurnToRecordedHeading {
            trigger_time_ms: t.u32(0, 0)?,
            duration_ms: t.u32(1, 0)?,
            offset_deg: t.i16(2, 0)?,
            speed_deg_per_sec: t.i16(3, 0)?,
            accel_deg_per_sec_2: t.i16(4, 1000)?,
            decel_deg_per_sec_2: t.i16(5, 1000)?,
            tolerance_deg: t.u16(6, 2)?,
            num_half_revs: t.u16(7, 0)?,
            use_shortest_dir: t.boolean(8, false)?,
        },
        4 => Keyframe::BodyMotion {
            trigger_time_ms: t.u32(0, 0)?,
            duration_ms: t.u32(1, 0)?,
            radius_mm: t.required_string(2, "radius_mm")?,
            speed: t.i16(3, 0)?,
        },
        5 => {
            let mut lights = [[0.0; 4]; 5];
            // left, front, middle, back, right
            for (i, slot) in [2usize, 4, 5, 6, 3].iter().enumerate() {
                let v = t.f32s(*slot)?;
                if v.len() != 4 {
                    return Err(Error::Assertion(String::new()));
                }
                lights[i].copy_from_slice(&v);
            }
            Keyframe::BackpackLights { trigger_time_ms: t.u32(0, 0)?, duration_ms: t.u32(1, 0)?, lights }
        }
        6 => Keyframe::FaceAnimation { trigger_time_ms: t.u32(0, 0)?, anim_name: t.required_string(1, "animName")? },
        7 => {
            let left_eye = t.f32s(6)?;
            if left_eye.len() != 19 {
                return Err(Error::Assertion(String::new()));
            }
            let right_eye = t.f32s(7)?;
            if right_eye.len() != 19 {
                return Err(Error::Assertion(String::new()));
            }
            Keyframe::ProceduralFace {
                trigger_time_ms: t.u32(0, 0)?,
                angle: t.f32(1, 0.0)?,
                center_x: t.f32(2, 0.0)?,
                center_y: t.f32(3, 0.0)?,
                scale_x: t.f32(4, 1.0)?,
                scale_y: t.f32(5, 1.0)?,
                left_eye,
                right_eye,
            }
        }
        8 => Keyframe::RobotAudio {
            trigger_time_ms: t.u32(0, 0)?,
            audio_event_ids: t.i64s(1)?,
            volume: t.f32(2, 1.0)?,
            probabilities: t.f32s(3)?,
            has_alts: t.boolean(4, true)?,
        },
        _ => Keyframe::Event { trigger_time_ms: t.u32(0, 0)?, event_id: t.required_string(1, "event_id")? },
    })
}

/// Keyframes slots by kind (kind order of `Keyframe::kind()`).
const KIND_SLOTS: [usize; 10] = [2, 0, 8, 9, 7, 4, 5, 1, 3, 6];

/// Decodes clips like `AnimClips.from_fb_stream()`: keyframes are grouped by kind, in the order of
/// `AnimClip.from_fb()`.
pub fn decode_clips(buf: &[u8]) -> Result<Vec<Clip>> {
    let root = Table::at(buf, u32_at(buf, 0)?)?;
    let mut clips = Vec::new();
    for c in root.tables(0)? {
        let kfs = c.table(1)?.ok_or_else(|| Error::Value("Missing keyframes.".into()))?;
        let mut keyframes = Vec::new();
        for (kind, slot) in KIND_SLOTS.iter().enumerate() {
            for t in kfs.tables(*slot)? {
                keyframes.push(read_keyframe(kind, &t)?);
            }
        }
        let name = c.required_string(0, "Name")?;
        clips.push(Clip { name, keyframes });
    }
    Ok(clips)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Vec<Clip> {
        vec![
            Clip { name: "empty".into(), keyframes: vec![] },
            Clip {
                name: "anim_test_01".into(),
                keyframes: vec![
                    Keyframe::HeadAngle { trigger_time_ms: 1, duration_ms: 2, angle_deg: -3, variability_deg: 4 },
                    Keyframe::ProceduralFace {
                        trigger_time_ms: 33,
                        angle: 1.5,
                        center_x: 0.0,
                        center_y: 2.0,
                        scale_x: 1.0,
                        scale_y: 0.5,
                        left_eye: (0..19).map(f64::from).collect(),
                        right_eye: vec![0.25; 19],
                    },
                    Keyframe::RobotAudio {
                        trigger_time_ms: 0,
                        audio_event_ids: vec![1, -2, 1 << 40],
                        volume: 1.0,
                        probabilities: vec![0.5],
                        has_alts: false,
                    },
                    Keyframe::BodyMotion {
                        trigger_time_ms: 5,
                        duration_ms: 6,
                        radius_mm: "STRAIGHT".into(),
                        speed: -100,
                    },
                    Keyframe::BackpackLights { trigger_time_ms: 7, duration_ms: 8, lights: [[1.0, 2.0, 3.0, 0.0]; 5] },
                    Keyframe::Event { trigger_time_ms: 9, event_id: "TAPPED_BLOCK".into() },
                    Keyframe::HeadAngle { trigger_time_ms: 10, duration_ms: 2, angle_deg: 3, variability_deg: 0 },
                ],
            },
        ]
    }

    #[test]
    fn round_trip_groups_keyframes_by_kind() {
        let clips = sample();
        let buf = encode_clips(&clips).unwrap();
        let back = decode_clips(&buf).unwrap();
        assert_eq!(back.len(), 2);
        let mut expected = clips[1].keyframes.clone();
        expected.sort_by_key(|k| k.kind());
        assert_eq!(back[1].keyframes, expected);
        assert_eq!(encode_clips(&back).unwrap().len(), buf.len());
    }

    #[test]
    fn range_checks() {
        let clips = vec![Clip {
            name: "x".into(),
            keyframes: vec![Keyframe::HeadAngle {
                trigger_time_ms: 0,
                duration_ms: 0,
                angle_deg: 200,
                variability_deg: 0,
            }],
        }];
        assert!(matches!(encode_clips(&clips), Err(Error::Type(_))));
    }

    #[test]
    fn malformed_input_is_an_error() {
        assert!(decode_clips(&[]).is_err());
        assert!(decode_clips(&[0xff, 0xff, 0xff, 0x7f]).is_err());
        let buf = encode_clips(&sample()).unwrap();
        for n in 0..buf.len() {
            let _ = decode_clips(&buf[..n]);
        }
    }
}
