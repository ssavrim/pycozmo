"""

Behavior representation and reading.

"""

import os
import time
from typing import Any, Self

from . import event
from . import client
from .logger import logger
from .activity import SelectionContext
from .emotions import DecayGraph, Node
from .json_loader import get_json_files, load_json_file


__all__ = [
    "ReactionTrigger",
    "Behavior",

    "BehaviorConfig",
    "ObjectiveTriggerParams",
    "ReactionTriggerManager",
    "REACTION_TRIGGER_ORDER",

    "load_behaviors",
    "load_behavior_configs",
    "load_reaction_triggers",
    "load_reaction_trigger_behavior_map",
]


class BehaviorConfig:
    """ Configuration of a behavior: its identity and the fields shared by all behavior classes. """
    __slots__ = [
        "id",
        "class_name",
        "required_unlock_id",
        "anim_triggers",
        "need",
        "need_bracket",
        "cooldown",
        "params",
    ]

    def __init__(self, behavior_id: str, class_name: str, required_unlock_id: str | None = None,
                 anim_triggers: list[str] | None = None, need: str | None = None, need_bracket: str | None = None,
                 cooldown: DecayGraph | None = None, params: dict[str, Any] | None = None) -> None:
        self.id = str(behavior_id)
        self.class_name = str(class_name)
        self.required_unlock_id = required_unlock_id
        self.anim_triggers = list(anim_triggers or [])
        self.need = need
        self.need_bracket = need_bracket
        self.cooldown = cooldown
        self.params = dict(params or {})

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        cooldown = None
        if 'cooldown' in data:
            cooldown = DecayGraph([Node(x=n['x'], y=n['y']) for n in data['cooldown']['nodes']])
        return cls(
            behavior_id=data['behaviorID'],
            class_name=data['behaviorClass'],
            required_unlock_id=data.get('requiredUnlockId'),
            anim_triggers=data.get('animTriggers'),
            need=data.get('need'),
            need_bracket=data.get('needBracket'),
            cooldown=cooldown,
            params=data)


# Reaction triggers in the order in which they are tried.
REACTION_TRIGGER_ORDER = (
    "CliffDetected", "CubeMoved", "FacePositionUpdated", "FistBump", "Frustration", "Hiccup", "MotorCalibration",
    "NoPreDockPoses", "ObjectPositionUpdated", "PlacedOnCharger", "PetInitialDetection", "RobotFalling",
    "RobotPickedUp", "RobotPlacedOnSlope", "ReturnedToTreads", "RobotOnBack", "RobotOnFace", "RobotOnSide",
    "RobotShaken", "Sparked", "UnexpectedMovement",
)


class ObjectiveTriggerParams:
    """ Parameters of the trigger of a reaction by an achieved behavior objective. """
    __slots__ = [
        "objective",
        "cooldown_secs",
        "probability",
        "expiration_secs",
    ]

    def __init__(self, objective: str, cooldown_secs: float, probability: float, expiration_secs: float) -> None:
        self.objective = str(objective)
        self.cooldown_secs = float(cooldown_secs)
        self.probability = float(probability)
        self.expiration_secs = float(expiration_secs)

    def is_valid(self) -> bool:
        return self.cooldown_secs >= 0.0 and 0.0 < self.probability <= 1.0 and self.expiration_secs > 0.0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            objective=data['behaviorObjective'],
            cooldown_secs=data['triggerCooldownTime_s'],
            probability=data['triggerProbability'],
            expiration_secs=data['triggerExpiration_s'])


class ReactionTrigger:
    """ Reaction trigger representation class. """
    __slots__ = [
        "name",
        "behavior_id",
        "should_resume_last",
        "objective_triggers",
        "max_confidence",
        "cooldown_secs",
        "wants_to_run_type",
    ]

    def __init__(self, name: str, behavior_id: str, should_resume_last: bool | None = False,
                 objective_triggers: list[ObjectiveTriggerParams] | None = None,
                 max_confidence: float | None = None, cooldown_secs: float | None = None,
                 wants_to_run_type: str | None = None) -> None:
        self.name = str(name)
        self.behavior_id = str(behavior_id)
        self.should_resume_last = bool(should_resume_last)
        self.objective_triggers = objective_triggers
        self.max_confidence = max_confidence
        self.cooldown_secs = cooldown_secs
        self.wants_to_run_type = wants_to_run_type

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        objectives = None
        if 'behaviorObjectiveTriggerParams' in data:
            objectives = [ObjectiveTriggerParams.from_json(d) for d in data['behaviorObjectiveTriggerParams']]
        frustration = data.get('frustrationParams', {})
        return cls(name=data['reactionTrigger'],
                   behavior_id=data['behaviorID'],
                   should_resume_last=data.get('genericStrategyParams', {}).get('shouldResumeLast'),
                   objective_triggers=objectives,
                   max_confidence=frustration.get('maxConfidence'),
                   cooldown_secs=frustration.get('cooldownTime_s'),
                   wants_to_run_type=data.get('wantsToRunStrategyConfig', {}).get('strategyType'))


