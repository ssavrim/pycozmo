//! FlatBuffers reading and writing of animation clips for `pycozmo.anim_encoder.AnimClips`.
//!
//! The animation classes stay Python classes; the extension converts between them and the core model. The classes
//! are given once by `anim_encoder.py` with `anim_register()`.

use pyo3::exceptions::{PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBytes, PyDict, PyList};
use pyo3::IntoPyObjectExt;

use cozmo_core::anim::{self, Clip, Keyframe};

use crate::errors::err;

/// Classes, in the order of `Keyframe::kind()`, then AnimClip and AnimLight.
const NAMES: [&str; 12] = [
    "AnimHeadAngle",
    "AnimLiftHeight",
    "AnimRecordHeading",
    "AnimTurnToRecordedHeading",
    "AnimBodyMotion",
    "AnimBackpackLights",
    "AnimFaceAnimation",
    "AnimProceduralFace",
    "AnimRobotAudio",
    "AnimEvent",
    "AnimClip",
    "AnimLight",
];

static CLASSES: GILOnceCell<Vec<Py<PyAny>>> = GILOnceCell::new();

fn classes(py: Python<'_>) -> PyResult<&Vec<Py<PyAny>>> {
    CLASSES.get(py).ok_or_else(|| PyRuntimeError::new_err("animation classes are not registered"))
}

/// Registers the Python animation classes (a dict by class name).
#[pyfunction]
pub fn anim_register(py: Python<'_>, classes: &Bound<'_, PyDict>) -> PyResult<()> {
    let v = NAMES
        .iter()
        .map(|n| {
            classes
                .get_item(n)?
                .map(|c| c.unbind())
                .ok_or_else(|| PyTypeError::new_err(format!("missing animation class {n}")))
        })
        .collect::<PyResult<Vec<_>>>()?;
    let _ = CLASSES.set(py, v);
    Ok(())
}

fn kw<'py>(py: Python<'py>, items: &[(&str, PyObject)]) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    for (k, v) in items {
        d.set_item(*k, v)?;
    }
    Ok(d)
}

fn obj<'py, T: IntoPyObject<'py>>(py: Python<'py>, v: T) -> PyResult<PyObject> {
    v.into_py_any(py)
}

