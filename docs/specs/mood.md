# Mood

Specification of the emotions and the mood, implemented by `pycozmo.emotions` (`EmotionType`, `EmotionEvent`,
`Mood`), for the 3.4.0 resources.

## Data

Under `<root>/cozmo_resources/config/engine/`:

| File | Content |
| --- | --- |
| `mood_config.json` | `decayGraphs`: one graph per emotion, or `default`; `defaultRepetitionPenalty`: a graph of the time since the previous occurrence of an event |
| `emotionevents/*.json` | `emotionEvents`: `name`, `emotionAffectors: [{emotionType, value}]`, and optionally `repetitionPenalty` (a graph) |

A graph is a list of `{x, y}` nodes; it is evaluated by linear interpolation and is constant before the first and
after the last node.

## Emotions

There are nine emotions, in this order: `Happy`, `Calm`, `Brave`, `Confident`, `Charged`, `Excited`, `Social`,
`Winning`, `WantToPlay`. Each has a value in `[-1, 1]`, starting at 0, and an age: the time since the value was
last strengthened. Its decay graph is the one of its name in `mood_config.json`, or `default`.

**Decay.** Over a time `dt` the age grows by `dt` and the value is multiplied by `g(age + dt) / g(age)`, where `g`
is the decay graph; when `g(age)` is not above 1e-5 the value is multiplied by `g(age + dt)`. The value is then
clamped. The value therefore follows the graph from the moment of the last strengthening, whatever the changes in
between.

**Adding to a value.** The value is clamped after the addition. The age restarts at 0 when the value changes
sign (0 counts as positive), or when the added amount is larger than 0.05 in absolute value and has the sign of
the value before the addition. Smaller or weakening additions do not restart it.

## Emotion events

Applying an event adds to each affected emotion the value of its affector multiplied by the **repetition
penalty**: the value, at the time since the same event last occurred, of the event's own `repetitionPenalty`
graph or, when it has none, of `defaultRepetitionPenalty`. An event that has not occurred before has an infinite
elapsed time, which is the last value of the graph. An unknown event is reported and ignored. Affectors with an
unknown emotion are ignored.

With the 3.4.0 defaults the penalty is 0 for an immediate repetition and rises linearly to 1 after 30 seconds.

## Simple mood

`Sad` when the `Happy` value is below -0.33 or the `Confident` value is below -0.29; otherwise `Happy` when the
`Happy` value is above 0.33; otherwise `Default`.

## Confidence

| Point | Status |
| --- | --- |
| Nine emotions and their order | Specified. |
| Incremental decay, age and the rules that restart it, 0.05 threshold | Specified. |
| Repetition penalty at the time since the previous occurrence, infinite for the first | Specified. |
| Event graph overriding the default | Specified; no emotion event of the resources uses it. |
| Simple mood thresholds | Specified. |
| Mood sent to the application, event logging, actions that trigger events | Not modeled. |
| Which events fire for each robot situation | Not modeled; `Brain` fires an event per reaction trigger. |
| Comparison on a robot | See the validation in the pull request. |
