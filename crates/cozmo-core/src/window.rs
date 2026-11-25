//! Sliding windows of the protocol (`pycozmo/window.py`).
//!
//! One structure holds the state of the receive and send variants; `last_seq` is used by the receive window and
//! `next_seq` by the send window.

use crate::error::{Error, Result};

/// Window state. `T` is the data kept for each sequence number.
#[derive(Debug, Clone, PartialEq)]
pub struct Window<T> {
    /// Size of the window.
    pub size: i64,
    /// Next expected sequence number.
    pub expected_seq: i64,
    /// Maximum sequence number (first invalid one).
    pub max_seq: i64,
    /// Data, indexed by sequence number modulo the size.
    pub slots: Vec<Option<T>>,
    /// Receive window: last sequence number in the window.
    pub last_seq: i64,
    /// Send window: next sequence number to use.
    pub next_seq: i64,
}

fn pow2(bits: i64) -> Result<i64> {
    if !(0..=62).contains(&bits) {
        return Err(Error::Value("Invalid sequence number bits.".into()));
    }
    Ok(1i64 << bits)
}

impl<T> Window<T> {
    /// Creates a window from sequence number bits, or a size, like `BaseWindow.__init__()`.
    pub fn new(seq_bits: i64, size: Option<i64>, max_seq: Option<i64>) -> Result<Self> {
        let size = match size {
            None => {
                if seq_bits < 1 {
                    return Err(Error::Value("Invalid sequence number bits.".into()));
                }
                pow2(seq_bits - 1)?
            }
            Some(s) => {
                let limit = if seq_bits < 1 { 0 } else { pow2(seq_bits - 1)? };
                if s <= 0 || s > limit {
                    return Err(Error::Value("Invalid window size.".into()));
                }
                s
            }
        };
        let max_seq = match max_seq {
            Some(m) if m != 0 => m,
            _ => pow2(seq_bits)?,
        };
        if max_seq % size != 0 {
            return Err(Error::Value("The maximum sequence number must be a multiple of the window size must.".into()));
        }
        let mut w = Window { size, expected_seq: 0, max_seq, slots: Vec::new(), last_seq: 0, next_seq: 0 };
        w.clear_slots();
        w.last_seq = (w.expected_seq + w.size - 1).rem_euclid(w.max_seq);
        Ok(w)
    }

    fn clear_slots(&mut self) {
        self.slots = (0..self.size).map(|_| None).collect();
    }

    fn index(&self, seq: i64) -> usize {
        seq.rem_euclid(self.size) as usize
    }

    /// Whether a sequence number is valid for the window.
    pub fn is_valid_seq(&self, seq: i64) -> bool {
        0 <= seq && seq < self.max_seq
    }

    /// Resets the base state (`BaseWindow.reset()`).
    pub fn reset_base(&mut self) {
        self.expected_seq = 0;
        self.clear_slots();
    }

    // ---- Receive window

    /// Whether a sequence number is outside of the receive window.
    pub fn recv_is_out_of_order(&self, seq: i64) -> bool {
        if self.expected_seq > self.last_seq {
            self.expected_seq > seq && seq > self.last_seq
        } else {
            seq < self.expected_seq || seq > self.last_seq
        }
    }

    /// Whether data for a sequence number was already received.
    pub fn exists(&self, seq: i64) -> bool {
        self.slots.get(self.index(seq)).is_some_and(|s| s.is_some())
    }

    /// Puts received data in the window; invalid, out-of-order and duplicate sequence numbers are ignored.
    pub fn recv_put(&mut self, seq: i64, data: Option<T>) {
        if !self.is_valid_seq(seq) || self.recv_is_out_of_order(seq) || self.exists(seq) {
            return;
        }
        let i = self.index(seq);
        self.slots[i] = data;
    }

    /// Returns the next data in order, if received, and moves the window forward.
    pub fn recv_get(&mut self) -> Option<T> {
        let i = self.index(self.expected_seq);
        let data = self.slots.get_mut(i).and_then(Option::take);
        if data.is_some() {
            self.expected_seq = (self.expected_seq + 1).rem_euclid(self.max_seq);
            self.last_seq = (self.expected_seq + self.size - 1).rem_euclid(self.max_seq);
        }
        data
    }

