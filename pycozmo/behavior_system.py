"""

Behavior selection: configuration of choosers, activities and behaviors, and the choice of the next behavior.

Follows docs/specs/behavior-selection.md. The selection is pure: time comes from an injectable clock, randomness from an
injectable random generator and the state of the robot from a SelectionContext. It does not start any behavior.

"""

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .emotions import DecayGraph, Mood, Node
from .json_loader import get_json_files, load_json_file
from .needs import Bracket, NeedId, NeedsModel


__all__ = [
    "ScoringEntry",
    "ChooserConfig",
    "StrategyConfig",
    "ActivityConfig",
    "BehaviorConfig",
    "SelectionContext",
    "BehaviorSelector",
    "ActivityStrategy",
    "ActivitySelector",

    "load_activity_configs",
    "load_behavior_configs",
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


def _graph(data: dict[str, Any] | None) -> DecayGraph | None:
    if not data:
        return None
    return DecayGraph([Node(x=n['x'], y=n['y']) for n in data['nodes']])


@dataclass
class ScoringEntry:
    """ A behavior of a chooser, with its scoring. """
    behavior_id: str
    flat_score: float = 0.0
    repetition_penalty: DecayGraph | None = None
    running_penalty: DecayGraph | None = None


@dataclass
class ChooserConfig:
    """ Chooser of an activity: "StrictPriority", "Scoring" or "Selection". """
    type: str
    behaviors: list[ScoringEntry] = field(default_factory=list)
    score_bonus_for_current: DecayGraph | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ChooserConfig":
        entries = []
        for item in data.get('behaviors', []):
            if isinstance(item, str):
                entries.append(ScoringEntry(item))
                continue
            scoring = item.get('scoring', {})
            entries.append(ScoringEntry(
                behavior_id=item['behaviorID'],
                flat_score=float(scoring.get('flatScore', 0.0)),
                repetition_penalty=_graph(scoring.get('repetitionPenalty')),
                running_penalty=_graph(scoring.get('runningPenalty'))))
        return cls(data['type'], entries, _graph(data.get('scoreBonusForCurrentBehavior')))


@dataclass
class StrategyConfig:
    """ Activity strategy. Times are in seconds; a value of 0 or less means "not set". """
    type: str
    can_end_secs: float = -1.0
    should_end_secs: float = 60.0
    cooldown_base_secs: float = -1.0
    cooldown_randomness_secs: float = 0.0
    required_recent_on_treads_secs: float = -1.0
    need_id: str | None = None
    need_cooldown: DecayGraph | None = None
    need_cooldown_randomness: DecayGraph | None = None
    wants_to_run: dict[str, Any] | None = None
    higher_priority: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "StrategyConfig":
        return cls(
            type=data['type'],
            can_end_secs=float(data.get('activityCanEndDurationSecs', -1.0)),
            should_end_secs=float(data.get('activityShouldEndDurationSecs', 60.0)),
            cooldown_base_secs=float(data.get('cooldownBaseSecs', -1.0)),
            cooldown_randomness_secs=float(data.get('cooldownRandomnessSecs', 0.0)),
            required_recent_on_treads_secs=float(data.get('requiredRecentOnTreadsEventSecs', -1.0)),
            need_id=data.get('needId'),
            need_cooldown=_graph(data.get('needCooldownGraph')),
            need_cooldown_randomness=_graph(data.get('needCooldownRandomnessGraph')),
            wants_to_run=data.get('wantsToRunStrategyConfig'),
            higher_priority=data.get('higherPriorityStrategyConfig'))


@dataclass
class ActivityConfig:
    """ Activity: a strategy, and a chooser of behaviors or sub-activities with their priority. """
    id: str
    type: str
    strategy: StrategyConfig
    chooser: ChooserConfig | None = None
    sub_activities: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ActivityConfig":
        chooser_data = data.get('behaviorChooser') or data.get('universalChooser')
        subs = sorted(((s['activityID'], int(s.get('activityPriority', 0))) for s in data.get('subActivities', [])),
                      key=lambda s: s[1])
        return cls(
            id=data['activityID'],
            type=data['activityType'],
            strategy=StrategyConfig.from_json(data['activityStrategy']),
            chooser=ChooserConfig.from_json(chooser_data) if chooser_data else None,
            sub_activities=subs)


@dataclass
class BehaviorConfig:
    """ Behavior: its identity and the fields shared by all behavior classes; the rest stays in ``params``. """
    id: str
    class_name: str
    required_unlock_id: str | None = None
    anim_triggers: list[str] = field(default_factory=list)
    need: str | None = None
    need_bracket: str | None = None
    cooldown: DecayGraph | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "BehaviorConfig":
        return cls(
            id=data['behaviorID'],
            class_name=data['behaviorClass'],
            required_unlock_id=data.get('requiredUnlockId'),
            anim_triggers=list(data.get('animTriggers', [])),
            need=data.get('need'),
            need_bracket=data.get('needBracket'),
            cooldown=_graph(data.get('cooldown')),
            params=dict(data))


def _behavior_system_dir(resource_dir: str) -> str:
    return os.path.join(resource_dir, 'cozmo_resources', 'config', 'engine', 'behaviorSystem')


def load_activity_configs(resource_dir: str) -> dict[str, ActivityConfig]:
    """ Load all activities of the prepared resources. """
    base = _behavior_system_dir(resource_dir)
    activities: dict[str, ActivityConfig] = {}
    files = get_json_files(base, ['activities']) + [
        os.path.join(base, 'activities_config.json'), os.path.join(base, 'behavior_system_config.json')]
    for filename in files:
        if not os.path.isfile(filename):
            continue
        data = load_json_file(filename)
        for item in data if isinstance(data, list) else [data]:
            if 'activityID' in item and 'activityStrategy' in item:
                activities[item['activityID']] = ActivityConfig.from_json(item)
    return activities


def load_behavior_configs(resource_dir: str) -> dict[str, BehaviorConfig]:
    """ Load all behavior configurations of the prepared resources. """
    behaviors = {}
    for filename in get_json_files(_behavior_system_dir(resource_dir), ['behaviors']):
        data = load_json_file(filename)
        behaviors[data['behaviorID']] = BehaviorConfig.from_json(data)
    return behaviors


@dataclass
class SelectionContext:
    """
    What the selection needs to know about the robot.

    ``runnable`` tells whether a behavior can run now (blocks, charger, other conditions of the behavior);
    ``unlocked`` whether an unlock id is available; ``last_on_treads`` returns the time at which the robot was last
    put on its treads, or None.
    """
    clock: Callable[[], float] = time.monotonic
    rng: random.Random = field(default_factory=random.Random)
    needs: NeedsModel | None = None
    mood: Mood | None = None
    runnable: Callable[[str], bool] = lambda behavior_id: True
    unlocked: Callable[[str], bool] = lambda unlock_id: True
    last_on_treads: Callable[[], float | None] = lambda: None


@dataclass
class _BehaviorState:
    running: bool = False
    start_time: float = 0.0
    last_stop: float | None = None
    no_penalty_until: float = 0.0
    cooldown_end: float = 0.0


class BehaviorSelector:
    """ Chooses the next behavior of a chooser. """

    def __init__(
            self, chooser: ChooserConfig, context: SelectionContext,
            behaviors: dict[str, BehaviorConfig] | None = None) -> None:
        self.chooser = chooser
        self.context = context
        self.behaviors = behaviors or {}
        self.states = {entry.behavior_id: _BehaviorState() for entry in chooser.behaviors}

    def started(self, behavior_id: str) -> None:
        state = self.states.setdefault(behavior_id, _BehaviorState())
        state.running = True
        state.start_time = self.context.clock()

    def stopped(self, behavior_id: str, immediate_penalty: bool = True) -> None:
        """ The behavior stopped. Without ``immediate_penalty`` its repetition penalty starts after 1 s. """
        state = self.states.setdefault(behavior_id, _BehaviorState())
        now = self.context.clock()
        state.running = False
        state.last_stop = now
        if not immediate_penalty:
            state.no_penalty_until = now + NO_PENALTY_SECONDS
        config = self.behaviors.get(behavior_id)
        if config is not None and config.cooldown is not None and config.need is not None and self.context.needs:
            level = self.context.needs.level(NeedId(config.need))
            state.cooldown_end = now + float(config.cooldown.evaluate(level))

    def is_runnable(self, behavior_id: str) -> bool:
        state = self.states[behavior_id]
        if state.running:
            return True
        config = self.behaviors.get(behavior_id)
        if config is not None and config.required_unlock_id and not self.context.unlocked(config.required_unlock_id):
            return False
        if state.cooldown_end > self.context.clock():
            return False
        return bool(self.context.runnable(behavior_id))

    def score(self, entry: ScoringEntry) -> float:
        """ Score of a behavior, before the chooser adds its noise or its bonus. """
        state = self.states[entry.behavior_id]
        now = self.context.clock()
        if state.running:
            score = entry.flat_score
            if entry.running_penalty is not None:
                score *= float(entry.running_penalty.evaluate(now - state.start_time))
            return score
        if not self.is_runnable(entry.behavior_id):
            return 0.0
        score = entry.flat_score
        if entry.repetition_penalty is not None and state.last_stop is not None and now >= state.no_penalty_until:
            score *= float(entry.repetition_penalty.evaluate(now - state.last_stop))
        return score

    def choose(self) -> str | None:
        """ Identifier of the behavior to run, or None. """
        if self.chooser.type == "StrictPriority":
            for entry in self.chooser.behaviors:
                if self.states[entry.behavior_id].running or self.is_runnable(entry.behavior_id):
                    return entry.behavior_id
            return None
        if self.chooser.type == "Scoring":
            return self._choose_scoring()
        return None

    def _choose_scoring(self) -> str | None:
        now = self.context.clock()
        best = 0.0
        chosen = None
        for entry in self.chooser.behaviors:
            score = self.score(entry)
            if score <= 0.0:
                continue
            state = self.states[entry.behavior_id]
            if state.running:
                bonus = 0.0
                if self.chooser.score_bonus_for_current is not None:
                    bonus = float(self.chooser.score_bonus_for_current.evaluate(now - state.start_time))
                score = max(RUNNING_SCORE_MIN, bonus + score + RUNNING_SCORE_BONUS)
            else:
                score += self.context.rng.uniform(0.0, SCORE_NOISE)
            if score > best:
                best = score
                chosen = entry.behavior_id
        return chosen


class ActivityStrategy:
    """ Decides when an activity may start and when it must end. """

    def __init__(self, config: StrategyConfig, context: SelectionContext) -> None:
        self.config = config
        self.context = context
        self.cooldown_base = config.cooldown_base_secs
        self.cooldown_randomness = config.cooldown_randomness_secs
        self.cooldown = self.cooldown_base

    def update_needs(self) -> None:
        """ Set the cooldown from the level of the need, for the NeedBasedCooldown strategy. """
        config = self.config
        if config.type != "NeedBasedCooldown" or config.need_id is None or self.context.needs is None:
            return
        level = self.context.needs.level(NeedId(config.need_id))
        if config.need_cooldown is not None:
            self.cooldown_base = float(config.need_cooldown.evaluate(level))
            self.cooldown = self.cooldown_base
        if config.need_cooldown_randomness is not None:
            self.cooldown_randomness = float(config.need_cooldown_randomness.evaluate(level))

    def _in_bracket(self, rule: dict[str, Any] | None) -> bool:
        if rule is None or self.context.needs is None:
            return False
        if rule.get('strategyType') != "InNeedsBracket":
            return False
        return self.context.needs.bracket(NeedId(rule['need'])) == Bracket(rule['needBracket'])

    def wants_to_start(self, last_end: float | None, last_on_treads: float | None = None) -> bool:
        config = self.config
        now = self.context.clock()
        if config.type in ("SevereNeedTransition", "Spark", "Pyramid", "PlayWithHumans"):
            return False
        if self.cooldown > 1e-5 and last_end is not None:
            if now < last_end + self.cooldown:
                return False
            self.cooldown = self.cooldown_base + self.context.rng.uniform(0.0, self.cooldown_randomness)
        if config.required_recent_on_treads_secs > 1e-5:
            event = self.context.last_on_treads() if last_on_treads is None else last_on_treads
            if event is None or event <= 1e-5:
                return False
            if last_end is not None and event <= last_end:
                return False
            if now - event >= config.required_recent_on_treads_secs:
                return False
        if config.type == "Needs":
            return self._in_bracket(config.wants_to_run)
        return True

    def wants_to_end(self, start_time: float) -> bool:
        config = self.config
        now = self.context.clock()
        if config.type in ("SevereNeedTransition", "Spark", "Pyramid", "PlayWithHumans"):
            return True
        if config.can_end_secs > 1e-5 and now < start_time + config.can_end_secs - 1e-5:
            return False
        if config.should_end_secs > 1e-5 and start_time + config.should_end_secs + 1e-5 < now:
            return True
        if config.type == "Needs":
            if self._in_bracket(config.higher_priority):
                return True
            return not self._in_bracket(config.wants_to_run)
        return False


class ActivitySelector:
    """ Chooses among the sub-activities of a StrictPriority activity, then the behavior of the chosen one. """

    def __init__(
            self, activity: ActivityConfig, activities: dict[str, ActivityConfig], context: SelectionContext,
            behaviors: dict[str, BehaviorConfig] | None = None) -> None:
        self.activity = activity
        self.activities = activities
        self.context = context
        self.behaviors = behaviors
        self.strategies = {
            sub_id: ActivityStrategy(activities[sub_id].strategy, context)
            for sub_id, _ in activity.sub_activities if sub_id in activities}
        self.selectors: dict[str, BehaviorSelector] = {}
        for sub_id in self.strategies:
            chooser = activities[sub_id].chooser
            if chooser is not None:
                self.selectors[sub_id] = BehaviorSelector(chooser, context, behaviors)
        self.current: str | None = None
        self._start_time = 0.0
        self._last_end: dict[str, float] = {}

    def choose(self) -> tuple[str, str] | None:
        """ The sub-activity and the behavior to run, or None. """
        for _ in range(MAX_ROUNDS):
            if self.current is not None:
                if not self.strategies[self.current].wants_to_end(self._start_time):
                    behavior_id = self._behavior(self.current)
                    if behavior_id is not None:
                        return self.current, behavior_id
                self._last_end[self.current] = self.context.clock()
                self.current = None
            for sub_id, _ in self.activity.sub_activities:
                strategy = self.strategies.get(sub_id)
                if strategy is None:
                    continue
                strategy.update_needs()
                if strategy.wants_to_start(self._last_end.get(sub_id)):
                    self.current = sub_id
                    self._start_time = self.context.clock()
                    break
            if self.current is None:
                return None
        return None

    def _behavior(self, activity_id: str) -> str | None:
        selector = self.selectors.get(activity_id)
        return selector.choose() if selector is not None else None
