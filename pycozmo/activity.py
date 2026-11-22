"""

Activity representation and reading, and the choice of the next behavior (docs/specs/behavior-selection.md).

The choice is pure: time comes from an injectable clock, randomness from an injectable random generator and the state
of the robot from a SelectionContext. It does not start any behavior.

"""

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Self

from .logger import logger
from .emotions import DecayGraph, Mood, Node
from .json_loader import get_json_files, load_json_file
from .needs import Bracket, NeedId, NeedsModel


__all__ = [
    "SelectionContext",
    "BehaviorChooser",
    "ActivityStrategy",
    "Activity",
    "StrictPriorityActivity",

    "load_activities",
]


# Largest random value added to the score of a behavior that is not running.
SCORE_NOISE = 0.1
# Smallest score of the behavior that is running, and what is added to it.
RUNNING_SCORE_MIN = 0.01
RUNNING_SCORE_BONUS = 0.1
# Seconds without repetition penalty after a stop that asked for none.
NO_PENALTY_SECONDS = 1.0
# Rounds of the choice of a sub-activity.
MAX_ROUNDS = 5
# Strategy types that are not supported: they never start.
UNSUPPORTED_STRATEGIES = ("SevereNeedTransition", "Spark", "Pyramid", "PlayWithHumans")


def _graph(data: dict[str, Any] | None) -> DecayGraph | None:
    if not data:
        return None
    return DecayGraph([Node(x=n['x'], y=n['y']) for n in data['nodes']])


@dataclass
class SelectionContext:
    """
    What the choice needs to know about the robot.

    ``runnable`` tells whether a behavior can run now (unlocked, blocks, charger and the other conditions of the
    behavior); ``last_on_treads`` returns the time at which the robot was last put on its treads, or None.

    For the reaction triggers: ``running`` tells whether a behavior is running, ``running_reaction`` returns the
    reaction trigger that is running, or None, ``can_interrupt(trigger, running_trigger)`` whether a trigger may
    interrupt the running one, ``carrying_block`` whether the robot carries a cube and ``wants_to_run(strategy_type)``
    whether the state condition of that name holds.
    """
    clock: Callable[[], float] = time.monotonic
    rng: random.Random = field(default_factory=random.Random)
    needs: NeedsModel | None = None
    mood: Mood | None = None
    runnable: Callable[[str], bool] = lambda behavior_id: True
    last_on_treads: Callable[[], float | None] = lambda: None
    running: Callable[[str], bool] = lambda behavior_id: False
    running_reaction: Callable[[], str | None] = lambda: None
    can_interrupt: Callable[[str, str], bool] = lambda trigger, running_trigger: False
    carrying_block: Callable[[], bool] = lambda: False
    wants_to_run: Callable[[str], bool] = lambda strategy_type: False


@dataclass
class _BehaviorState:
    running: bool = False
    start_time: float = 0.0
    last_stop: float | None = None
    no_penalty_until: float = 0.0


