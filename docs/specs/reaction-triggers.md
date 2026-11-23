# Reaction triggers

Specification of the reaction triggers: the situations in which Cozmo interrupts what it does to react (the robot is
picked up, a cliff is detected, a fist bump is offered, ...), and the decision to trigger one. Implemented by
`pycozmo.behavior` (`ReactionTrigger`, `ReactionTriggerManager`). The decision is pure, like the selection of
behaviors (`docs/specs/behavior-selection.md`): time, randomness and the state of the robot come from a
`SelectionContext`. It does not start any behavior.

## Data

`reactionTrigger_behavior_map.json`, in `<root>/cozmo_resources/config/engine/behaviorSystem/`: a list of entries
`reactionTrigger`, `behaviorID`, and optionally:

| Field | Content |
| --- | --- |
| `genericStrategyParams` | `shouldResumeLast`, `debugStrategyName` |
| `wantsToRunStrategyConfig` | `strategyType` of a state condition (`PlacedOnCharger`, `RobotPlacedOnSlope`, `RobotShaken`) |
| `behaviorObjectiveTriggerParams` | list of `behaviorObjective`, `triggerCooldownTime_s`, `triggerProbability`, `triggerExpiration_s` (FistBump) |
| `frustrationParams` | `maxConfidence`, `cooldownTime_s` (Frustration) |
| `hiccupParams` | parameters of the Hiccup reaction (loaded, not used) |

A trigger can have several entries: `Frustration` has a minor and a major one. `load_reaction_triggers` returns all the
entries in the order of the file; `load_reaction_trigger_behavior_map` keeps only the last entry of each trigger, so `Brain` uses `load_reaction_triggers` with `select_reaction_trigger`.

## Order

The triggers are tried in the order of their identifiers: CliffDetected, CubeMoved, FacePositionUpdated, FistBump,
Frustration, Hiccup, MotorCalibration, NoPreDockPoses, ObjectPositionUpdated, PlacedOnCharger, PetInitialDetection,
RobotFalling, RobotPickedUp, RobotPlacedOnSlope, ReturnedToTreads, RobotOnBack, RobotOnFace, RobotOnSide, RobotShaken,
Sparked, UnexpectedMovement; the entries of one trigger in the order of the file. All of them are tried in one check; when
several trigger, the application is told of all of them, in this order, and the last one is the one that wins.

## Check

`ReactionTriggerManager.check(context)` returns the entries that trigger now. An entry is skipped when:

1. its trigger is locked (`lock`, `unlock`: a count, as several parts of the application can lock a trigger);
2. a reaction of another trigger is already running (`SelectionContext.running_reaction`) and the application does not
   allow the entry's trigger to interrupt it (`SelectionContext.can_interrupt`, no by default).

Then the rule of the entry applies. Each rule ends with the same condition: the behavior of the entry is runnable
(`SelectionContext.runnable`) or running.

| Entry | Triggers when |
| --- | --- |
| `FistBump` (entry with `behaviorObjectiveTriggerParams`) | it is armed, not expired, the behavior is not running and the robot does not carry a block (`SelectionContext.carrying_block`) |
| `Frustration` (entry with `frustrationParams`) | the running reaction is not `Frustration`, the *Confident* emotion of the mood in the context is below `maxConfidence` (strictly; without a mood, never), and the previous trigger of this entry is older than `cooldownTime_s` (none when the entry has no cooldown) |
| entry with `wantsToRunStrategyConfig` | the state condition holds (`SelectionContext.wants_to_run`, given the `strategyType`) |
| any other entry | an event was notified for the trigger and not yet used (`notify`), and the behavior is runnable |

A trigger that fired uses its notification, and, for `Frustration`, records its time.

**FistBump arming.** `objective_achieved(objective)` is called when the application reports an achieved behavior
objective. For an objective that has parameters: nothing happens when the last fist bump trigger was less than
`triggerCooldownTime_s` ago; otherwise, with probability `triggerProbability`, the trigger is armed for
`triggerExpiration_s` seconds. A parameter entry is rejected when its cooldown is negative, its probability is not in
(0, 1] or its expiration is not above 0. An armed trigger that expired is disarmed at the next check.

## Confidence

| Point | Status |
| --- | --- |
| Order of the triggers, all tried in one check, interruption is per trigger | Assumed (the order is the one of the identifiers, not of the file; it only matters when several triggers fire together). |
| FistBump: cooldown, probability, expiration, carrying block, validation of the parameters | Specified. |
| Frustration: current reaction, confidence, cooldown | Specified; the default cooldown of an entry that has none is assumed to be none. |
| Locks of a trigger | Specified as a count. |
| Generic entries: notification latched until used | Assumed. The original keeps the time of each relevant event and compares it to a timeout fixed for each trigger, with a state condition; the events and timeouts of each trigger are not specified here. |
| Interruption of a running reaction by another trigger | Left to the application (`can_interrupt`); the rules of the original are not specified. |
| State conditions `PlacedOnCharger`, `RobotPlacedOnSlope`, `RobotShaken` | Left to the application (`wants_to_run`). |
| `Hiccup`, `FacePositionUpdated`, `ObjectPositionUpdated`, `CubeMoved`, `PetInitialDetection`, `NoPreDockPoses` | Treated as any other entry; their own rules are not modeled. |
| Which robot event notifies which trigger | Left to the application (`Brain` posts them). |
| Comparison on a robot | See the validation in the pull request. |
