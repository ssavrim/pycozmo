//! Cozmo protocol: packet types, the messages generated from `pycozmo/protocol_declaration.py`, and packets.

pub mod messages;

use crate::error::{Error, Result};
use crate::wire::{Reader, Writer};

pub use messages::*;

/// Frame identifier, at the start of every frame.
pub const FRAME_ID: &[u8; 7] = b"COZ\x03RE\x01";
/// Smallest frame: identifier, type and three sequence numbers.
pub const MIN_FRAME_SIZE: usize = FRAME_ID.len() + 1 + 2 + 2 + 2;
/// Largest frame that the robot accepts.
pub const MAX_FRAME_SIZE: usize = 1051;
/// Largest frame payload.
pub const MAX_FRAME_PAYLOAD_SIZE: usize = MAX_FRAME_SIZE - MIN_FRAME_SIZE;
/// Packets with an identifier from this one on are sent by the robot.
pub const FIRST_ROBOT_PACKET_ID: i64 = 0xb0;
/// Largest sequence number used.
pub const MAX_SEQ: i64 = 0xfffe;
/// Sequence number of out-of-band packets.
pub const OOB_SEQ: i64 = 0xffff;

/// Packet types (`PacketType` in `pycozmo/protocol_ast.py`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PacketType {
    Unknown = -1,
    Connect = 2,
    Disconnect = 3,
    Command = 4,
    Event = 5,
    Keyframe = 0x0a,
    Ping = 0x0b,
}

impl PacketType {
    /// Numeric value.
    pub fn value(self) -> i64 {
        self as i64
    }

    /// Member name, as in Python.
    pub fn name(self) -> &'static str {
        match self {
            PacketType::Unknown => "UNKNOWN",
            PacketType::Connect => "CONNECT",
            PacketType::Disconnect => "DISCONNECT",
            PacketType::Command => "COMMAND",
            PacketType::Event => "EVENT",
            PacketType::Keyframe => "KEYFRAME",
            PacketType::Ping => "PING",
        }
    }

    /// Converts a numeric value.
    pub fn from_value(v: i64) -> Result<Self> {
        Ok(match v {
            -1 => PacketType::Unknown,
            2 => PacketType::Connect,
            3 => PacketType::Disconnect,
            4 => PacketType::Command,
            5 => PacketType::Event,
            0x0a => PacketType::Keyframe,
            0x0b => PacketType::Ping,
            _ => return Err(Error::Value(format!("{v} is not a valid PacketType"))),
        })
    }
}

/// Frame types (`FrameType` in `pycozmo/protocol_ast.py`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FrameType {
    Reset = 1,
    ResetAck = 2,
    Fin = 3,
    EngineAct = 4,
    Engine = 7,
    Robot = 9,
    Ping = 0x0b,
}

impl FrameType {
    /// Numeric value.
    pub fn value(self) -> i64 {
        self as i64
    }

    /// Member name, as in Python.
    pub fn name(self) -> &'static str {
        match self {
            FrameType::Reset => "RESET",
            FrameType::ResetAck => "RESET_ACK",
            FrameType::Fin => "FIN",
            FrameType::EngineAct => "ENGINE_ACT",
            FrameType::Engine => "ENGINE",
            FrameType::Robot => "ROBOT",
            FrameType::Ping => "PING",
        }
    }

    /// Converts a numeric value.
    pub fn from_value(v: i64) -> Result<Self> {
        Ok(match v {
            1 => FrameType::Reset,
            2 => FrameType::ResetAck,
            3 => FrameType::Fin,
            4 => FrameType::EngineAct,
            7 => FrameType::Engine,
            9 => FrameType::Robot,
            0x0b => FrameType::Ping,
            _ => return Err(Error::Value(format!("{v} is not a valid FrameType"))),
        })
    }
}

/// Content of a packet: a declared message, or the bytes of a message that is not declared.
#[derive(Debug, Clone, PartialEq)]
pub enum Body {
    Message(Message),
    Unknown(Vec<u8>),
}

/// A packet: type, identifier, sequence numbers and content (`Packet` in `pycozmo/protocol_base.py`).
#[derive(Debug, Clone, PartialEq)]
pub struct Packet {
    pub ptype: PacketType,
    /// Message identifier. `None` for connect, disconnect, ping and keyframe packets.
    pub id: Option<i64>,
    pub seq: i64,
    pub ack: i64,
    pub body: Body,
}

