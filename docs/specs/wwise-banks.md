# Wwise sound banks, version 120

Specification of the subset of the AudioKinetic Wwise sound bank format read by
`pycozmo.audiokinetic.hierarchy`. Little-endian. Other bank versions are refused.

## Chunks

A bank is a sequence of chunks `tag[4] uint32 size data[size]`.

| Chunk | Content used |
| --- | --- |
| `BKHD` | `uint32 version` (120), `uint32 bank id`, `uint32 language id`, `uint32 flags` (bit 0: feedback information in nodes) |
| `DIDX` | media index: `size / 12` entries `uint32 media id, uint32 offset in DATA, uint32 size` |
| `DATA` | embedded media |
| `HIRC` | `uint32 count`, then objects `uint8 type, uint32 size, uint32 id, body[size − 4]` |

An object must end exactly at `id + size`; otherwise it is unreadable (reported, kept as a
plain node) and reading goes on with the next object.

## Common structures

- **Property bundle**: `uint8 n`, `n × uint8 id`, `n × value`; then randomizer ranges:
  `uint8 m`, `m × uint8 id`, `m × (value min, value max)`. Values are `int32` for DelayTime,
  TransitionTime, Loop and the MIDI properties (0x2d–0x35, 0x37–0x39), `float32` otherwise.
- **Variable-length integer**: big-endian base 128 (high bit: more bytes follow).
- **RTPC list**: `uint16 n`, each: `uint32 rtpc id, uint8 type, uint8 accumulation,
  varint parameter id, uint32 curve id, uint8 scaling, uint16 points, points × 12 bytes`.

Property identifiers:

| ID | Property | Unit |
| --- | --- | --- |
| 0x00 | Volume | dB |
| 0x02 | Pitch | cents |
| 0x03 | LPF | 0–100 |
| 0x04 | HPF | 0–100 |
| 0x06 | MakeUpGain | dB |
| 0x0f | DelayTime | ms |
| 0x10 | TransitionTime | ms |
| 0x11 | Probability | % |
| 0x3a | Loop | count, 0 = infinite |
| 0x3b | InitialDelay | s |

## Node base parameters

In order:

1. Effects: `uint8 override`, `uint8 n`; if `n > 0`: `uint8 bypass bits`, then `n × 7 bytes`.
2. `uint8 override attachment`, `uint32 override bus`, `uint32 parent id`, `uint8 flags`.
3. Property bundle.
4. Positioning: `uint8 bits`; if bits 0 and 3 are set: `uint8 3D bits`, `uint32 attenuation`; if
   `(3D bits & 3) != 1`: `uint8 path mode`, `int32 transition time`, `uint32 v`, `v × 16 bytes`,
   `uint32 p`, `p × 8 bytes`, `p × 12 bytes`.
5. Aux sends: `uint8 bits`; if bit 3: 16 bytes.
6. Advanced settings: 6 bytes.
7. States: `uint32 groups`, each `uint32 group id, uint8 sync, uint16 n, n × 8 bytes`.
8. RTPC list.
9. If the bank header has feedback: `uint32 feedback bus`.

## Objects

| Type | Object | Body |
| --- | --- | --- |
| 2 | Sound | `uint32 plugin id, uint8 stream type, uint32 source id, uint32 in-memory size, uint8 source bits`; if `plugin id & 0xf` is 2 or 5 (source plugin): `uint32 n, n bytes`; node base |
| 3 | Action | `uint16 type` (high byte: action, low byte: scope), `uint32 target id`, `uint8 is bus`, property bundle, then parameters below |
| 4 | Event | `uint32 n, n × uint32 action id` |
| 5 | Random/sequence container | node base; `uint16 loop count, uint16, uint16, float32 × 3, uint16 avoid repeat count, uint8 transition mode, uint8 random mode (0 standard, 1 shuffle), uint8 mode (0 random, 1 sequence), uint8 flags`; children; playlist `uint16 n, n × (uint32 id, int32 weight)` |
| 6 | Switch container | node base; `uint8 group type, uint32 group id, uint32 default switch, uint8 continuous validation`; children; `uint32 n`, each `uint32 switch id, uint32 m, m × uint32 node`; `uint32 k, k × 14 bytes` |
| 7 | Actor-mixer | node base; children |
| 9 | Layer container | node base; children; `uint32 layers`, each `uint32 id`, RTPC list, `uint32 crossfade parameter, uint8 type, uint32 n`, each `uint32 child, uint32 points, points × 12 bytes`; `uint8 continuous validation` |
| others | — | kept as a node with its type |

Children: `uint32 n, n × uint32 id`.

Actions (high byte of the type) and their parameters:

| Action | Parameters |
| --- | --- |
| 0x01 Stop | `uint8 fade curve`, exceptions |
| 0x04 Play | `uint8 fade curve`, `uint32 bank id` |
| 0x1e Seek | `uint8 relative to duration, float32 value, float32 min, float32 max, uint8 snap to marker`, exceptions |
| others | not read |

Exceptions: `uint32 n`, each `uint32 id, uint8 is bus`. Scopes: 0x02/0x03 element,
0x04/0x05 all, 0x08/0x09 all except.
