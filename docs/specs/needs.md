# Needs

Specification of the needs model, implemented by `pycozmo.needs`. The model is pure: time comes from an
injectable clock and randomness from an injectable random generator. It is specified for the 3.4.0 resources;
the last section marks what is assumed.

## Data

Under `<root>/cozmo_resources/config/engine/` (JSON with `//` comments):

| File | Content |
| --- | --- |
| `needs_config.json` | `MaximumNeedLevel`, `MinimumNeedLevel`, `DecayPeriodSeconds`, `InitialNeedLevel<Need>`, `BracketLevel<Need><Bracket>`, `FullnessDecayCooldown<Need>` (seconds), `BrokenPartThreshold0..2` |
| `needs_decay_config.json` | `DecayRates.{Connected,Unconnected}DecayRates<Need>`: lists of `{Threshold, DecayPerMinute}`; `DecayModifiers.ConnectedDecayModifiers<Need>`: lists of `{Threshold, OtherNeedsAffected: [{OtherNeedID, Multiplier}]}` |
| `needs_action_config.json` | `actionDeltas`: `actionId`, `<need>Delta`, `<need>Range`, `cooldownSecs`, ... |

Needs are `Repair`, `Energy` and `Play` (identifiers 0, 1, 2). Brackets are `Full`, `Normal`, `Warning`,
`Critical` (0 to 3). Levels are in `[MinimumNeedLevel, MaximumNeedLevel]`.

## Brackets

The bracket of a need is the first of its four `BracketLevel<Need><Bracket>` thresholds, taken in the order
Full, Normal, Warning, Critical, that the level reaches (`level >= threshold`). `Critical` is 0 and always matches.

## Decay

The decay of the needs is applied every `DecayPeriodSeconds`. Each need is decayed over the time elapsed since
its own last decay, in minutes (seconds / 60). The set of rates used is the connected one while a robot is
connected, otherwise the unconnected one.

**Rates.** The rate list of a need is sorted by decreasing threshold. A rate applies from the level down to its
own threshold: starting from the first entry whose threshold is at or below the level, the level decreases at
`DecayPerMinute * multiplier` until it reaches that threshold, then continues with the next entry for the time
left. After the last entry the level stays at its threshold. A level below every threshold does not decay. A rate
that is zero (or less) stops the decay. The result is not lower than `MinimumNeedLevel`.

**Multipliers.** Before decaying, a multiplier of 1 is set for every need. Each need, in the order Repair,
Energy, Play, looks in its modifier list (sorted by decreasing threshold) for the first entry whose threshold is at
or below its current level, and multiplies the multiplier of each need it lists by the entry's value. The resources only
define connected modifiers, so the model uses none when unconnected. The multipliers are computed once, from the levels before any decay.

**Fullness cooldown.** When an action raises a need by a positive amount up to the `Full` threshold or above, the
decay of that need is paused until `FullnessDecayCooldown<Need>` seconds later. When the pause ends, the time
spent paused is not decayed.

## Actions

An action lists, for each need, a `delta` and a `range`. For each need the change is
`delta + uniform(0, 2 * range) - range`, added to the level and clamped to the limits.

- For a positive change, the action is said to reach the full level when the new level is at or above the `Full`
  threshold of the need. It then starts the fullness cooldown of that need.
- **Energy**: when the full level is reached from below it, the level becomes the maximum.
- **Play**: only the clamp applies.
- **Repair**: see below.

The `cooldownSecs` field is not used by the needs model; it is left to the consumers of the action.

## Repair and broken parts

The robot has three parts that can be damaged, in order head, lift and treads. `BrokenPartThreshold0..2`,
sorted by decreasing value, `T[0] > T[1] > T[2]`.

- When the repair level changes by decay or by a negative change, the number of damaged parts is raised to the
  number of thresholds that are at or above the level, by damaging parts chosen at random among the intact ones.
  Parts are never repaired by decay.
- With `d` damaged parts, a change of the repair level is kept inside a band: not above `T[d-1] - 1e-5` (when
  `d >= 1`), and not below `T[d] + 1e-5` (when `d` is under the number of thresholds).
- The actions `RepairHead`, `RepairLift` and `RepairTreads` first mark their part as repaired, then apply their
  deltas. When no part is damaged afterwards, the repair level becomes the maximum.

## Confidence

| Point | Status |
| --- | --- |
| Band-by-band decay, sorting, no decay below every threshold, floor | Specified. |
| Multipliers: selection per source need, computed once | Specified. |
| Decay period, per-need elapsed time, fullness cooldown and its exclusion from the decay | Specified. |
| Action change with range as `uniform(0, 2r) - r`, clamp, full level, energy snap, cooldown start | Specified. |
| Repair band, damaged parts and part repair | Specified; the order head, lift, treads of the three part identifiers is inferred from the action names. |
| Restoring the repair level to the maximum after the last repair | Specified as the maximum level. |
| Initial levels | Assumed to be the `InitialNeedLevel<Need>` values. Saved state is not modeled. |
| Use of `cooldownSecs` | Not used by the model. |
| Saved state, decay for the time the application was closed, pausing, stars and rewards | Not modeled. |
| Comparison on a robot | See the validation in the pull request. |

Values obtained from the 3.4.0 resources with these rules (connected, no actions): after 1 hour
repair 0.96, energy 0.70, play 0.19; after 6 hours energy and play are at the minimum.