class BehaviorChooser:
    """
    Chooser of the next behavior: "StrictPriority", "Scoring" or "Selection".

    ``behaviors`` is the list of the configuration: identifiers for a StrictPriority chooser, entries with a
    ``scoring`` (``flatScore``, ``repetitionPenalty``, ``runningPenalty``) for a Scoring chooser.
    """

    __slots__ = [
        "choice_type",
        "behaviors",
        "iteration",
        "score_bonus_for_current",
        "context",
        "_entries",
        "_states",
    ]

    def __init__(self,
                 choice_type: str,
                 behaviors: list,
                 score_bonus_for_current: DecayGraph | None = None,
                 context: SelectionContext | None = None) -> None:
        self.choice_type = str(choice_type)
        self.behaviors = behaviors
        self.iteration = 0
        self.score_bonus_for_current = score_bonus_for_current
        self.context = context or SelectionContext()
        self._entries: list[tuple[str, float, DecayGraph | None, DecayGraph | None]] = []
        for item in behaviors:
            if isinstance(item, str):
                self._entries.append((item, 0.0, None, None))
                continue
            scoring = item.get('scoring', {})
            self._entries.append((
                item['behaviorID'],
                float(scoring.get('flatScore', 0.0)),
                _graph(scoring.get('repetitionPenalty')),
                _graph(scoring.get('runningPenalty'))))
        self._states = {entry[0]: _BehaviorState() for entry in self._entries}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            choice_type=data['type'],
            behaviors=data.get('behaviors', []),
            score_bonus_for_current=_graph(data.get('scoreBonusForCurrentBehavior')),
        )

    def reset(self) -> None:
        self.iteration = 0
        self._states = {entry[0]: _BehaviorState() for entry in self._entries}

    def started(self, behavior_id: str) -> None:
        """ The behavior started. """
        state = self._states[behavior_id]
        state.running = True
        state.start_time = self.context.clock()

    def stopped(self, behavior_id: str, immediate_penalty: bool = True) -> None:
        """ The behavior stopped. Without ``immediate_penalty`` its repetition penalty starts after 1 s. """
        state = self._states[behavior_id]
        now = self.context.clock()
        state.running = False
        state.last_stop = now
        if not immediate_penalty:
            state.no_penalty_until = now + NO_PENALTY_SECONDS

    def apply_repetition_penalty(self, ref: str | int) -> None:
        """ A behavior was run: it stopped now. """
        if isinstance(ref, int):
            ref = self._entries[ref][0]
        elif not isinstance(ref, str):
            raise TypeError('Invalid behavior index: {}'.format(ref))
        self.stopped(ref)

    def is_runnable(self, behavior_id: str) -> bool:
        return self._states[behavior_id].running or bool(self.context.runnable(behavior_id))

    def _score(self, index: int) -> float:
        """ Score of a behavior, before the chooser adds its noise or its bonus. """
        behavior_id, flat_score, repetition_penalty, running_penalty = self._entries[index]
        state = self._states[behavior_id]
        now = self.context.clock()
        if state.running:
            score = flat_score
            if running_penalty is not None:
                score *= float(running_penalty.evaluate(now - state.start_time))
            return score
        if not self.context.runnable(behavior_id):
            return 0.0
        score = flat_score
        if repetition_penalty is not None and state.last_stop is not None and now >= state.no_penalty_until:
            score *= float(repetition_penalty.evaluate(now - state.last_stop))
        return score

    def _final_scores(self) -> list[tuple[str, float]]:
        """ Final score of the behaviors that score above 0, in the order of the configuration. """
        now = self.context.clock()
        result = []
        for index, entry in enumerate(self._entries):
            score = self._score(index)
            if score <= 0.0:
                continue
            state = self._states[entry[0]]
            if state.running:
                bonus = 0.0
                if self.score_bonus_for_current is not None:
                    bonus = float(self.score_bonus_for_current.evaluate(now - state.start_time))
                score = max(RUNNING_SCORE_MIN, bonus + score + RUNNING_SCORE_BONUS)
            else:
                score += self.context.rng.uniform(0.0, SCORE_NOISE)
            result.append((entry[0], score))
        return result

    def choose(self, context: SelectionContext | None = None) -> str | None:
        """ Identifier of the behavior to run, or None. """
        if context is not None:
            self.context = context
        if self.choice_type == 'StrictPriority':
            for behavior_id, *_ in self._entries:
                if self.is_runnable(behavior_id):
                    return behavior_id
            return None
        if self.choice_type == 'Scoring':
            best = 0.0
            chosen = None
            for behavior_id, score in self._final_scores():
                if score > best:
                    best = score
                    chosen = behavior_id
            return chosen
        return None

    def get_sorted_choices(self) -> list[str] | None:
        """ Behaviors in the order in which they would be tried; None for a Selection chooser. """
        if self.choice_type == 'Selection':
            return None
        if self.choice_type == 'StrictPriority':
            return [entry[0] for entry in self._entries if self.is_runnable(entry[0])]
        if self.choice_type == 'Scoring':
            ranked = sorted(self._final_scores(), key=lambda item: item[1], reverse=True)
            return [behavior_id for behavior_id, _ in ranked]
        raise ValueError('Unknown choice type: {}'.format(self.choice_type))


class Objective:
    __slots__ = [
        "objective",
        "behavior_id",
        "ignore_if_locked",
        "probability_to_require_objective",
        "random_completions_needed_min",
        "random_completions_needed_max",
    ]

    def __init__(self,
                 objective: str,
                 behavior_id: str,
                 ignore_if_locked: str,
                 probability_to_require_objective: float,
                 random_completions_needed_min: int | None = 0,
                 random_completions_needed_max: int | None = 0) -> None:
        self.objective = str(objective)
        self.behavior_id = str(behavior_id)
        self.ignore_if_locked = str(ignore_if_locked)
        self.probability_to_require_objective = float(probability_to_require_objective)
        self.random_completions_needed_min = \
            int(random_completions_needed_min) if random_completions_needed_min is not None else 0
        self.random_completions_needed_max = \
            int(random_completions_needed_max) if random_completions_needed_max is not None else 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(objective=data['objective'],
                   behavior_id=data['behaviorID'],
                   ignore_if_locked=data['ignoreIfLocked'],
                   probability_to_require_objective=data['probabilityToRequireObjective'],
                   random_completions_needed_min=data.get('randomCompletionsNeededMin', 0),
                   random_completions_needed_max=data.get('randomCompletionsNeededMax', 0))