fn keyframe_to_py(py: Python<'_>, cls: &[Py<PyAny>], kf: Keyframe) -> PyResult<PyObject> {
    let k = kf.kind();
    let args: Vec<(&str, PyObject)> = match kf {
        Keyframe::HeadAngle { trigger_time_ms, duration_ms, angle_deg, variability_deg } => vec![
            ("trigger_time_ms", obj(py, trigger_time_ms)?),
            ("duration_ms", obj(py, duration_ms)?),
            ("angle_deg", obj(py, angle_deg)?),
            ("variability_deg", obj(py, variability_deg)?),
        ],
        Keyframe::LiftHeight { trigger_time_ms, duration_ms, height_mm, variability_mm } => vec![
            ("trigger_time_ms", obj(py, trigger_time_ms)?),
            ("duration_ms", obj(py, duration_ms)?),
            ("height_mm", obj(py, height_mm)?),
            ("variability_mm", obj(py, variability_mm)?),
        ],
        Keyframe::RecordHeading { trigger_time_ms } => vec![("trigger_time_ms", obj(py, trigger_time_ms)?)],
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
        } => vec![
            ("trigger_time_ms", obj(py, trigger_time_ms)?),
            ("duration_ms", obj(py, duration_ms)?),
            ("offset_deg", obj(py, offset_deg)?),
            ("speed_deg_per_sec", obj(py, speed_deg_per_sec)?),
            ("accel_deg_per_sec_2", obj(py, accel_deg_per_sec_2)?),
            ("decel_deg_per_sec_2", obj(py, decel_deg_per_sec_2)?),
            ("tolerance_deg", obj(py, tolerance_deg)?),
            ("num_half_revs", obj(py, num_half_revs)?),
            ("use_shortest_dir", obj(py, use_shortest_dir)?),
        ],
        Keyframe::BodyMotion { trigger_time_ms, duration_ms, radius_mm, speed } => vec![
            ("trigger_time_ms", obj(py, trigger_time_ms)?),
            ("duration_ms", obj(py, duration_ms)?),
            ("radius_mm", obj(py, radius_mm)?),
            ("speed", obj(py, speed)?),
        ],
        Keyframe::BackpackLights { trigger_time_ms, duration_ms, lights } => {
            let light = cls[11].bind(py);
            let mut v = vec![("trigger_time_ms", obj(py, trigger_time_ms)?), ("duration_ms", obj(py, duration_ms)?)];
            for (name, l) in ["left", "front", "middle", "back", "right"].iter().zip(lights.iter()) {
                v.push((name, light.call1((l[0], l[1], l[2], l[3]))?.unbind()));
            }
            v
        }
        Keyframe::FaceAnimation { trigger_time_ms, anim_name } => {
            vec![("trigger_time_ms", obj(py, trigger_time_ms)?), ("anim_name", obj(py, anim_name)?)]
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
        } => vec![
            ("trigger_time_ms", obj(py, trigger_time_ms)?),
            ("angle", obj(py, angle)?),
            ("center_x", obj(py, center_x)?),
            ("center_y", obj(py, center_y)?),
            ("scale_x", obj(py, scale_x)?),
            ("scale_y", obj(py, scale_y)?),
            ("left_eye", PyList::new(py, left_eye)?.into_any().unbind()),
            ("right_eye", PyList::new(py, right_eye)?.into_any().unbind()),
        ],
        Keyframe::RobotAudio { trigger_time_ms, audio_event_ids, volume, probabilities, has_alts } => vec![
            ("trigger_time_ms", obj(py, trigger_time_ms)?),
            ("audio_event_ids", PyList::new(py, audio_event_ids)?.into_any().unbind()),
            ("volume", obj(py, volume)?),
            ("probabilities", PyList::new(py, probabilities)?.into_any().unbind()),
            ("has_alts", obj(py, has_alts)?),
        ],
        Keyframe::Event { trigger_time_ms, event_id } => {
            vec![("trigger_time_ms", obj(py, trigger_time_ms)?), ("event_id", obj(py, event_id)?)]
        }
    };
    Ok(cls[k].bind(py).call((), Some(&kw(py, &args)?))?.unbind())
}

/// Decodes FlatBuffers data into a list of `AnimClip` objects.
#[pyfunction]
pub fn anim_decode_clips(py: Python<'_>, buffer: PyBackedBytes) -> PyResult<Py<PyList>> {
    let clips = anim::decode_clips(&buffer).map_err(err)?;
    let cls = classes(py)?;
    let mut out = Vec::with_capacity(clips.len());
    for c in clips {
        let kfs = c.keyframes.into_iter().map(|k| keyframe_to_py(py, cls, k)).collect::<PyResult<Vec<_>>>()?;
        let args = kw(py, &[("name", obj(py, c.name)?), ("keyframes", PyList::new(py, kfs)?.into_any().unbind())])?;
        out.push(cls[10].bind(py).call((), Some(&args))?);
    }
    Ok(PyList::new(py, out)?.unbind())
}

fn i(o: &Bound<'_, PyAny>, name: &str) -> PyResult<i64> {
    o.getattr(name)?.extract::<i64>()
}

fn f(o: &Bound<'_, PyAny>, name: &str) -> PyResult<f64> {
    o.getattr(name)?.extract::<f64>()
}

fn light(o: &Bound<'_, PyAny>, name: &str) -> PyResult<[f64; 4]> {
    let l = o.getattr(name)?;
    Ok([f(&l, "red")?, f(&l, "green")?, f(&l, "blue")?, f(&l, "ir")?])
}

fn floats(o: &Bound<'_, PyAny>, name: &str) -> PyResult<Vec<f64>> {
    o.getattr(name)?.try_iter()?.map(|v| v?.extract::<f64>()).collect()
}

