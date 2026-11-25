//! Frame encoding and decoding (`Frame` in `pycozmo/frame.py`).

use crate::error::{Error, Result};
use crate::protocol::{decode_packet, FrameType, Packet, PacketType, FRAME_ID, MAX_SEQ, MIN_FRAME_SIZE, OOB_SEQ};
use crate::wire::{Reader, Writer};

/// A decoded frame.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub ftype: FrameType,
    pub first_seq: i64,
    pub seq: i64,
    pub ack: i64,
    pub pkts: Vec<Packet>,
}

/// Result of decoding a frame: the frame and the reasons for which packets were skipped.
#[derive(Debug, Clone, PartialEq)]
pub struct Decoded {
    pub frame: Frame,
    /// One message per packet that could not be decoded and was ignored, as PyCozmo does.
    pub skipped: Vec<String>,
}

fn header_seq(v: i64) -> i64 {
    (v + 1).rem_euclid(0x10000)
}

fn encode_packet(pkt: &Packet, w: &mut Writer) -> Result<()> {
    w.write_int('B', pkt.ptype.value())?;
    let len = pkt.payload_len() as i64;
    if pkt.ptype == PacketType::Command || pkt.ptype == PacketType::Event {
        w.write_int('H', len + 1)?;
        write_id(pkt, w)?;
    } else {
        w.write_int('H', len)?;
    }
    pkt.encode_payload(w)
}

fn write_id(pkt: &Packet, w: &mut Writer) -> Result<()> {
    match pkt.id {
        Some(id) => w.write_int('B', id),
        None => Err(Error::Pack("required argument is not an integer".into())),
    }
}

/// Encodes a frame from borrowed packets.
pub fn encode(ftype: FrameType, first_seq: i64, seq: i64, ack: i64, pkts: &[&Packet]) -> Result<Vec<u8>> {
    let mut w = Writer::with_capacity(MIN_FRAME_SIZE + pkts.iter().map(|p| p.payload_len() + 4).sum::<usize>());
    w.write_bytes(FRAME_ID);
    w.write_u8(ftype.value() as u8);
    w.write_u16(header_seq(first_seq) as u16);
    w.write_u16(header_seq(seq) as u16);
    w.write_u16(header_seq(ack) as u16);
    match ftype {
        FrameType::Engine | FrameType::Robot => {
            for pkt in pkts {
                encode_packet(pkt, &mut w)?;
            }
        }
        FrameType::Ping => {
            if pkts.len() != 1 || pkts[0].ptype != PacketType::Ping {
                return Err(Error::Assertion(String::new()));
            }
            pkts[0].encode_payload(&mut w)?;
        }
        FrameType::EngineAct => {
            if pkts.len() != 1 || !matches!(pkts[0].ptype, PacketType::Command | PacketType::Disconnect) {
                return Err(Error::Assertion(String::new()));
            }
            write_id(pkts[0], &mut w)?;
            pkts[0].encode_payload(&mut w)?;
        }
        FrameType::Reset | FrameType::Fin => {
            if !pkts.is_empty() {
                return Err(Error::Assertion(String::new()));
            }
        }
        FrameType::ResetAck => {
            return Err(Error::NotImplemented(format!("Unexpected frame type FrameType.{}.", ftype.name())))
        }
    }
    Ok(w.into_bytes())
}

impl Frame {
    /// Encodes the frame.
    pub fn to_bytes(&self) -> Result<Vec<u8>> {
        let refs: Vec<&Packet> = self.pkts.iter().collect();
        encode(self.ftype, self.first_seq, self.seq, self.ack, &refs)
    }

    /// Decodes a frame. Packets that fail to decode are skipped, like in PyCozmo.
    pub fn decode(buf: &[u8]) -> Result<Decoded> {
        Self::decode_with_end(buf).map(|(d, _)| d)
    }