class ActivityStrategy:
    """
    Decides when an activity may start and when it must end.

    Times are in seconds; a value of 0 or less means "not set".
    """

    __slots__ = [
        "type",
        "can_end_secs",
        "should_end_secs",
        "cooldown_base_secs",
        "cooldown_randomness_secs",
        "required_recent_on_treads_secs",
        "need_id",
        "need_cooldown",
        "need_cooldown_randomness",
        "wants_to_run",
        "higher_priority",
        "cooldown_secs",
    ]

    def __init__(self,
                 strategy_type: str,
                 can_end_secs: float = -1.0,
                 should_end_secs: float = 60.0,
                 cooldown_base_secs: float = -1.0,
                 cooldown_randomness_secs: float = 0.0,
                 required_recent_on_treads_secs: float = -1.0,
                 need_id: str | None = None,
                 need_cooldown: DecayGraph | None = None,
                 need_cooldown_randomness: DecayGraph | None = None,
                 wants_to_run: dict[str, Any] | None = None,
                 higher_priority: dict[str, Any] | None = None) -> None:
        self.type = str(strategy_type)
        self.can_end_secs = float(can_end_secs)
        self.should_end_secs = float(should_end_secs)
        self.cooldown_base_secs = float(cooldown_base_secs)
        self.cooldown_randomness_secs = float(cooldown_randomness_secs)
        self.required_recent_on_treads_secs = float(required_recent_on_treads_secs)
        self.need_id = need_id
        self.need_cooldown = need_cooldown
        self.need_cooldown_randomness = need_cooldown_randomness
        self.wants_to_run = wants_to_run
        self.higher_priority = higher_priority
        self.cooldown_secs = self.cooldown_base_secs

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            strategy_type=data['type'],
            can_end_secs=data.get('activityCanEndDurationSecs', -1.0),
            should_end_secs=data.get('activityShouldEndDurationSecs', 60.0),
            cooldown_base_secs=data.get('cooldownBaseSecs', -1.0),
            cooldown_randomness_secs=data.get('cooldownRandomnessSecs', 0.0),
            required_recent_on_treads_secs=data.get('requiredRecentOnTreadsEventSecs', -1.0),
            need_id=data.get('needId'),
            need_cooldown=_graph(data.get('needCooldownGraph')),
            need_cooldown_randomness=_graph(data.get('needCooldownRandomnessGraph')),
            wants_to_run=data.get('wantsToRunStrategyConfig'),
            higher_priority=data.get('higherPriorityStrategyConfig'))

    def update_needs(self, context: SelectionContext) -> None:
        """ NeedBasedCooldown: set the cooldown from the level of the need. """
        if self.type != "NeedBasedCooldown" or self.need_id is None or context.needs is None:
            return
        level = context.needs.level(NeedId(self.need_id))
        if self.need_cooldown is not None:
            self.cooldown_base_secs = float(self.need_cooldown.evaluate(level))
            self.cooldown_secs = self.cooldown_base_secs
        if self.need_cooldown_randomness is not None:
            self.cooldown_randomness_secs = float(self.need_cooldown_randomness.evaluate(level))

    @staticmethod
    def _in_bracket(context: SelectionContext, rule: dict[str, Any] | None) -> bool:
        if rule is None or context.needs is None or rule.get('strategyType') != "InNeedsBracket":
            return False
        return context.needs.bracket(NeedId(rule['need'])) == Bracket(rule['needBracket'])

    def wants_to_start(
            self, context: SelectionContext, last_end: float | None = None,
            last_on_treads: float | None = None) -> bool:
        """ ``last_end`` is the time at which the activity last ended, if it ever did. """
        if self.type in UNSUPPORTED_STRATEGIES:
            return False
        now = context.clock()
        if self.cooldown_secs > 1e-5 and last_end is not None:
            if now < last_end + self.cooldown_secs:
                return False
            self.cooldown_secs = self.cooldown_base_secs + context.rng.uniform(0.0, self.cooldown_randomness_secs)
        if self.required_recent_on_treads_secs > 1e-5:
            event = context.last_on_treads() if last_on_treads is None else last_on_treads
            if event is None or event <= 1e-5:
                return False
            if last_end is not None and event <= last_end:
                return False
            if now - event >= self.required_recent_on_treads_secs:
                return False
        if self.type == "Needs":
            return self._in_bracket(context, self.wants_to_run)
        return True

    def wants_to_end(self, context: SelectionContext, start_time: float) -> bool:
        """ ``start_time`` is the time at which the activity started. """
        if self.type in UNSUPPORTED_STRATEGIES:
            return True
        now = context.clock()
        if self.can_end_secs > 1e-5 and now < start_time + self.can_end_secs - 1e-5:
            return False
        if self.should_end_secs > 1e-5 and start_time + self.should_end_secs + 1e-5 < now:
            return True
        if self.type == "Needs":
            if self._in_bracket(context, self.higher_priority):
                return True
            return not self._in_bracket(context, self.wants_to_run)
        return False


