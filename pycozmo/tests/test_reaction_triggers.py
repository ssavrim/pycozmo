
import json
import os
import random
import tempfile
import unittest
from typing import Any

from pycozmo.activity import SelectionContext
from pycozmo.behavior import ReactionTrigger, ReactionTriggerManager, load_reaction_triggers
from pycozmo.emotions import DecayGraph, EmotionType, Mood, Node
from pycozmo.tests.test_mood_needs import Clock


def trigger(name: str, behavior_id: str = "B", **extra: Any) -> ReactionTrigger:
    return ReactionTrigger.from_json(dict(reactionTrigger=name, behaviorID=behavior_id, **extra))


FIST_BUMP: dict[str, Any] = dict(behaviorObjectiveTriggerParams=[
    dict(behaviorObjective="Stacked", triggerCooldownTime_s=100.0, triggerProbability=0.5, triggerExpiration_s=3.0),
    dict(behaviorObjective="Bad", triggerCooldownTime_s=-1.0, triggerProbability=0.5, triggerExpiration_s=3.0),
    dict(behaviorObjective="Never", triggerCooldownTime_s=0.0, triggerProbability=1.5, triggerExpiration_s=3.0),
    dict(behaviorObjective="Instant", triggerCooldownTime_s=0.0, triggerProbability=1.0, triggerExpiration_s=0.0),
])


class FixedRandom(random.Random):
    def __init__(self, value: float) -> None:
        super().__init__()
        self.value = value

    def random(self) -> float:
        return self.value