    /// Decodes a frame (the whole buffer, like PyCozmo) and also returns the position where decoding stopped.
    pub fn decode_with_end(buf: &[u8]) -> Result<(Decoded, usize)> {
        if buf.len() < MIN_FRAME_SIZE {
            return Err(Error::Value("Invalid frame.".into()));
        }
        if &buf[..7] != FRAME_ID {
            return Err(Error::Value("Invalid frame ID.".into()));
        }
        let mut r = Reader::new(buf, 7);
        let ftype = FrameType::from_value(i64::from(r.read_u8()?))?;
        let first_seq = (i64::from(r.read_u16()?) - 1).rem_euclid(0x10000);
        let seq = (i64::from(r.read_u16()?) - 1).rem_euclid(0x10000);
        let ack = (i64::from(r.read_u16()?) - 1).rem_euclid(0x10000);
        let mut pkts = Vec::new();
        let mut skipped = Vec::new();
        match ftype {
            FrameType::Engine | FrameType::Robot => {
                let mut pkt_seq = first_seq;
                let mut last_type: Option<PacketType> = None;
                while r.tell() < r.len() {
                    let ptype = PacketType::from_value(i64::from(r.read_u8()?))?;
                    let pkt_len = i64::from(r.read_u16()?);
                    let expected = r.tell() + pkt_len as usize;
                    match decode_packet(ptype, pkt_len, &mut r) {
                        Ok(mut pkt) => {
                            if r.tell() != expected {
                                // Packet length may change between protocol versions.
                                r.seek(expected);
                            }
                            pkt.seq = pkt_seq;
                            pkt.ack = ack;
                            if !pkt.is_oob() {
                                pkt_seq = (pkt_seq + 1) % MAX_SEQ;
                            }
                            last_type = Some(pkt.ptype);
                            pkts.push(pkt);
                        }
                        Err(e @ (Error::Value(_) | Error::Index(_))) => {
                            skipped.push(e.to_string());
                            r.seek(expected);
                        }
                        Err(e) => return Err(e),
                    }
                }
                if !(seq == OOB_SEQ || seq + 1 == pkt_seq) {
                    match last_type {
                        None => {
                            return Err(Error::UnboundLocal(
                                "cannot access local variable 'pkt' where it is not associated with a value".into(),
                            ))
                        }
                        Some(PacketType::Ping) => {}
                        Some(_) => return Err(Error::Assertion(String::new())),
                    }
                }
            }
            FrameType::Ping => {
                pkts.push(decode_packet(PacketType::Ping, 0, &mut r)?);
            }
            FrameType::EngineAct => {
                let mut pkt_seq = first_seq;
                let pkt_len = r.len() as i64 - r.tell() as i64;
                match decode_packet(PacketType::Command, pkt_len, &mut r) {
                    Ok(mut pkt) => {
                        pkt.seq = pkt_seq;
                        pkt.ack = ack;
                        if !pkt.is_oob() {
                            pkt_seq = (pkt_seq + 1) % MAX_SEQ;
                        }
                        pkts.push(pkt);
                    }
                    Err(e @ (Error::Value(_) | Error::Index(_))) => skipped.push(e.to_string()),
                    Err(e) => return Err(e),
                }
                if !(seq == OOB_SEQ || seq + 1 == pkt_seq) {
                    return Err(Error::Assertion(String::new()));
                }
            }
            FrameType::Reset | FrameType::Fin => {
                if r.tell() != r.len() {
                    return Err(Error::Assertion(String::new()));
                }
            }
            FrameType::ResetAck => {
                return Err(Error::NotImplemented(format!("Unexpected frame type FrameType.{}.", ftype.name())))
            }
        }
        let pos = r.tell();
        Ok((Decoded { frame: Frame { ftype, first_seq, seq, ack, pkts }, skipped }, pos))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::Body;

    const MULTI: &[u8] =
        b"COZ\x03RE\x01\x07\x9d\n\xa0\n\x8f\x00\x04\x01\x00\x8f\x04\x1d\x00\x97\x1a\x00\x15\xb0\xaa\x9c\
\xac\xb2@\xa8\xba^\xac\xb2@\x02\xb4\xa2\xa0\xb0\xaa@\xac\xb2`\xb0\xaa\x1b\x04 \x00\x03\x1f\x80\
\x1f\x80\t\x00\x00\x00\x00\x00\x1f\x80\x1f\x80\t\x00\x00\x00\x00\x00\x1f\x80\x1f\x80\t\x00\x00\
\x00\x00\x00\x00\x04\x16\x00\x11\x1f\x80\x1f\x80\t\x00\x00\x00\x00\x00\x1f\x80\x1f\x80\t\x00\
\x00\x00\x00\x00\x00";

    #[test]
    fn decode_multi_and_reencode() {
        let d = Frame::decode(MULTI).unwrap();
        let f = &d.frame;
        assert_eq!(f.ftype, FrameType::Engine);
        assert_eq!((f.first_seq, f.seq, f.ack), (2716, 2719, 142));
        assert_eq!(f.pkts.len(), 4);
        assert!(d.skipped.is_empty());
        assert_eq!(f.to_bytes().unwrap(), MULTI);
    }

    #[test]
    fn ignore_decode_failures() {
        let buf = b"COZ\x03RE\x01\t\x00\x00\x00\x00\x13\x00\x0b\x11\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\
\x00\x00\x00\x00\x00\x00\x00\x05\x0f\x00\xf1y\x8bJO$\x00\x00\x00\x06\x00\x00\x00\xff\x00";
        let d = Frame::decode(buf).unwrap();
        assert_eq!(d.frame.pkts.len(), 1);
        assert_eq!(d.skipped.len(), 1);
    }

    #[test]
    fn invalid_frames() {
        assert_eq!(Frame::decode(b"COZ"), Err(Error::Value("Invalid frame.".into())));
        assert_eq!(Frame::decode(b"XOZ\x03RE\x01\x07\0\0\0\0\0\0"), Err(Error::Value("Invalid frame ID.".into())));
        assert!(matches!(Frame::decode(b"COZ\x03RE\x01\x08\0\0\0\0\0\0"), Err(Error::Value(_))));
    }

    #[test]
    fn reset_frame_round_trip() {
        let bytes = encode(FrameType::Reset, 0, 0, OOB_SEQ, &[]).unwrap();
        let d = Frame::decode(&bytes).unwrap();
        assert_eq!((d.frame.first_seq, d.frame.seq, d.frame.ack), (0, 0, OOB_SEQ));
        assert!(d.frame.pkts.is_empty());
    }

    #[test]
    fn unknown_packets_are_kept() {
        let pkt = Packet::unknown(PacketType::Event, Some(0xfa), vec![1, 2, 3]);
        let bytes = encode(FrameType::Robot, 0, OOB_SEQ, 0, &[&pkt]).unwrap();
        let d = Frame::decode(&bytes).unwrap();
        assert_eq!(d.frame.pkts[0].body, Body::Unknown(vec![1, 2, 3]));
    }
}
