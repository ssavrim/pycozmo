# Animation library

Specification of the access to animation clips, animation groups and triggers of prepared
resources, of the choice of a clip, and of face animation keyframes. Implemented by
`pycozmo.anim_library`, `pycozmo.anim.AnimationGroup` and `pycozmo.anim.PreprocessedClip`.

## Data

Under `<root>/cozmo_resources/assets/`:

| Path | Content |
| --- | --- |
| `animations/*.bin` | FlatBuffers clips (`cozmo_anim.fbs`), several clips per file |
| `animationGroups/**/<group>.json` | `{"Animations": [member, ...]}` |
| `animationGroupMaps/AnimationTriggerMap.json` | `{"Pairs": [{"CladEvent": trigger, "AnimName": group}, ...]}` |
| `cubeAnimationGroupMaps/CubeAnimationTriggerMap.json` | same, for cube light animations |
| `faceAnimations/<name>/<name>_NNNNN.png` | frames of a face animation |

Group member fields: `Name` (clip), `Weight`, `CooldownTime_Sec`, `Mood`, and optionally
`UseHeadAngle`, `HeadAngleMin_Deg`, `HeadAngleMax_Deg`.

## Library

`AnimationLibrary(root)` indexes clips and groups without loading them; clips are loaded on
first use. `choose(trigger_or_group, head_angle_rad=None, now=None, mood="Default",
strict=False)` resolves a trigger to its group (a group name is also accepted) and returns a
clip name, or `None` for an unknown trigger or group, or when the rules below give no member.

Cooldowns are kept per library, **per clip name**: a clip in several groups shares its
cooldown. Times use the library clock (`time.monotonic` by default, injectable).

## Choice of a member

Given members in file order, a mood, the current head angle (radians, optional) and `now`:

1. **Eligible** members: `Mood` equal to the mood; if `UseHeadAngle`, the head angle is known and
   within `[radians(HeadAngleMin_Deg), radians(HeadAngleMax_Deg)]`; not on cooldown (cooldown end
   ≤ `now`).
2. If some are eligible: draw `r` uniformly in `[0, sum of their weights)`, subtract the weights
   in order and pick the member at which `r` becomes negative; if none (all weights zero), the
   **last** eligible member. Its cooldown ends at `now + CooldownTime_Sec`.
3. Otherwise, if the mood is not `Default`: apply these rules with `Default`.
4. Otherwise, in strict mode, or if no member has the mood: no member.
5. Otherwise, among `Default` members whose head angle range (if `UseHeadAngle`) widened by
   **±0.05 rad** contains the head angle: the one whose cooldown ends first. No new cooldown.
6. Otherwise: the first member of the group.

`AnimationGroup.choose_member()` applies the same rules; without a cooldown table it does not
remember cooldowns. `Client.play_anim_group()` keeps a cooldown table and passes its head angle.

## Face animation keyframes

A `FaceAnimation(trigger, anim_name)` keyframe plays the PNG frames of
`faceAnimations/<anim_name>/`, in name order:

- one frame per animation frame (30 per second): frame *i* at `trigger + round(i × 1000 / 30)` ms;
- a 128×64 frame is reduced to the 128×32 display by keeping the even lines (as procedural
  faces); pixels above 127 are lit;
- a frame with no lit pixel takes its time but sends nothing;
- from `trigger` to the end of the last frame, procedural face images are dropped;
- a missing directory skips the keyframe with a warning.

To be checked on a robot: threshold and choice of even lines.
