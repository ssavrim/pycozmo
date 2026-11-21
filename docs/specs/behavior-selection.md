# Behavior selection

Specification of how a behavior is chosen among the behaviors of an activity, and how an activity is chosen
among the sub-activities of a parent activity. Implemented in `pycozmo.activity` (`BehaviorChooser`,
`ActivityStrategy`, `Activity.choose`, `StrictPriorityActivity`) and `pycozmo.behavior` (`BehaviorConfig`). The
selection is pure: time comes from an injectable clock, randomness from an injectable random generator, and the state
of the robot (needs, mood, whether a behavior can run) from a `SelectionContext`. It does not start any behavior and
does not move the robot.

Usage: `activities = load_activities(root)`, then `behavior_id = activities["Freeplay"].choose(context, activities)`
for a container activity, or `activity.choose(context)` for an activity that has a chooser. The application tells the
chooser when a behavior starts and stops (`BehaviorChooser.started`, `stopped`).

## Data

Under `<root>/cozmo_resources/config/engine/behaviorSystem/` (JSON with `//` comments):

| File | Content |
| --- | --- |
| `behaviors/**/*.json` | one behavior: `behaviorID`, `behaviorClass`, `requiredUnlockId`, `animTriggers`, `need`, `needBracket`, `cooldown` (a graph), class-specific parameters; loaded by `load_behavior_configs`, which the application can use to decide runnability |
| `activities/**/*.json`, `activities_config.json`, `behavior_system_config.json` | activities: `activityID`, `activityType`, `activityStrategy`, `behaviorChooser` (or `universalChooser`), `subActivities` (`activityID`, `activityPriority`) |
| `reactionTrigger_behavior_map.json` | reaction triggers (see `pycozmo.behavior`) |

A graph is `{"nodes": [{"x", "y"}, ...]}`, evaluated by linear interpolation, constant outside its nodes.

## Behavior score

For one behavior of a chooser, with its `scoring` entry (`flatScore`, optional `repetitionPenalty` and
`runningPenalty` graphs):

- **Not running.** The score is 0 when the behavior is not runnable. Otherwise it is `flatScore`, multiplied by the
  `repetitionPenalty` graph evaluated at the time since the behavior last stopped (no multiplication when it never
  stopped, when it has no such graph, or in the second that follows a stop that asked for no immediate penalty).
- **Running.** `flatScore`, multiplied by the `runningPenalty` graph evaluated at the time since the behavior
  started (no multiplication when it has no such graph).

A behavior is runnable when the application says so (`SelectionContext.runnable`): unlocked, blocks, charger and the
other conditions of the behavior are the application's to evaluate. A behavior that is running stays runnable.

## Choosers

- **StrictPriority**: the first behavior of the list that is running or runnable.
- **Scoring**: each behavior with a score above 0 gets a final score; the behavior with the largest one wins and a
  chooser without any such behavior chooses nothing.
  - Not running: score plus a random value in [0, 0.1).
  - Running: `max(0.01, bonus(runningTime) + score + 0.1)`, where `bonus` is the `scoreBonusForCurrentBehavior`
    graph of the chooser (0 when absent) and `runningTime` the time since the behavior started. This favors the
    behavior that is already running.
  - On equal final scores the behavior listed first wins.
- **Selection**: chosen by the application; the chooser chooses nothing by itself.

A chooser is held by the activities of types `BehaviorsOnly`, `NeedsExpression` and `Socialize` (`behaviorChooser`),
and `Feeding` (`universalChooser`, a StrictPriority list). The other activity types have none.

## Activity strategies

A strategy decides when an activity may start and when it must end. Common fields of `activityStrategy`:
`activityCanEndDurationSecs` (default -1), `activityShouldEndDurationSecs` (60), `cooldownBaseSecs` (-1),
`cooldownRandomnessSecs` (0), `requiredRecentOnTreadsEventSecs` (-1), `startMoodScorer` and
`requiredMinStartMoodScore` (-1).

**Wants to start**, in this order, all must hold:

1. **Cooldown.** When the cooldown is above 0 and the activity ended before: `now >= endTime + cooldown`. When
   this holds, the cooldown is drawn again as `cooldownBaseSecs + uniform(0, cooldownRandomnessSecs)`.
2. **Recently on treads.** When `requiredRecentOnTreadsEventSecs` is above 0: the robot was put on its treads after
   the activity ended, and no more than that number of seconds ago.
3. The strategy's own rule, below.

**Wants to end**: false while the activity has run less than `activityCanEndDurationSecs` (when above 0); true once
it has run more than `activityShouldEndDurationSecs` (when above 0); otherwise the strategy's own rule.

| Type | Own rule to start | Own rule to end |
| --- | --- | --- |
| `Simple` | always | never |
| `Needs` | the `wantsToRunStrategyConfig` strategy (`InNeedsBracket`: the need is in the bracket) holds | the `higherPriorityStrategyConfig` strategy holds, or the `wantsToRunStrategyConfig` strategy no longer holds |
| `NeedBasedCooldown` | as `Simple` | never; the cooldown is set from the level of `needId` as `needCooldownGraph(level)` with randomness `needCooldownRandomnessGraph(level)` |
| `SevereNeedTransition`, `Spark`, `Pyramid`, `PlayWithHumans` | not supported: never | not supported: always |

## Activity selection

A `StrictPriority` activity holds sub-activities with a priority. At each choice, in turn:

1. when there is a current sub-activity that does not want to end, its behavior is chosen, if it has one;
2. otherwise the current sub-activity ends (its end time is recorded for its cooldown) and the sub-activities are
   taken by increasing priority number; the first one whose strategy wants to start becomes the current one, and
   its behavior is chosen.

This is repeated at most 5 times; when no sub-activity starts, no behavior is chosen. The current sub-activity is
`StrictPriorityActivity.current`.

## Confidence

| Point | Status |
| --- | --- |
| Score of a behavior, repetition and running penalties, the 1 s without penalty after some stops | Specified. |
| Scoring chooser: noise [0, 0.1), running score, equal-score rule | Specified, except the equal-score rule, which is assumed (the original order is the one of its table). |
| StrictPriority chooser | Specified. |
| Strategy start and end rules, defaults | Specified. The end time passed for the cooldown follows the end of the activity; two further time rules of the original (a shorter cooldown of 3 s in some cases) are not modeled. |
| `Needs`, `NeedBasedCooldown`, mood and on-treads conditions | Specified. |
| Runnability of a behavior (unlock, blocks, charger, mood, other conditions, the `cooldown` of `ExpressNeeds` behaviors) | Left to the application (`SelectionContext.runnable`); not modeled. |
| Order of the original table of behaviors; the `boredomMultiplier` hook of the scoring chooser; emotion scorers | Not modeled. |
| `SevereNeedTransition`, `Spark`, `Pyramid`, `PlayWithHumans`, `Sparked`, `BuildPyramid`, `Socialize` activities | Not supported. |
| Comparison on a robot | See the validation in the pull request. |
