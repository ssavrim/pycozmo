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
| any other value | arc, signed radius (on firmware 2381 the robot drives a circle of about 2.05 times this radius, see below) | mm/s |

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

## Validation on a robot

`tools/pycozmo_validate.py` plays these messages on a robot and measures the result with
`RobotState` (see its help for the tests). Results with firmware 2381, body HW 5:

| Item | Result |
| --- | --- |
| `AnimEvent` → `AnimationEvent` | Verified (`anim_energy_eat_01`, `ENERGY_DRAINCUBE_END`) |
| `AnimBody` straight, 40 mm/s × 1.5 s | 56 mm for 60 mm, heading 0° |
| `AnimBody` turn in place, 45 deg/s × 1 s | +41° for +45°: `speed` is in deg/s |
| `AnimBody` arcs, radius 150 mm at 40 mm/s × 2, 4, 8 s | Positive radius turns left; distance right (chord 305 mm for 320 mm). Heading expected / odometry / floor (protractor, about 5° precision): 31 / 11 / 14°, 61 / 26 / 40° (estimate), 122 / 58 / 60°. The ratio does not tend to 1 on longer arcs. Quarter circle of 707 mm of path (50 mm/s × 14.1 s), odometry heading for a commanded radius of 450 / 200 / 220 mm: +38° / +99° / +89° for +90° expected, i.e. an effective radius of about 1060 / 410 / 455 mm. A commanded radius R gives a circle of about 2.05 R (about 2.4 R at 450 mm, where the heading is small and the odometry error weighs more). Cause not established (the robot may read the value as a diameter). The radius is sent unchanged: the original clips are probably authored for this behaviour. |
| `AnimHead` / `AnimLift`, 3 s moves | Verified: head 90 % in 3.26 s (40.7°); lift 84.8–85.3 mm for 85 mm in 3.2–3.3 s. `RobotState.lift_height_rad` (named `lift_height_mm` before this was measured) is the arm angle in radians; the height is `45 + 66 × sin(angle)` mm (`pycozmo.robot`) |
| `body` test | Straight 95 mm for 100, turn in place 81° for 90°; the `stopped` criterion (wheel speeds of `RobotState`) reports failure although the robot stops, probably another unit issue |

## Uses of the events (informative)

- `TAPPED_BLOCK`: in a reaction-speed game, the `timestamp` is the moment the robot taps;
  compare it with the player's tap time rather than with the reception time.
- `DEVICE_AUDIO_TRIGGER`: a moment when the host can play a sound or light a cube in sync
  with the animation (memory games).
- `ENERGY_DRAINCUBE_END`: end of an "energy" sequence.