class Activity:
    """ Activity representation class. """

    __slots__ = [
        "id",
        "type",
        "strategy",
        "strategy_config",
    ]

    def __init__(self,
                 activity_id: str,
                 activity_type: str,
                 strategy: str) -> None:
        self.id = str(activity_id)
        self.type = str(activity_type)
        self.strategy = str(strategy)
        self.strategy_config = ActivityStrategy(self.strategy)

    def get_chooser(self) -> BehaviorChooser | None:
        """ Chooser of the behaviors of the activity, if it has one. """
        return None

    def choose(self, context: SelectionContext | None = None,
               activities: dict[str, "Activity"] | None = None) -> str | None:
        """
        Identifier of the next behavior of the activity, or None.

        ``activities`` are the activities that a container activity chooses from.
        """
        del activities
        chooser = self.get_chooser()
        if chooser is None:
            return None
        return chooser.choose(context)


class BehaviorsActivity(Activity):
    __slots__ = [
        "behavior_chooser"
    ]

    def __init__(self,
                 behavior_chooser: BehaviorChooser | None,
                 *args: Any, **kwargs: Any) -> None:
        self.behavior_chooser = behavior_chooser
        super().__init__(*args, **kwargs)

    def get_chooser(self) -> BehaviorChooser | None:
        return self.behavior_chooser

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            BehaviorChooser.from_json(data['behaviorChooser'])
            if 'behaviorChooser' in data else None,
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class VoiceCommandActivity(Activity):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class FeedingActivity(Activity):
    __slots__ = [
        "universal_chooser",
    ]

    def __init__(self,
                 universal_chooser: list[str] | None,
                 *args: Any, **kwargs: Any) -> None:
        self.universal_chooser = universal_chooser
        super().__init__(*args, **kwargs)

    def get_chooser(self) -> BehaviorChooser | None:
        if self.universal_chooser is None:
            return None
        return BehaviorChooser('StrictPriority', self.universal_chooser)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            universal_chooser=data['universalChooser']['behaviors']
            if 'universalChooser' in data else None,
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class FreeplayActivity(Activity):
    __slots__ = [
        "cube_only_activity",
        "face_only_activity",
        "face_and_cube_activity",
        "no_face_no_cube_activity",
        "sub_activities",
    ]

    def __init__(self,
                 cube_only_activity: str,
                 face_only_activity: str,
                 face_and_cube_activity: str,
                 no_face_no_cube_activity: str,
                 sub_activities: list[Any] | None,
                 *args: Any, **kwargs: Any) -> None:
        self.cube_only_activity = str(cube_only_activity)
        self.face_only_activity = str(face_only_activity)
        self.face_and_cube_activity = str(face_and_cube_activity)
        self.no_face_no_cube_activity = str(no_face_no_cube_activity)
        self.sub_activities = sub_activities

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            cube_only_activity=data['desiredActivityNames']['cubeOnlyActivityName'],
            face_only_activity=data['desiredActivityNames']['faceOnlyActivityName'],
            face_and_cube_activity=data['desiredActivityNames']['faceAndCubeActivityName'],
            no_face_no_cube_activity=data['desiredActivityNames']['noFaceNoCubeActivityName'],
            sub_activities=data.get('subActivities'),
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class SparkedActivity(Activity):
    __slots__ = [
        "require_spark",
        "min_time",
        "max_time",
        "reps",
        "behavior_objective",
        "soft_spark_trigger",
        "behavior_chooser",
        "sub_activity_delegate",
        "spark_success_trigger",
        "spark_fail_trigger",
        "drive_start_trigger",
        "drive_loop_trigger",
        "drive_stop_trigger",
    ]

    def __init__(self,
                 require_spark: str,
                 min_time_secs: float,
                 max_time_secs: float,
                 reps: int,
                 behavior_objective: str,
                 soft_spark_trigger: str,
                 behavior_chooser: BehaviorChooser | None = None,
                 sub_activity_delegate: Activity | None = None,
                 spark_success_trigger: str | None = None,
                 spark_fail_trigger: str | None = None,
                 drive_start_trigger: str | None = None,
                 drive_loop_trigger: str | None = None,
                 drive_stop_trigger: str | None = None,
                 *args: Any, **kwargs: Any) -> None:
        self.require_spark = str(require_spark)
        self.min_time = float(min_time_secs)
        self.max_time = float(max_time_secs)
        self.reps = int(reps)
        self.behavior_objective = str(behavior_objective)
        self.soft_spark_trigger = str(soft_spark_trigger)

        self.behavior_chooser = behavior_chooser
        self.sub_activity_delegate = sub_activity_delegate
        self.spark_success_trigger = spark_success_trigger
        self.spark_fail_trigger = spark_fail_trigger

        self.drive_start_trigger = drive_start_trigger
        self.drive_loop_trigger = drive_loop_trigger
        self.drive_stop_trigger = drive_stop_trigger

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        if 'subActivityDelegate' in data:
            sub_act_delegate = from_dict(data['subActivityDelegate'])
        else:
            sub_act_delegate = None
        return cls(
            require_spark=data['requireSpark'],
            min_time_secs=data['minTimeSecs'],
            max_time_secs=data['maxTimeSecs'],
            reps=data['numberOfRepetitions'],
            behavior_objective=data['behaviorObjective'],
            soft_spark_trigger=data['softSparkTrigger'],
            behavior_chooser=BehaviorChooser.from_json(data['behaviorChooser'])
            if 'behaviorChooser' in data else None,
            sub_activity_delegate=sub_act_delegate,
            spark_success_trigger=data.get('sparksSuccessTrigger'),
            spark_fail_trigger=data.get('sparksFailTrigger'),
            drive_start_trigger=data.get('driveStartAnimTrigger'),
            drive_loop_trigger=data.get('driveLoopAnimTrigger'),
            drive_stop_trigger=data.get('driveStopAnimTrigger'),
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class PyramidActivity(Activity):
    __slots__ = [
        "setup_chooser",
        "build_chooser",
        "interlude_chooser",
        "needs_action_id",
    ]

    def __init__(self,
                 setup_chooser: BehaviorChooser,
                 build_chooser: BehaviorChooser,
                 interlude_chooser: BehaviorChooser | None = None,
                 needs_action_id: str | None = None,
                 *args: Any, **kwargs: Any) -> None:
        self.setup_chooser = setup_chooser
        self.build_chooser = build_chooser
        self.interlude_chooser = interlude_chooser
        self.needs_action_id = needs_action_id

        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            setup_chooser=BehaviorChooser.from_json(data['setupChooser']),
            build_chooser=BehaviorChooser.from_json(data['buildChooser']),
            interlude_chooser=BehaviorChooser.from_json(data['interludeBehaviorChooser'])
            if 'interludeBehaviorChooser' in data else None,
            needs_action_id=data.get('needsActionID'),
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class SocializeActivity(Activity):
    __slots__ = [
        "behavior_chooser",
        "interlude_chooser",
        "max_face_iterations",
        "required_objectives",
    ]

    def __init__(self,
                 behavior_chooser: BehaviorChooser,
                 interlude_chooser: BehaviorChooser,
                 max_face_iterations: int,
                 required_objectives: list[Objective],
                 *args: Any, **kwargs: Any) -> None:
        self.behavior_chooser = behavior_chooser
        self.interlude_chooser = interlude_chooser
        self.max_face_iterations = int(max_face_iterations)
        self.required_objectives = required_objectives

        super().__init__(*args, **kwargs)

    def get_chooser(self) -> BehaviorChooser | None:
        return self.behavior_chooser

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            behavior_chooser=BehaviorChooser.from_json(data['behaviorChooser']),
            interlude_chooser=BehaviorChooser.from_json(data['interludeBehaviorChooser']),
            max_face_iterations=data['maxNumFindFacesSearchIterations'],
            required_objectives=[Objective.from_json(d) for d in data['requiredObjectives']],
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class NeedsActivity(Activity):
    __slots__ = [
        "behavior_chooser"
    ]

    def __init__(self,
                 behavior_chooser: BehaviorChooser,
                 *args: Any, **kwargs: Any) -> None:
        self.behavior_chooser = behavior_chooser
        super().__init__(*args, **kwargs)

    def get_chooser(self) -> BehaviorChooser | None:
        return self.behavior_chooser

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            behavior_chooser=BehaviorChooser.from_json(data['behaviorChooser']),
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])


