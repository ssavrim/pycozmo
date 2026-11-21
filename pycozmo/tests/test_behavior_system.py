
import json
import os
import random
import tempfile
import unittest
from typing import Any

from pycozmo.behavior_system import (
    ActivitySelector, ActivityStrategy, BehaviorSelector, ChooserConfig, SelectionContext, StrategyConfig,
    load_activity_configs, load_behavior_configs)
from pycozmo.tests.test_mood_needs import Clock, make_resources
from pycozmo.needs import NeedId, NeedsModel, load_needs_config


def chooser(kind, behaviors, **extra):
    return ChooserConfig.from_json(dict(type=kind, behaviors=behaviors, **extra))


class TestBehaviorSelector(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.runnable = {"A": True, "B": True, "C": True}
        self.context = SelectionContext(clock=self.clock, rng=random.Random(0), runnable=lambda b: self.runnable[b])

    def test_strict_priority(self):
        selector = BehaviorSelector(chooser("StrictPriority", ["A", "B", "C"]), self.context)
        self.assertEqual(selector.choose(), "A")
        self.runnable["A"] = False
        self.assertEqual(selector.choose(), "B")
        selector.started("A")       # a running behavior is kept even if it is not runnable any more
        self.assertEqual(selector.choose(), "A")
        self.runnable.update(B=False, C=False)
        selector.stopped("A")
        self.assertIsNone(selector.choose())

    def test_scoring_picks_best_with_noise_below_gap(self):
        selector = BehaviorSelector(chooser("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0}},
            {"behaviorID": "B", "scoring": {"flatScore": 2.0}}]), self.context)
        for _ in range(20):
            self.assertEqual(selector.choose(), "B")
        self.runnable["B"] = False
        self.assertEqual(selector.choose(), "A")
        self.runnable["A"] = False
        self.assertIsNone(selector.choose())

    def test_scoring_noise_range(self):
        calls = []

        class Rng(random.Random):
            def uniform(self, a, b):
                calls.append((a, b))
                return b

        context = SelectionContext(clock=self.clock, rng=Rng())
        selector = BehaviorSelector(chooser("Scoring", [{"behaviorID": "A", "scoring": {"flatScore": 1.0}}]), context)
        self.assertEqual(selector.choose(), "A")
        self.assertEqual(calls, [(0.0, 0.1)])

    def test_zero_flat_score_is_never_chosen(self):
        selector = BehaviorSelector(chooser("Scoring", [{"behaviorID": "A", "scoring": {"flatScore": 0}}]),
                                    self.context)
        self.assertIsNone(selector.choose())

    def test_repetition_penalty(self):
        penalty = {"nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]}
        selector = BehaviorSelector(chooser("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0, "repetitionPenalty": penalty}},
            {"behaviorID": "B", "scoring": {"flatScore": 0.5}}]), self.context)
        entry = selector.chooser.behaviors[0]
        self.assertEqual(selector.score(entry), 1.0)        # never stopped
        selector.started("A")
        self.clock.now = 5
        selector.stopped("A")
        self.assertEqual(selector.score(entry), 0.0)        # just stopped: fully penalized
        self.assertEqual(selector.choose(), "B")
        self.clock.now = 10
        self.assertAlmostEqual(selector.score(entry), 0.5)
        self.clock.now = 20
        self.assertAlmostEqual(selector.score(entry), 1.0)

    def test_stop_without_immediate_penalty(self):
        penalty = {"nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]}
        selector = BehaviorSelector(chooser("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0, "repetitionPenalty": penalty}}]), self.context)
        entry = selector.chooser.behaviors[0]
        selector.started("A")
        selector.stopped("A", immediate_penalty=False)
        self.assertEqual(selector.score(entry), 1.0)
        self.clock.now = 0.5
        self.assertEqual(selector.score(entry), 1.0)
        self.clock.now = 2
        self.assertAlmostEqual(selector.score(entry), 0.2)

    def test_running_bonus_keeps_current_behavior(self):
        selector = BehaviorSelector(chooser("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0}},
            {"behaviorID": "B", "scoring": {"flatScore": 1.05}}],
            scoreBonusForCurrentBehavior={"nodes": [{"x": 0, "y": 0.5}]}), self.context)
        selector.started("A")
        for _ in range(20):
            self.assertEqual(selector.choose(), "A")        # 0.5 + 1 + 0.1 beats 1.05 + noise

    def test_running_penalty(self):
        selector = BehaviorSelector(chooser("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0, "runningPenalty": {"nodes": [
                {"x": 0, "y": 1}, {"x": 10, "y": 0}]}}},
            {"behaviorID": "B", "scoring": {"flatScore": 0.5}}]), self.context)
        selector.started("A")
        self.clock.now = 10
        self.assertEqual(selector.choose(), "B")        # running score 0 -> not above 0

    def test_unlock(self):
        context = SelectionContext(clock=self.clock, unlocked=lambda u: u != "Locked")
        behaviors = load_behavior_configs_from({"A": {"requiredUnlockId": "Locked"}, "B": {}})
        selector = BehaviorSelector(chooser("StrictPriority", ["A", "B"]), context, behaviors)
        self.assertEqual(selector.choose(), "B")