class TestReactionTriggers(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.running_reaction = None
        self.carrying = False
        self.running: set[str] = set()
        self.runnable = True
        self.wants: set[str] = set()
        self.context = SelectionContext(
            clock=self.clock, rng=FixedRandom(0.1), runnable=lambda b: self.runnable,
            running=lambda b: b in self.running, running_reaction=lambda: self.running_reaction,
            carrying_block=lambda: self.carrying, wants_to_run=lambda t: t in self.wants)

    def names(self, manager):
        return [e.name for e in manager.check()]

    def test_parsing(self):
        entry = trigger("FistBump", **FIST_BUMP)
        assert entry.objective_triggers is not None
        self.assertEqual(len(entry.objective_triggers), 4)
        self.assertEqual(entry.objective_triggers[0].cooldown_secs, 100.0)
        frustration = trigger("Frustration", frustrationParams=dict(maxConfidence=-0.6, cooldownTime_s=60.0))
        self.assertEqual((frustration.max_confidence, frustration.cooldown_secs), (-0.6, 60.0))
        slope = trigger("RobotPlacedOnSlope", wantsToRunStrategyConfig=dict(strategyType="RobotPlacedOnSlope"))
        self.assertEqual(slope.wants_to_run_type, "RobotPlacedOnSlope")
        generic = trigger("RobotPickedUp", genericStrategyParams=dict(shouldResumeLast=True))
        self.assertTrue(generic.should_resume_last)
        self.assertIsNone(generic.objective_triggers)

    def test_generic_notification_is_used_once(self):
        manager = ReactionTriggerManager([trigger("RobotPickedUp")], self.context)
        self.assertEqual(self.names(manager), [])
        manager.notify("RobotPickedUp")
        self.assertEqual(self.names(manager), ["RobotPickedUp"])
        self.assertEqual(self.names(manager), [])
        manager.notify("RobotPickedUp")
        self.runnable = False
        self.assertEqual(self.names(manager), [])       # not runnable: not used
        self.runnable = True
        self.assertEqual(self.names(manager), ["RobotPickedUp"])

    def test_order_and_all_fire(self):
        manager = ReactionTriggerManager(
            [trigger("RobotPickedUp", "P"), trigger("CliffDetected", "C"), trigger("Sparked", "S")], self.context)
        for name in ("RobotPickedUp", "CliffDetected", "Sparked"):
            manager.notify(name)
        self.assertEqual([e.behavior_id for e in manager.check()], ["C", "P", "S"])

    def test_locks_are_counted(self):
        manager = ReactionTriggerManager([trigger("CliffDetected")], self.context)
        manager.notify("CliffDetected")
        manager.lock("CliffDetected")
        manager.lock("CliffDetected")
        self.assertTrue(manager.is_locked("CliffDetected"))
        self.assertEqual(self.names(manager), [])
        manager.unlock("CliffDetected")
        self.assertEqual(self.names(manager), [])
        manager.unlock("CliffDetected")
        manager.unlock("CliffDetected")             # extra unlock is ignored
        self.assertFalse(manager.is_locked("CliffDetected"))
        self.assertEqual(self.names(manager), ["CliffDetected"])

    def test_interruption_of_a_running_reaction(self):
        manager = ReactionTriggerManager([trigger("CliffDetected")], self.context)
        manager.notify("CliffDetected")
        self.running_reaction = "RobotPickedUp"
        self.assertEqual(self.names(manager), [])
        self.context.can_interrupt = lambda t, running: running == "RobotPickedUp"
        self.assertEqual(self.names(manager), ["CliffDetected"])

    def test_wants_to_run_strategy(self):
        manager = ReactionTriggerManager(
            [trigger("PlacedOnCharger", wantsToRunStrategyConfig=dict(strategyType="PlacedOnCharger"))], self.context)
        self.assertEqual(self.names(manager), [])
        self.wants.add("PlacedOnCharger")
        self.assertEqual(self.names(manager), ["PlacedOnCharger"])
        self.assertEqual(self.names(manager), ["PlacedOnCharger"])      # a state: triggers while it holds

    def test_fist_bump_arming_and_expiration(self):
        manager = ReactionTriggerManager([trigger("FistBump", "F", **FIST_BUMP)], self.context)
        self.assertEqual(self.names(manager), [])
        manager.objective_achieved("Stacked")       # random 0.1 < 0.5: armed until 3 s
        self.clock.now = 2
        self.assertEqual(self.names(manager), ["FistBump"])
        self.assertEqual(self.names(manager), [])    # consumed
        manager.objective_achieved("Stacked")
        self.clock.now = 50                          # within the cooldown of 100 s: not armed
        self.assertEqual(self.names(manager), [])
        self.clock.now = 200
        manager.objective_achieved("Stacked")
        self.clock.now = 204                         # expired
        self.assertEqual(self.names(manager), [])
        self.clock.now = 204.5
        self.assertEqual(self.names(manager), [])    # and stays disarmed

    def test_fist_bump_probability(self):
        manager = ReactionTriggerManager([trigger("FistBump", **FIST_BUMP)], self.context)
        self.context.rng = FixedRandom(0.6)
        manager.objective_achieved("Stacked")
        self.assertEqual(self.names(manager), [])

    def test_fist_bump_rejects_invalid_parameters_and_unknown_objectives(self):
        manager = ReactionTriggerManager([trigger("FistBump", **FIST_BUMP)], self.context)
        for objective in ("Bad", "Never", "Instant", "Unknown"):
            manager.objective_achieved(objective)
        self.assertEqual(self.names(manager), [])

    def test_fist_bump_conditions(self):
        manager = ReactionTriggerManager([trigger("FistBump", "F", **FIST_BUMP)], self.context)
        manager.objective_achieved("Stacked")
        self.carrying = True
        self.assertEqual(self.names(manager), [])
        self.carrying = False
        self.running.add("F")
        self.assertEqual(self.names(manager), [])
        self.running.clear()
        self.runnable = False
        self.assertEqual(self.names(manager), [])
        self.runnable = True
        self.assertEqual(self.names(manager), ["FistBump"])     # still armed

    def make_mood(self, value):
        graph = DecayGraph([Node(0, 1)])
        confident = EmotionType("Confident", graph, graph, clock=self.clock)
        mood = Mood({"Confident": confident}, {}, clock=self.clock)
        confident.set(value)
        return mood

    def test_frustration(self):
        minor = trigger("Frustration", "Minor", frustrationParams=dict(maxConfidence=-0.6, cooldownTime_s=60.0))
        major = trigger("Frustration", "Major", frustrationParams=dict(maxConfidence=-0.9))
        manager = ReactionTriggerManager([minor, major], self.context)
        self.context.mood = self.make_mood(-0.5)
        self.assertEqual(self.names(manager), [])                       # not frustrated enough
        self.context.mood = self.make_mood(-0.7)
        self.assertEqual([e.behavior_id for e in manager.check()], ["Minor"])
        self.clock.now = 30
        self.assertEqual(self.names(manager), [])                       # cooldown of the minor one
        self.clock.now = 61
        self.assertEqual(self.names(manager), ["Frustration"])
        self.context.mood = self.make_mood(-0.95)
        self.clock.now = 62
        self.assertEqual([e.behavior_id for e in manager.check()], ["Major"])   # no cooldown for this one
        self.assertEqual([e.behavior_id for e in manager.check()], ["Major"])

    def test_frustration_not_while_frustration_runs_and_not_without_mood(self):
        entry = trigger("Frustration", "Minor", frustrationParams=dict(maxConfidence=-0.6))
        manager = ReactionTriggerManager([entry], self.context)
        self.assertEqual(self.names(manager), [])                       # no mood
        self.context.mood = self.make_mood(-0.9)
        self.running_reaction = "Frustration"
        self.assertEqual(self.names(manager), [])
        self.running_reaction = None
        self.assertEqual(self.names(manager), ["Frustration"])

    def test_load(self):
        with tempfile.TemporaryDirectory() as directory:
            base = os.path.join(directory, 'cozmo_resources', 'config', 'engine', 'behaviorSystem')
            os.makedirs(base)
            with open(os.path.join(base, 'reactionTrigger_behavior_map.json'), 'w') as f:
                f.write("// comment\n" + json.dumps(dict(reactionTriggerBehaviorMap=[
                    dict(reactionTrigger="Frustration", behaviorID="Minor",
                         frustrationParams=dict(maxConfidence=-0.6, cooldownTime_s=60.0)),
                    dict(reactionTrigger="CliffDetected", behaviorID="ReactToCliff"),
                    dict(reactionTrigger="Frustration", behaviorID="Major",
                         frustrationParams=dict(maxConfidence=-0.9))])))
            triggers = load_reaction_triggers(directory)
        self.assertEqual([t.behavior_id for t in triggers], ["Minor", "ReactToCliff", "Major"])
        manager = ReactionTriggerManager(triggers)
        self.assertEqual([t.behavior_id for t in manager.entries], ["ReactToCliff", "Minor", "Major"])