    /// Resets a receive window.
    pub fn recv_reset(&mut self) {
        self.reset_base();
        self.last_seq = (self.expected_seq + self.size - 1).rem_euclid(self.max_seq);
    }

    // ---- Send window

    /// Whether a sequence number is outside of the send window.
    pub fn send_is_out_of_order(&self, seq: i64) -> bool {
        if self.expected_seq > self.next_seq {
            self.expected_seq > seq && seq >= self.next_seq
        } else {
            seq < self.expected_seq || seq >= self.next_seq
        }
    }

    /// Whether the send window is full.
    pub fn is_full(&self) -> bool {
        if self.expected_seq > self.next_seq {
            (self.next_seq + self.max_seq - self.expected_seq) >= self.size
        } else {
            (self.next_seq - self.expected_seq) >= self.size
        }
    }

    /// Adds data and returns its sequence number, or `None` when the window is full.
    pub fn send_put(&mut self, data: Option<T>) -> Option<i64> {
        if self.is_full() {
            return None;
        }
        let i = self.index(self.next_seq);
        self.slots[i] = data;
        let seq = self.next_seq;
        self.next_seq = (self.next_seq + 1).rem_euclid(self.max_seq);
        Some(seq)
    }

    /// Acknowledges a sequence number and drops the data up to it.
    pub fn acknowledge(&mut self, seq: i64) {
        if !self.is_valid_seq(seq) || self.send_is_out_of_order(seq) {
            return;
        }
        let seq = (seq + 1).rem_euclid(self.max_seq);
        while self.expected_seq != seq {
            let i = self.index(self.expected_seq);
            self.slots[i] = None;
            self.expected_seq = (self.expected_seq + 1).rem_euclid(self.max_seq);
        }
    }

    /// Sequence numbers in the send window, with their data.
    pub fn send_get(&self) -> Vec<(i64, Option<&T>)> {
        let mut res = Vec::new();
        let mut seq = self.expected_seq;
        while seq != self.next_seq {
            res.push((seq, self.slots.get(self.index(seq)).and_then(Option::as_ref)));
            seq = (seq + 1).rem_euclid(self.max_seq);
        }
        res
    }

    /// Resets a send window.
    pub fn send_reset(&mut self) {
        self.reset_base();
        self.next_seq = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create() {
        let w = Window::<u8>::new(4, None, None).unwrap();
        assert_eq!((w.size, w.max_seq), (8, 16));
        assert!(Window::<u8>::new(0, None, None).is_err());
        assert!(Window::<u8>::new(3, Some(10), None).is_err());
        assert!(Window::<u8>::new(16, Some(62), Some(0xfffe)).is_ok());
    }

    #[test]
    fn receive_in_order_from_out_of_order_input() {
        let mut w = Window::new(4, None, None).unwrap();
        for seq in [2, 0, 1, 1, 20] {
            w.recv_put(seq, Some(seq));
        }
        assert_eq!(w.recv_get(), Some(0));
        assert_eq!(w.recv_get(), Some(1));
        assert_eq!(w.recv_get(), Some(2));
        assert_eq!(w.recv_get(), None);
    }

    #[test]
    fn receive_wraps_around() {
        let mut w = Window::new(4, None, None).unwrap();
        let mut got = Vec::new();
        for i in 1..100 {
            w.recv_put((i - 1) % w.max_seq, Some(i));
            while let Some(d) = w.recv_get() {
                got.push(d);
            }
        }
        assert_eq!(got, (1..100).collect::<Vec<_>>());
    }

    #[test]
    fn send_full_and_acknowledge() {
        let mut w = Window::new(3, None, None).unwrap();
        for i in 0..4 {
            assert_eq!(w.send_put(Some(i)), Some(i));
        }
        assert!(w.is_full());
        assert_eq!(w.send_put(Some(9)), None);
        w.acknowledge(1);
        assert_eq!(w.send_get(), vec![(2, Some(&2)), (3, Some(&3))]);
        w.acknowledge(7); // out of the window: ignored
        assert_eq!(w.expected_seq, 2);
        w.send_reset();
        assert!(w.send_get().is_empty());
    }
}
