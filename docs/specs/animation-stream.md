# Animation stream messages

Specification of the animation messages PyCozmo streams to the robot, and of the
conversion of animation keyframes (`anim_encoder` / `cozmo_anim.fbs`) into these messages.
Implemented by `pycozmo.protocol_declaration`, `pycozmo.anim.PreprocessedClip` and
`pycozmo.client.Client`.

All values are little-endian.

## Messages

| ID | Name | Direction | Payload |
| --- | --- | --- | --- |
| 0x93 | `AnimHead` | host → robot | `uint16 duration_ms`, `int8 angle_deg` |
| 0x94 | `AnimLift` | host → robot | `uint16 duration_ms`, `uint8 height_mm` |
| 0x95 | `AnimEvent` | host → robot | `uint8 event_id` (`AnimEventType`) |
| 0x99 | `AnimBody` | host → robot | `int16 speed`, `int16 curvature_radius_mm` |
| 0xd5 | `AnimationEvent` | robot → host | `uint32 timestamp`, `uint8 anim_id`, `uint8 event_id` (`AnimEventType`) |

`AnimEventType`: `DEVICE_AUDIO_TRIGGER` = 0, `ENERGY_DRAINCUBE_END` = 1, `TAPPED_BLOCK` = 2.

### AnimHead, AnimLift

The robot moves the head to `angle_deg` (lift to `height_mm`) in `duration_ms`
milliseconds. Durations up to 65,535 ms are valid; animation data uses up to 8,900 ms.

### AnimBody

| `curvature_radius_mm` | Motion | Unit of `speed` |
| --- | --- | --- |
| `0x7fff` | straight | mm/s |
| `0` | turn in place | deg/s |
| any other value | arc of that radius (signed) | mm/s |

The robot computes the wheel speeds. `AnimBody(speed=0, curvature_radius_mm=0x7fff)` stops.

### AnimEvent, AnimationEvent

`AnimEvent` marks a moment of the animation stream. When the robot plays it, it sends
`AnimationEvent` with its time in milliseconds and the `anim_id` given in the
`StartAnimation` of that animation.

## Keyframe conversion

| Keyframe | Messages |
| --- | --- |
| `HeadAngle(trigger, duration, angle, variability)` | at `trigger`: `AnimHead(duration, angle')` |
| `LiftHeight(trigger, duration, height, variability)` | at `trigger`: `AnimLift(duration, height')` |
| `BodyMotion(trigger, duration, radius, speed)` | at `trigger`: `AnimBody(speed', radius')`; at `trigger + duration`: `AnimBody(0, 0x7fff)` |
| `Event(trigger, event_id)` | at `trigger`: `AnimEvent(event_id)`; unknown identifiers are skipped with a warning |

- **Variability**: when `variability > 0`, `angle'` (`height'`) is drawn uniformly among the
  integers of `[value - variability, value + variability]`, at each preprocessing; then limited
  to the field range (`int8`, `uint8`). Durations are limited to `uint16`.
- **Radius**: `"STRAIGHT"` → `0x7fff`; `"TURN_IN_PLACE"` and `"POINT_TURN"` → `0`; a number
  (or numeric string) → its integer value, values ≥ 32,768 → `0x7fff`, values < −32,768 → −32,768.
- **Speed limits**: straight and arcs ±220 mm/s; turns in place ±300 deg/s; larger values are
  clamped.

## Client

`Client.play_anim_ppclip` remembers the `anim_id` of the animation it starts.
On `AnimationEvent`, `Client` dispatches `EvtAnimationEvent(cli, event_id, timestamp)` if
`anim_id` is that of the current animation, or if no animation was started by the client;
events of other animations are ignored.

## Uses of the events (informative)

- `TAPPED_BLOCK`: in a reaction-speed game, the `timestamp` is the moment the robot taps;
  compare it with the player's tap time rather than with the reception time.
- `DEVICE_AUDIO_TRIGGER`: a moment when the host can play a sound or light a cube in sync
  with the animation (memory games).
- `ENERGY_DRAINCUBE_END`: end of an "energy" sequence.
