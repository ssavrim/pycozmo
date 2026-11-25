//! Decay graphs and emotion values (`DecayGraph` and `EmotionType` of `pycozmo/emotions.py`).

use crate::error::{Error, Result};

/// A delta larger than this, in the direction of the value, restarts the decay.
pub const STRENGTHEN_THRESHOLD: f64 = 0.05;
/// Smallest decay graph value that is divided by.
pub const DECAY_EPSILON: f64 = 1e-5;

/// Piecewise linear function given by nodes, evaluated like `numpy.interp()`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct DecayGraph {
    pub xs: Vec<f64>,
    pub ys: Vec<f64>,
}

impl DecayGraph {
    /// Creates a graph from node coordinates.
    pub fn new(xs: Vec<f64>, ys: Vec<f64>) -> Self {
        DecayGraph { xs, ys }
    }

    /// Evaluates the graph at `x`, with the semantics of `numpy.interp(x, xs, ys)`: constant outside of the nodes,
    /// the value of the last node among equal abscissas, and the same fallbacks for non-finite slopes.
    pub fn evaluate(&self, x: f64) -> Result<f64> {
        let (xp, fp) = (&self.xs, &self.ys);
        let n = xp.len();
        if n == 0 {
            return Err(Error::Value("array of sample points is empty".into()));
        }
        if fp.len() != n {
            return Err(Error::Value("fp and xp are not of the same length.".into()));
        }
        if x.is_nan() {
            return Ok(f64::NAN);
        }
        if n == 1 {
            return Ok(fp[0]);
        }
        if x > xp[n - 1] {
            return Ok(fp[n - 1]);
        }
        if x < xp[0] {
            return Ok(fp[0]);
        }
        // Largest j with xp[j] <= x.
        let j = xp.partition_point(|v| *v <= x).saturating_sub(1);
        if j >= n - 1 {
            return Ok(fp[n - 1]);
        }
        if xp[j] == x {
            return Ok(fp[j]);
        }
        let slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
        let mut res = slope * (x - xp[j]) + fp[j];
        if res.is_nan() {
            res = slope * (x - xp[j + 1]) + fp[j + 1];
            if res.is_nan() && fp[j] == fp[j + 1] {
                res = fp[j];
            }
        }
        Ok(res)
    }
}

/// `numpy.clip()` of a scalar: `minimum(maximum(v, lo), hi)`, NaN propagating.
pub fn clip(v: f64, lo: f64, hi: f64) -> f64 {
    if v.is_nan() || lo.is_nan() || hi.is_nan() {
        return f64::NAN;
    }
    let a = if v > lo { v } else { lo };
    if a < hi {
        a
    } else {
        hi
    }
}

/// Value of an emotion and its decay state.
#[derive(Debug, Clone, PartialEq)]
pub struct Emotion {
    pub min: f64,
    pub max: f64,
    /// Time since the value was last strengthened.
    pub age: f64,
    pub last_update: f64,
    pub value: f64,
}

impl Emotion {
    /// Decays the value over the time since the last update. `graph` evaluates the decay graph.
    pub fn update(&mut self, now: f64, graph: &mut dyn FnMut(f64) -> Result<f64>) -> Result<()> {
        let d = now - self.last_update;
        let dt = if d > 0.0 || d.is_nan() { d } else { 0.0 };
        self.last_update = now;
        if dt == 0.0 {
            return Ok(());
        }
        let before = graph(self.age)?;
        self.age += dt;
        let after = graph(self.age)?;
        let ratio = if before > DECAY_EPSILON { after / before } else { after };
        self.value = clip(self.value * ratio, self.min, self.max);
        Ok(())
    }

    /// Sets the value and restarts the decay.
    pub fn set(&mut self, value: f64, now: f64, graph: &mut dyn FnMut(f64) -> Result<f64>) -> Result<()> {
        self.update(now, graph)?;
        self.age = 0.0;
        self.value = clip(value, self.min, self.max);
        Ok(())
    }

    /// Adds to the value. The decay restarts when the value changes sign or is strengthened by a delta > 0.05.
    pub fn add(&mut self, delta: f64, now: f64, graph: &mut dyn FnMut(f64) -> Result<f64>) -> Result<()> {
        self.update(now, graph)?;
        let previous = self.value;
        self.value = clip(previous + delta, self.min, self.max);
        if (previous >= 0.0) != (self.value >= 0.0)
            || (delta.abs() > STRENGTHEN_THRESHOLD && (previous >= 0.0) == (delta >= 0.0))
        {
            self.age = 0.0;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn graph() -> DecayGraph {
        DecayGraph::new(vec![0.0, 10.0, 60.0, 100.0], vec![1.0, 1.0, 0.6, 0.0])
    }

    #[test]
    fn interp() {
        let g = graph();
        assert_eq!(g.evaluate(-1.0).unwrap(), 1.0);
        assert!((g.evaluate(40.0).unwrap() - 0.76).abs() < 1e-12);
        assert_eq!(g.evaluate(60.0).unwrap(), 0.6);
        assert_eq!(g.evaluate(200.0).unwrap(), 0.0);
        assert!(g.evaluate(f64::NAN).unwrap().is_nan());
        assert!(DecayGraph::default().evaluate(1.0).is_err());
        let dup = DecayGraph::new(vec![0.0, 5.0, 5.0, 10.0], vec![0.0, 1.0, 2.0, 3.0]);
        assert_eq!(dup.evaluate(5.0).unwrap(), 2.0);
    }

    #[test]
    fn decay() {
        let g = DecayGraph::new(vec![0.0, 10.0, 20.0], vec![1.0, 1.0, 0.0]);
        let mut eval = |x: f64| g.evaluate(x);
        let mut e = Emotion { min: -1.0, max: 1.0, age: 0.0, last_update: 0.0, value: 0.0 };
        e.add(0.5, 0.0, &mut eval).unwrap();
        e.update(15.0, &mut eval).unwrap();
        assert!((e.value - 0.25).abs() < 1e-12);
        e.update(20.0, &mut eval).unwrap();
        assert_eq!(e.value, 0.0);
        e.add(2.0, 20.0, &mut eval).unwrap();
        assert_eq!(e.value, 1.0);
    }

    #[test]
    fn clip_like_numpy() {
        assert_eq!(clip(2.0, -1.0, 1.0), 1.0);
        assert_eq!(clip(0.5, 1.0, 0.0), 0.0);
        assert!(clip(f64::NAN, 0.0, 1.0).is_nan());
    }
}