fn keyframe_from_py(cls: &[Py<PyAny>], o: &Bound<'_, PyAny>) -> PyResult<Keyframe> {
    let py = o.py();
    let kind = (0..10).find(|k| o.is_instance(cls[*k].bind(py)).unwrap_or(false));
    let t = || i(o, "trigger_time_ms");
    Ok(match kind {
        Some(0) => Keyframe::HeadAngle {
            trigger_time_ms: t()?,
            duration_ms: i(o, "duration_ms")?,
            angle_deg: i(o, "angle_deg")?,
            variability_deg: i(o, "variability_deg")?,
        },
        Some(1) => Keyframe::LiftHeight {
            trigger_time_ms: t()?,
            duration_ms: i(o, "duration_ms")?,
            height_mm: i(o, "height_mm")?,
            variability_mm: i(o, "variability_mm")?,
        },
        Some(2) => Keyframe::RecordHeading { trigger_time_ms: t()? },
        Some(3) => Keyframe::TurnToRecordedHeading {
            trigger_time_ms: t()?,
            duration_ms: i(o, "duration_ms")?,
            offset_deg: i(o, "offset_deg")?,
            speed_deg_per_sec: i(o, "speed_deg_per_sec")?,
            accel_deg_per_sec_2: i(o, "accel_deg_per_sec_2")?,
            decel_deg_per_sec_2: i(o, "decel_deg_per_sec_2")?,
            tolerance_deg: i(o, "tolerance_deg")?,
            num_half_revs: i(o, "num_half_revs")?,
            use_shortest_dir: o.getattr("use_shortest_dir")?.is_truthy()?,
        },
        Some(4) => Keyframe::BodyMotion {
            trigger_time_ms: t()?,
            duration_ms: i(o, "duration_ms")?,
            radius_mm: o.getattr("radius_mm")?.str()?.to_string(),
            speed: i(o, "speed")?,
        },
        Some(5) => Keyframe::BackpackLights {
            trigger_time_ms: t()?,
            duration_ms: i(o, "duration_ms")?,
            lights: [light(o, "left")?, light(o, "front")?, light(o, "middle")?, light(o, "back")?, light(o, "right")?],
        },
        Some(6) => Keyframe::FaceAnimation { trigger_time_ms: t()?, anim_name: o.getattr("anim_name")?.extract()? },
        Some(7) => Keyframe::ProceduralFace {
            trigger_time_ms: t()?,
            angle: f(o, "angle")?,
            center_x: f(o, "center_x")?,
            center_y: f(o, "center_y")?,
            scale_x: f(o, "scale_x")?,
            scale_y: f(o, "scale_y")?,
            left_eye: floats(o, "left_eye")?,
            right_eye: floats(o, "right_eye")?,
        },
        Some(8) => Keyframe::RobotAudio {
            trigger_time_ms: t()?,
            audio_event_ids: o
                .getattr("audio_event_ids")?
                .try_iter()?
                .map(|v| v?.extract::<i64>())
                .collect::<PyResult<_>>()?,
            volume: f(o, "volume")?,
            probabilities: floats(o, "probabilities")?,
            has_alts: o.getattr("has_alts")?.is_truthy()?,
        },
        Some(_) => Keyframe::Event { trigger_time_ms: t()?, event_id: o.getattr("event_id")?.extract()? },
        None => return Err(PyRuntimeError::new_err("Unexpected keyframe type.")),
    })
}

/// Encodes `AnimClip` objects into FlatBuffers data, byte for byte like the Python FlatBuffers builder.
#[pyfunction]
pub fn anim_encode_clips<'py>(py: Python<'py>, clips: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    let cls = classes(py)?;
    let mut model = Vec::new();
    for c in clips.try_iter()? {
        let c = c?;
        let keyframes =
            c.getattr("keyframes")?.try_iter()?.map(|k| keyframe_from_py(cls, &k?)).collect::<PyResult<Vec<_>>>()?;
        model.push(Clip { name: c.getattr("name")?.extract()?, keyframes });
    }
    let buf = anim::encode_clips(&model).map_err(err)?;
    Ok(PyBytes::new(py, &buf))
}
