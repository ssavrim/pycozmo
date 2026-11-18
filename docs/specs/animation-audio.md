# Sound of animations

Specification of the playback of `RobotAudio` keyframes. Implemented by `pycozmo.anim_audio`;
sound banks are read as described in `wwise-banks.md`.

## RobotAudio keyframe

Fields: `audioEventId` (list, Wwise event identifiers; the low 32 bits are used),
`probability` (list, optional), `volume`, `hasAlts`.

- Without `probability`, each event has probability 1/N.
- With `probability`, its length must equal the number of events and its sum must not exceed 1;
  otherwise the keyframe is invalid and skipped (logged).
- Each time the clip is rendered, draw `r` uniformly in `[0, 1)`; play the first event whose
  cumulative probability exceeds `r`; if none, the keyframe is silent. A keyframe plays at most
  one event.
- `hasAlts` has no effect.
- `volume` multiplies the linear gain of everything the event plays.

## Event resolution

An event is a list of actions, each with an optional delay (DelayTime property, ms, plus its
randomizer range). Times are relative to the keyframe.

| Action | Effect |
| --- | --- |
| Play | Voices for the target object (below), starting after the delay plus the target's InitialDelay (s) |
| Stop | At its time, the voices it addresses fade out over TransitionTime (ms, default 30 ms) |
| Seek | Starts the voices of its target at a position (relative: fraction of the duration, `value + uniform(min, max)` limited to [0, 1]) |
| others | Ignored, logged |

Stop scopes: element (the voices of the target), all, all except the listed objects.
A voice is addressed by an object if the object is on its resolution path or is an ancestor of
an object on it.

Resolution of a target (depth at most 8, cycles refused):

| Object | Voices |
| --- | --- |
| Sound | One voice playing its media, `Loop` times (absent: once; 0: until stopped) |
| Random/sequence container, no loop | The voices of one child chosen as below |
| Random/sequence container, looping | One voice: a new child chosen for each iteration, `loop count` times (0: until stopped) |
| Switch container | The voices of the nodes of its default switch, or of its first child |
| Layer container | The voices of all children, simultaneously |
| Other (music, missing) | None, logged |

Choice of a child (state kept per container until `reset()`):

- **Sequence**: the next item of the playlist, starting over after the last.
- **Random, standard**: weighted draw among playlist items, excluding the last
  `min(avoid repeat count, n − 1)` choices.
- **Random, shuffle**: as standard, but each item is chosen once before any is chosen again.
- Items of weight ≤ 0 are not drawn unless all are; then the draw is uniform.

Media parameters, from the sound and its ancestors (parent chain): volume and make-up gain (dB)
add up; pitch (cents) adds up; LPF and HPF add up, limited to 100. Randomizer ranges add a
uniform draw in `[min, max]` to the property of the object that has them.

## Rendering

- Media: `<audio dir>/**/<source id>.wem`, else media embedded in a bank. Decoded to mono PCM by
  a decoder (default: external `vgmstream-cli`, `VGMSTREAM_CLI`), at most 60 s.
- Each media is resampled to **22,320 Hz**, pitched by resampling (factor `2^(cents/1200)`),
  filtered (one-pole low-pass and high-pass; the 0–100 amount moves the cutoff logarithmically
  from 20 kHz to 50 Hz for LPF and from 20 Hz to 10 kHz for HPF) and scaled by its volume.
- A voice ends when its media ends, at a Stop that addresses it (fade), or, if it loops until
  stopped and nothing stops it, at the end of the clip with a 30 ms fade. The end of the clip is
  the latest `trigger + duration` of its keyframes.
- A Seek applies to voices that start at most one frame before it.
- Voices are summed; above 80 % of full scale, the signal is softly saturated.
- The mix is cut into frames of 744 samples; frame *i* is sent as `OutputAudio` (µ-law) at
  `round(i × 1000 / 30)` ms of the clip; silent frames are not sent.
- Source plugins (generated sounds without media) are not rendered; this is logged.

## Integration

`PreprocessedClip.from_anim_clip(clip, audio=renderer)` adds the `OutputAudio` packets to the
clip, unless `allowed_types` excludes `AnimRobotAudio`. `AnimationLibrary.enable_audio()` creates
a renderer for the library's sound banks.

## Limitations

Music objects, source plugins, buses, RTPC, states and effects are not played; switches use
their default; filters are approximations.