class StrictPriorityActivity(Activity):
    """ Activity that chooses among sub-activities, taken by increasing priority number. """

    __slots__ = [
        "sub_activities",
        "current",
        "start_time",
        "last_end",
    ]

    def __init__(self,
                 sub_activities: list[tuple[str, int]],
                 *args: Any, **kwargs: Any) -> None:
        self.sub_activities = sorted(sub_activities, key=lambda sub: sub[1])
        self.current: str | None = None
        self.start_time = 0.0
        self.last_end: dict[str, float] = {}
        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            sub_activities=[(sub['activityID'], int(sub.get('activityPriority', 0)))
                            for sub in data.get('subActivities', [])],
            activity_id=data['activityID'],
            activity_type=data['activityType'],
            strategy=data['activityStrategy']['type'])

    def reset(self) -> None:
        self.current = None
        self.last_end = {}

    def choose(self, context: SelectionContext | None = None,
               activities: dict[str, Activity] | None = None) -> str | None:
        """ Behavior of the current sub-activity, which is chosen first, if needed (``self.current``). """
        context = context or SelectionContext()
        activities = activities or {}
        for _ in range(MAX_ROUNDS):
            current = activities.get(self.current) if self.current is not None else None
            if current is not None:
                if not current.strategy_config.wants_to_end(context, self.start_time):
                    behavior_id = current.choose(context, activities)
                    if behavior_id is not None:
                        return behavior_id
                assert self.current is not None
                self.last_end[self.current] = context.clock()
            self.current = None
            for sub_id, _ in self.sub_activities:
                sub = activities.get(sub_id)
                if sub is None:
                    continue
                sub.strategy_config.update_needs(context)
                if sub.strategy_config.wants_to_start(context, self.last_end.get(sub_id)):
                    self.current = sub_id
                    self.start_time = context.clock()
                    break
            if self.current is None:
                return None
        return None