class Behavior(event.Dispatcher):
    """ Behavior representation class. """

    def __init__(self, cli: client.Client, conf: Any) -> None:
        super().__init__()
        self.cli = cli
        self.conf = conf

    def get_id(self) -> str:
        return str(self.conf["behaviorID"])

    def activate(self) -> None:
        logger.warning("Behavior '{}' not implemented.".format(self.get_id()))
        self.cli.conn.post_event(event.EvtBehaviorDone, self.cli)

    def deactivate(self) -> None:
        pass


class BehaviorPlayAnim(Behavior):
    """ Play a sequence of animation triggers. """

    def __init__(self, cli: client.Client, conf: Any):
        super().__init__(cli, conf)
        self.anim_triggers = conf.get("animTriggers", [])
        self.current_trigger = 0
        self.add_handler(event.EvtAnimationCompleted, self._on_animation_completed)

    def activate(self) -> None:
        self.current_trigger = 0
        if self.anim_triggers:
            anim_trigger = self.anim_triggers[self.current_trigger]
            self.current_trigger += 1
            self.cli.play_anim_group(anim_trigger)

    def _on_animation_completed(self, cli):
        # TODO: Support additional animation triggers.
        self.cli.conn.post_event(event.EvtBehaviorDone, self.cli)

    def deactivate(self) -> None:
        self.cli.cancel_anim()


class BehaviorPlayArbitraryAnim(BehaviorPlayAnim):
    """ Play a random animation trigger. """

    def activate(self) -> None:
        # TODO: Pick random animation trigger and put it in sequence
        super().activate()


class BehaviorReactToCliff(BehaviorPlayAnim):
    """ ReactToCliff behavior - currently, just plays animation. """

    def activate(self) -> None:
        self.anim_triggers = ("ReactToCliff", )
        super().activate()


class BehaviorDriveOffCharger(Behavior):

    def activate(self) -> None:
        # extraDistanceToDrive_mm = float(self.conf["extraDistanceToDrive_mm"])
        # TODO: Play wake up animation?
        # TODO: Drive and wait for completion.
        self.cli.conn.post_event(event.EvtBehaviorDone, self.cli)

    def deactivate(self) -> None:
        # TODO: Cancel
        pass


def get_behavior_class_from_dict(data):
    """ Choose a behavior class, based on the behaviorClass JSON attribute. """
    # TODO: Replace with a behavior package.
    class_map = {
        "PlayAnim": BehaviorPlayAnim,
        "PlayArbitraryAnim": BehaviorPlayArbitraryAnim,
        "ReactToCliff": BehaviorReactToCliff,
        "DriveOffCharger": BehaviorDriveOffCharger,
    }
    cls = class_map.get(data["behaviorClass"], Behavior)
    return cls


def load_behaviors(resource_dir: str, cli: client.Client) -> dict[str, Behavior]:

    start_time = time.perf_counter()

    behavior_files = get_json_files(
        resource_dir, [os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'behaviors')])
    behaviors = {}
    for filename in behavior_files:
        data = load_json_file(filename)
        cls = get_behavior_class_from_dict(data)
        behaviors[data['behaviorID']] = cls(cli, data)

    logger.debug("Loaded {} behaviors in {:.02f} s.".format(len(behaviors), time.perf_counter() - start_time))

    return behaviors


def load_behavior_configs(resource_dir: str) -> dict[str, BehaviorConfig]:
    """ Load the configuration of all behaviors, without creating them. """
    behavior_files = get_json_files(
        resource_dir, [os.path.join('cozmo_resources', 'config', 'engine', 'behaviorSystem', 'behaviors')])
    configs = {}
    for filename in behavior_files:
        data = load_json_file(filename)
        configs[data['behaviorID']] = BehaviorConfig.from_json(data)
    return configs


