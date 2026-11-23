# Cube light patterns

How the light patterns of the cubes (`config/engine/lights/cubeLights`) are read, turned into the packets of the
cube, and played one after the other. Implemented in `pycozmo/lights.py` (colors, frames, LED states) and
`pycozmo/cube_lights.py` (files and playback).

Confidence: **Specified** (precise rule, tested), **Assumed** (reasonable choice, may differ from the original),
**Not modeled**.

## Files

`config/engine/lights/cubeLights/**/*.json` (38 files). A file defines one animation, named by its only key (the first
key in alphabetical order when there are several); its value is a list of entries:

```json
{"flash": [{"pattern": {...}, "duration_ms": 600, "patternDebugName": "flash", "canBeOverridden": false}]}
```

- `pattern` and `duration_ms` are required in each entry, `canBeOverridden` defaults to true, `patternDebugName` is
  only a label (default: the name of the animation followed by the index of the entry).
- A `pattern` needs all of: `onColors`, `offColors` (4 colors `[r, g, b, a]`), `onPeriod_ms`, `offPeriod_ms`,
  `transitionOnPeriod_ms`, `transitionOffPeriod_ms`, `offset` (4 values each) and `rotationPeriod_ms`. A pattern with a
  missing field makes its animation invalid (it is skipped).
- `assets/cubeAnimationGroupMaps/CubeAnimationTriggerMap.json` maps the trigger names (`CladEvent`) to animation
  names (`AnimName`).

## From a pattern to the packets

The cube has four LEDs. Each one follows `on color` for `on period`, then `off color` for `off period`, with a
transition of `transition on` / `transition off` between the two, shifted by `offset`.

1. **Solid LEDs** (Specified). A LED whose on period is 0 is solid in its off color (if the off period is also 0: black);
   a LED whose off period is 0 is solid in its on color. A solid LED gets both colors equal and the period
   2147483647.
2. **White balance** (Specified). Applied to each color of the cube lights before encoding (the backpack lights of
   the robot are encoded the same way but without the white balance): when the red value is not 0, green and blue
   are multiplied by 0.6 (in 32-bit floats, then truncated); otherwise the color is unchanged.
3. **Color** (Specified). 16 bits: red, green and blue on 5 bits (the value divided by 8) in bits 10-14, 5-9 and 0-4,
   and bit 15 set when the alpha value is not 0.
4. **Periods** (Specified). Milliseconds to frames of 30 ms, rounded up: `(ms + 29) // 30` on 32 bits, kept on 8 bits;
   -1 gives 255. The offset follows the same rule on signed values, kept on 16 bits.
5. **Rotation** (Specified). `rotationPeriod_ms` goes through the same conversion, to the `rotation_period_frames` of the
   cube selection packet that precedes the lights packet.
6. **Gamma** (Specified). Before the lights of a cube, the original sets the gamma of its LEDs (`SetCubeGamma`,
   0x80) when the value changes. Without it, white shows as yellow and red on the cubes tested. `Client.set_cube_lights`
   sends it before the first lights of a cube and again after the cube connects again.

## Playback

A cube plays one animation at a time; this is what is modeled for one layer (`CubeLightPlayer`).

- **Start** (Specified). The first pattern is set at once. If its `duration_ms` is not 0, the pattern ends that long
  after the start. A duration of 0 keeps the pattern until the animation is stopped.
- **Next pattern** (Specified). When a pattern ends, the next one is set, and ends at *the time of the update* plus its
  duration (the delay of the update accumulates; it is not compensated).
- **End of the animation** (Specified). When the last pattern ends, the animation is over: its callback is called, and
  the animation that was running before it (if any) is set again.
- **Stack** (Specified). Playing an animation whose first pattern has a duration of 0 replaces everything. Playing one
  whose first pattern has a duration keeps the running animation under it, and it resumes when the new one ends. The
  resumed animation keeps its own deadline.
- **Override** (Specified). While the running animation was flagged `canBeOverridden: false` in its first pattern,
  playing another animation is refused.
- **Stop** (Assumed). Stopping the running animation resumes the previous one, if any; with nothing to resume, the
  lights stay as they are.
- **Not modeled**: the three layers of the original and their priorities, the modifier and the blending of an
  animation with the current lights, the default animation chosen when a layer is empty, the lights state
  transition messages, and the lights while the cube is not connected.

## In the brain

`CubeLightManager` (in `pycozmo.cube_lights`) holds one player per connected cube, follows `ObjectConnectionState` and is
created by `Brain`, which calls `update` once per frame:

- A cube that connects (the three light cubes only) plays the animation of the trigger `Connected`; a cube that
  disconnects loses its player.
- `Brain.play_cube_animation(name, object_id=None)` plays an animation by the name of its trigger (the `CladEvent` of
  `CubeAnimationTriggerMap.json`, for example `Flash`, `Dance_01`) or of the animation, on one cube or on all of them. It
  returns the number of cubes it plays on (0 for an unknown name, a warning is logged).
- When the brain stops, the animations stop and the lights of the cubes are turned off.

Not done: the brain does not connect the cubes by itself (use `Client.set_object_connection`), and no behavior plays a
cube animation yet; the triggers of the original (`Sleep`, `Dance_*`, `GuardDog*`...) are for the behaviors that use them.
On a robot (2026-11-23, `--tests braincubes`): the brain created with the prepared resources played `Connected`, `Flash`
and `Dance_01` on a cube (the packets were sent; the operator saw the animations).

## Open points

| Point | Status |
|---|---|
| Parsing, required fields, default of `canBeOverridden` | Specified |
| Solid pattern rule, white balance, color and period encoding | Specified |
| Bytes of the colors (red in the lowest byte) deduced from the encoding code | Specified |
| Playback of one layer, stack, override flag | Specified |
| Stop behavior and lights after the last pattern | Assumed |
| Gamma sent before the lights | Specified (validated on three cubes) |
| Layers, blending, default animation | Not modeled |
| Playing the animations of the connected cubes from the brain (`Connected`, `play_cube_animation`) | Tested offline and on a robot (the operator saw the animations) |