def _from_dict(info: dict) -> Activity:
    if info['activityType'] == 'VoiceCommand':
        return VoiceCommandActivity.from_json(info)

    elif info['activityType'] == 'BehaviorsOnly':
        return BehaviorsActivity.from_json(info)

    elif info['activityType'] == 'Feeding':
        return FeedingActivity.from_json(info)

    elif info['activityType'] == 'Freeplay':
        return FreeplayActivity.from_json(info)

    elif info['activityType'] == 'Sparked':
        return SparkedActivity.from_json(info)

    elif info['activityType'] == 'BuildPyramid':
        return PyramidActivity.from_json(info)

    elif info['activityType'] == 'Socialize':
        return SocializeActivity.from_json(info)

    elif info['activityType'] == 'NeedsExpression':
        return NeedsActivity.from_json(info)
    elif info['activityType'] == 'StrictPriority':
        return StrictPriorityActivity.from_json(info)

    else:
        return Activity(
            activity_id=info['activityID'],
            activity_type=info['activityType'],
            strategy=info['activityStrategy']['type']
        )


def from_dict(info: dict) -> Activity:
    """ Create an activity, and its strategy, from its configuration. """
    activity = _from_dict(info)
    activity.strategy_config = ActivityStrategy.from_json(info['activityStrategy'])
    return activity


def load_activities(resource_dir: str) -> dict[str, Activity]:
    """ Load activity map from cozmo resources. """

    # TODO: cozmo_resources/config/engine/needs_action_config.json
    # TODO: cozmo_resources/config/engine/do_a_trick_weights.json

    start_time = time.perf_counter()

    activity_folders = [
        os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'activities_config.json'),
        os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'behavior_system_config.json'),
        os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'activities')
    ]
    activity_files = get_json_files(resource_dir, activity_folders)

    activities = {}

    for filename in activity_files:
        json_data = load_json_file(filename)
        if isinstance(json_data, list):
            for activity in json_data:
                activities[activity['activityID']] = from_dict(activity)
        else:
            activities[json_data['activityID']] = from_dict(json_data)

    logger.debug("Loaded {} activities in {:.02f} s.".format(len(activities), time.perf_counter() - start_time))

    return activities
