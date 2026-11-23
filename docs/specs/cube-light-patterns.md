# Cube light patterns

How the light patterns of the cubes (`config/engine/lights/cubeLights`) are read, turned into the packets of the
cube, and played one after the other. Implemented in `pycozmo/lights.py` (colors, frames, LED states) and
`pycozmo/cube_lights.py` (files and playback).

Confidence: **Specified** (precise rule, tested), **Assumed** (reasonable choice, may differ from the original),
**Not modeled**.

Implemented in `pycozmo/lights.py` (colors, frames, LED states) and `pycozmo/cube_lights.py` (files and playback).

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
2. **White balance** (Specified). Applied to each color before encoding: when the red value is not 0, green and blue
   are multiplied by 0.6 (in 32-bit floats, then truncated); otherwise the color is unchanged.
3. **Color** (Specified). 16 bits: red, green and blue on 5 bits (the value divided by 8) in bits 10-14, 5-9 and 0-4,
   and bit 15 set when the alpha value is not 0.
4. **Periods** (Specified). Milliseconds to frames of 30 ms, rounded up: `(ms + 29) // 30` on 32 bits, kept on 8 bits;
   -1 gives 255. The offset follows the same rule on signed values, kept on 16 bits.
5. **Rotation** (Specified). `rotationPeriod_ms` goes through the same conversion, to the `rotation_period_frames` of the
   cube selection packet that precedes the lights packet.
6. **Gamma** (Not modeled). The original sets a gamma value on the cube when it changes; the value is a constant
   (0x80) in all cases found, and pycozmo does not send it.

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

## Open points

| Point | Status |
|---|---|
| Parsing, required fields, default of `canBeOverridden` | Specified |
| Solid pattern rule, white balance, color and period encoding | Specified |
| Bytes of the colors (red in the lowest byte) deduced from the encoding code | Specified |
| Playback of one layer, stack, override flag | Specified |
| Stop behavior and lights after the last pattern | Assumed |
| Layers, blending, default animation, gamma | Not modeled |