def load_behavior_configs_from(data):
    from pycozmo.behavior_system import BehaviorConfig
    return {k: BehaviorConfig.from_json(dict(behaviorID=k, behaviorClass="X", **v)) for k, v in data.items()}


class TestStrategy(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.context = SelectionContext(clock=self.clock, rng=random.Random(0))

    def strategy(self, **data):
        return ActivityStrategy(StrategyConfig.from_json(dict(type="Simple", **data)), self.context)

    def test_cooldown(self):
        strategy = self.strategy(cooldownBaseSecs=30.0)
        self.assertTrue(strategy.wants_to_start(None))
        self.clock.now = 100
        self.assertFalse(strategy.wants_to_start(80))
        self.clock.now = 110
        self.assertTrue(strategy.wants_to_start(80))

    def test_cooldown_randomness_is_redrawn_from_base(self):
        strategy = self.strategy(cooldownBaseSecs=10.0, cooldownRandomnessSecs=10.0)
        self.clock.now = 1000
        for _ in range(5):
            self.assertTrue(strategy.wants_to_start(0))
            self.assertTrue(10 <= strategy.cooldown <= 20)

    def test_can_end_and_should_end(self):
        strategy = self.strategy(activityCanEndDurationSecs=5.0, activityShouldEndDurationSecs=20.0)
        self.assertFalse(strategy.wants_to_end(0))
        self.clock.now = 10
        self.assertFalse(strategy.wants_to_end(0))
        self.clock.now = 21
        self.assertTrue(strategy.wants_to_end(0))

    def test_default_should_end_is_60_seconds(self):
        strategy = self.strategy()
        self.clock.now = 59
        self.assertFalse(strategy.wants_to_end(0))
        self.clock.now = 61
        self.assertTrue(strategy.wants_to_end(0))

    def test_recent_on_treads(self):
        strategy = self.strategy(requiredRecentOnTreadsEventSecs=5.0)
        self.clock.now = 100
        self.assertFalse(strategy.wants_to_start(None))                 # never put on treads
        self.assertTrue(strategy.wants_to_start(None, last_on_treads=98.0))
        self.assertFalse(strategy.wants_to_start(None, last_on_treads=90.0))
        self.assertFalse(strategy.wants_to_start(99.0, last_on_treads=98.0))   # before the activity ended

    def test_unsupported_types(self):
        strategy = ActivityStrategy(StrategyConfig.from_json(dict(type="Spark")), self.context)
        self.assertFalse(strategy.wants_to_start(None))
        self.assertTrue(strategy.wants_to_end(0))

    def test_needs_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            make_resources(directory)
            needs = NeedsModel(load_needs_config(directory), clock=self.clock)
        self.context.needs = needs
        strategy = ActivityStrategy(StrategyConfig.from_json(dict(
            type="Needs", activityShouldEndDurationSecs=-1.0,
            wantsToRunStrategyConfig=dict(strategyType="InNeedsBracket", need="Energy", needBracket="Critical"),
            higherPriorityStrategyConfig=dict(strategyType="InNeedsBracket", need="Repair", needBracket="Critical"))),
            self.context)
        self.assertFalse(strategy.wants_to_start(None))
        needs.levels[NeedId.ENERGY] = 0.1                   # below the Warning threshold: Critical
        self.assertTrue(strategy.wants_to_start(None))
        self.assertFalse(strategy.wants_to_end(0))
        needs.levels[NeedId.REPAIR] = 0.1
        self.assertTrue(strategy.wants_to_end(0))           # higher priority need
        needs.levels[NeedId.REPAIR] = 1.0
        needs.levels[NeedId.ENERGY] = 1.0
        self.assertTrue(strategy.wants_to_end(0))           # no longer in need

    def test_need_based_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            make_resources(directory)
            needs = NeedsModel(load_needs_config(directory), clock=self.clock)
        self.context.needs = needs
        strategy = self.strategy(
            needId="Play", cooldownBaseSecs=1.0,
            needCooldownGraph={"nodes": [{"x": 0.0, "y": 1500.0}, {"x": 1.0, "y": 600.0}]},
            needCooldownRandomnessGraph={"nodes": [{"x": 0.0, "y": 0.0}]})
        strategy.config.type = "NeedBasedCooldown"
        needs.levels[NeedId.PLAY] = 0.5
        strategy.update_needs()
        self.assertAlmostEqual(strategy.cooldown, 1050.0)


class TestActivitySelector(unittest.TestCase):

    def make(self):
        data: dict[str, dict[str, Any]] = {
            "Top": dict(activityID="Top", activityType="StrictPriority", activityStrategy=dict(type="Simple"),
                        subActivities=[dict(activityID="Low", activityPriority=20),
                                       dict(activityID="High", activityPriority=10)]),
            "High": dict(activityID="High", activityType="BehaviorsOnly",
                         activityStrategy=dict(type="Simple", cooldownBaseSecs=100.0,
                                               activityShouldEndDurationSecs=30.0),
                         behaviorChooser=dict(type="StrictPriority", behaviors=["H1"])),
            "Low": dict(activityID="Low", activityType="BehaviorsOnly", activityStrategy=dict(type="Simple"),
                        behaviorChooser=dict(type="StrictPriority", behaviors=["L1"])),
        }
        from pycozmo.behavior_system import ActivityConfig
        return {k: ActivityConfig.from_json(v) for k, v in data.items()}

    def test_priority_order_and_cooldown(self):
        clock = Clock()
        activities = self.make()
        self.assertEqual([s for s, _ in activities["Top"].sub_activities], ["High", "Low"])
        context = SelectionContext(clock=clock)
        selector = ActivitySelector(activities["Top"], activities, context)
        self.assertEqual(selector.choose(), ("High", "H1"))
        clock.now = 10
        self.assertEqual(selector.choose(), ("High", "H1"))     # still running
        clock.now = 31                                          # should end: High goes in cooldown
        self.assertEqual(selector.choose(), ("Low", "L1"))
        clock.now = 140
        self.assertEqual(selector.choose(), ("High", "H1"))     # cooldown over: higher priority again

    def test_nothing_runnable(self):
        activities = self.make()
        context = SelectionContext(clock=Clock(), runnable=lambda b: False)
        selector = ActivitySelector(activities["Top"], activities, context)
        self.assertIsNone(selector.choose())


class TestLoading(unittest.TestCase):

    def test_load(self):
        with tempfile.TemporaryDirectory() as directory:
            base = os.path.join(directory, 'cozmo_resources', 'config', 'engine', 'behaviorSystem')
            os.makedirs(os.path.join(base, 'behaviors', 'x'))
            os.makedirs(os.path.join(base, 'activities'))

            def write(path, data):
                with open(os.path.join(base, path), 'w') as f:
                    f.write("// comment\n" + json.dumps(data))
            write('behaviors/x/a.json', dict(
                behaviorClass="ExpressNeeds", behaviorID="A", animTriggers=["T"], need="Repair", needBracket="Warning",
                cooldown={"nodes": [{"x": 0, "y": 20}]}, requiredUnlockId="U"))
            write('activities/one.json', dict(
                activityID="One", activityType="BehaviorsOnly", activityStrategy=dict(type="Simple"),
                behaviorChooser=dict(type="Scoring", behaviors=[dict(behaviorID="A", scoring=dict(flatScore=2))])))
            write('activities_config.json', [dict(
                activityID="Two", activityType="StrictPriority", activityStrategy=dict(type="Simple"),
                subActivities=[dict(activityID="One", activityPriority=1)])])
            write('behavior_system_config.json', dict(
                activityID="Three", activityType="StrictPriority", activityStrategy=dict(type="Simple")))
            activities = load_activity_configs(directory)
            behaviors = load_behavior_configs(directory)
        self.assertEqual(sorted(activities), ["One", "Three", "Two"])
        one = activities["One"].chooser
        assert one is not None
        self.assertEqual(one.behaviors[0].flat_score, 2.0)
        self.assertEqual(activities["Two"].sub_activities, [("One", 1)])
        self.assertEqual(behaviors["A"].need_bracket, "Warning")
        self.assertEqual(behaviors["A"].required_unlock_id, "U")
        self.assertEqual(behaviors["A"].anim_triggers, ["T"])
        self.assertIsNotNone(behaviors["A"].cooldown)