impl Packet {
    /// A packet for a declared message, with out-of-band sequence numbers.
    pub fn new(msg: Message) -> Self {
        Packet {
            ptype: msg.packet_type(),
            id: msg.id().map(i64::from),
            seq: OOB_SEQ,
            ack: OOB_SEQ,
            body: Body::Message(msg),
        }
    }

    /// A packet whose message is not declared.
    pub fn unknown(ptype: PacketType, id: Option<i64>, data: Vec<u8>) -> Self {
        Packet { ptype, id, seq: OOB_SEQ, ack: OOB_SEQ, body: Body::Unknown(data) }
    }

    /// Out-of-band packets (events, keyframes and pings) are not sequenced.
    pub fn is_oob(&self) -> bool {
        self.ptype.value() >= PacketType::Event.value()
    }

    /// Whether the packet comes from the robot.
    pub fn is_from_robot(&self) -> bool {
        match self.id {
            Some(id) => id >= FIRST_ROBOT_PACKET_ID,
            None => self.ptype == PacketType::Connect || (self.ptype == PacketType::Ping && self.seq > 0),
        }
    }

    /// Size of the encoded content.
    pub fn payload_len(&self) -> usize {
        match &self.body {
            Body::Message(m) => m.encoded_len(),
            Body::Unknown(d) => d.len(),
        }
    }

    /// Encodes the content.
    pub fn encode_payload(&self, w: &mut Writer) -> Result<()> {
        match &self.body {
            Body::Message(m) => m.encode(w),
            Body::Unknown(d) => {
                w.write_bytes(d);
                Ok(())
            }
        }
    }

    /// The encoded content.
    pub fn payload_bytes(&self) -> Result<Vec<u8>> {
        let mut w = Writer::with_capacity(self.payload_len());
        self.encode_payload(&mut w)?;
        Ok(w.into_bytes())
    }
}

/// Decodes a packet of a given type whose identifier, for commands and events, was already read.
///
/// `pkt_len` is the declared length including the identifier, used for messages that are not declared.
pub fn decode_packet(ptype: PacketType, pkt_len: i64, r: &mut Reader<'_>) -> Result<Packet> {
    let msg = match ptype {
        PacketType::Command | PacketType::Event => {
            let id = r.read_u8()?;
            match Message::decode_by_id(id, r) {
                Some(res) => res?,
                None => {
                    let n = pkt_len - 1;
                    if n < 0 {
                        return Err(Error::Pack("count must be non-negative".into()));
                    }
                    let data = r.take(n as usize)?.to_vec();
                    return Ok(Packet::unknown(ptype, Some(i64::from(id)), data));
                }
            }
        }
        PacketType::Ping => Message::Ping(Ping::decode(r)?),
        PacketType::Keyframe => Message::Keyframe(Keyframe::decode(r)?),
        PacketType::Connect => Message::Connect(Connect::decode(r)?),
        PacketType::Disconnect => Message::Disconnect(Disconnect::decode(r)?),
        PacketType::Unknown => {
            return Err(Error::Value(format!("Unexpected packet type PacketType.{}.", ptype.name())))
        }
    };
    Ok(Packet::new(msg))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packet_flags() {
        let p = Packet::new(Message::Ping(Ping::try_default().unwrap()));
        assert!(p.is_oob());
        assert!(p.is_from_robot());
        let c = Packet::new(Message::SetRobotVolume(SetRobotVolume::try_default().unwrap()));
        assert!(!c.is_oob());
        assert!(!c.is_from_robot());
        assert_eq!(c.id, Some(0x64));
    }

    #[test]
    fn unknown_command_keeps_its_bytes() {
        let data = [0x03u8, 9, 8, 7];
        let mut r = Reader::new(&data, 0);
        // 0x03 is declared; use an identifier that is not.
        let raw = [0x7fu8, 1, 2];
        let mut r2 = Reader::new(&raw, 0);
        assert!(Message::decode_by_id(0x7f, &mut r).is_none());
        let p = decode_packet(PacketType::Command, 3, &mut r2).unwrap();
        assert_eq!(p.body, Body::Unknown(vec![1, 2]));
        assert_eq!(p.id, Some(0x7f));
    }
}