class ReactionTriggerManager:
    """ Decides which reaction triggers fire (docs/specs/reaction-triggers.md). It does not start any behavior. """

    def __init__(self, triggers: list[ReactionTrigger], context: SelectionContext | None = None) -> None:
        self.context = context or SelectionContext()
        order = {name: index for index, name in enumerate(REACTION_TRIGGER_ORDER)}
        self.entries = sorted(
            (t for t in triggers), key=lambda t: order.get(t.name, len(order)))     # stable: file order kept
        self._locks: dict[str, int] = {}
        self._notified: dict[int, bool] = {}
        self._armed: dict[int, float] = {}          # entry -> expiration time
        self._last_fist_bump: float | None = None
        self._last_frustration: dict[int, float] = {}

    def lock(self, trigger: str) -> None:
        self._locks[trigger] = self._locks.get(trigger, 0) + 1

    def unlock(self, trigger: str) -> None:
        if self._locks.get(trigger, 0) > 0:
            self._locks[trigger] -= 1

    def is_locked(self, trigger: str) -> bool:
        return self._locks.get(trigger, 0) > 0

    def notify(self, trigger: str) -> None:
        """ An event of the trigger occurred; it is kept until a check uses it. """
        for index, entry in enumerate(self.entries):
            if entry.name == trigger:
                self._notified[index] = True

    def objective_achieved(self, objective: str) -> None:
        """ A behavior objective was achieved: it may arm the fist bump trigger. """
        context = self.context
        now = context.clock()
        for index, entry in enumerate(self.entries):
            for params in entry.objective_triggers or []:
                if params.objective != objective or not params.is_valid():
                    continue
                if self._last_fist_bump is not None and now - self._last_fist_bump <= params.cooldown_secs:
                    continue
                if context.rng.random() < params.probability:
                    self._armed[index] = now + params.expiration_secs

    def _should_trigger(self, index: int, entry: ReactionTrigger) -> bool:
        context = self.context
        now = context.clock()
        if entry.objective_triggers is not None:
            expiration = self._armed.get(index)
            if expiration is None:
                return False
            if now > expiration:
                del self._armed[index]
                return False
            if context.running(entry.behavior_id) or context.carrying_block():
                return False
            return bool(context.runnable(entry.behavior_id))
        runnable = context.running(entry.behavior_id) or context.runnable(entry.behavior_id)
        if entry.max_confidence is not None:
            if context.running_reaction() == "Frustration" or context.mood is None:
                return False
            if context.mood.values().get("Confident", 0.0) >= entry.max_confidence:
                return False
            last = self._last_frustration.get(index)
            if entry.cooldown_secs and last is not None and now - last <= entry.cooldown_secs:
                return False
            return bool(runnable)
        if entry.wants_to_run_type is not None:
            return bool(context.wants_to_run(entry.wants_to_run_type)) and bool(runnable)
        return self._notified.get(index, False) and bool(runnable)

    def check(self, context: SelectionContext | None = None) -> list[ReactionTrigger]:
        """ The entries that trigger now, in the order in which they are tried; the last one wins. """
        if context is not None:
            self.context = context
        running = self.context.running_reaction()
        fired = []
        for index, entry in enumerate(self.entries):
            if self.is_locked(entry.name):
                continue
            if running is not None and running != entry.name and not self.context.can_interrupt(entry.name, running):
                continue
            if not self._should_trigger(index, entry):
                continue
            fired.append(entry)
            self._notified[index] = False
            now = self.context.clock()
            if entry.objective_triggers is not None:
                self._armed.pop(index, None)
                self._last_fist_bump = now
            if entry.max_confidence is not None:
                self._last_frustration[index] = now
        return fired


def load_reaction_triggers(resource_dir: str) -> list[ReactionTrigger]:
    """ Load all entries of the reaction trigger map, in the order of the file. """
    filename = os.path.join(resource_dir, 'cozmo_resources', 'config',
                            'engine', 'behaviorSystem', 'reactionTrigger_behavior_map.json')
    json_data = load_json_file(filename)
    return [ReactionTrigger.from_json(trigger) for trigger in json_data['reactionTriggerBehaviorMap']]


def load_reaction_trigger_behavior_map(resource_dir: str) -> dict[str, ReactionTrigger]:

    start_time = time.perf_counter()

    reaction_trigger_behavior_map = {}
    filename = os.path.join(resource_dir, 'cozmo_resources', 'config',
                            'engine', 'behaviorSystem', 'reactionTrigger_behavior_map.json')

    json_data = load_json_file(filename)
    for trigger in json_data['reactionTriggerBehaviorMap']:
        reaction_trigger_behavior_map[trigger['reactionTrigger']] = ReactionTrigger.from_json(trigger)

    logger.debug("Loaded {} entry reaction trigger behavior map in {:.02f} s.".format(
        len(reaction_trigger_behavior_map), time.perf_counter() - start_time))

    return reaction_trigger_behavior_map
