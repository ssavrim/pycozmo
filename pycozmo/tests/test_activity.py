
import json
import os
import random
import tempfile
import unittest
from typing import Any

from pycozmo.activity import (
    Activity, ActivityStrategy, BehaviorChooser, BehaviorsActivity, SelectionContext, StrictPriorityActivity,
    from_dict, load_activities)
from pycozmo.behavior import load_behavior_configs
from pycozmo.needs import NeedId, NeedsModel, load_needs_config
from pycozmo.tests.test_mood_needs import Clock, make_resources


def chooser(kind, behaviors, **extra):
    return BehaviorChooser.from_json(dict(type=kind, behaviors=behaviors, **extra))


class TestBehaviorChooser(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.runnable = {"A": True, "B": True, "C": True}
        self.context = SelectionContext(clock=self.clock, rng=random.Random(0), runnable=lambda b: self.runnable[b])

    def make(self, kind, behaviors, **extra):
        result = chooser(kind, behaviors, **extra)
        result.context = self.context
        return result

    def test_strict_priority(self):
        c = self.make("StrictPriority", ["A", "B", "C"])
        self.assertEqual(c.choose(), "A")
        self.runnable["A"] = False
        self.assertEqual(c.choose(), "B")
        c.started("A")       # a running behavior is kept even if it is not runnable any more
        self.assertEqual(c.choose(), "A")
        self.runnable.update(B=False, C=False)
        c.stopped("A")
        self.assertIsNone(c.choose())

    def test_selection_chooses_nothing(self):
        self.assertIsNone(self.make("Selection", []).choose())
        self.assertIsNone(self.make("Selection", []).get_sorted_choices())

    def test_scoring_picks_best(self):
        c = self.make("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0}},
            {"behaviorID": "B", "scoring": {"flatScore": 2.0}}])
        for _ in range(20):
            self.assertEqual(c.choose(), "B")
        self.runnable["B"] = False
        self.assertEqual(c.choose(), "A")
        self.runnable["A"] = False
        self.assertIsNone(c.choose())

    def test_scoring_noise_range(self):
        calls = []

        class Rng(random.Random):
            def uniform(self, a, b):
                calls.append((a, b))
                return b

        self.context.rng = Rng()
        c = self.make("Scoring", [{"behaviorID": "A", "scoring": {"flatScore": 1.0}}])
        self.assertEqual(c.choose(), "A")
        self.assertEqual(calls, [(0.0, 0.1)])

    def test_zero_flat_score_is_never_chosen(self):
        self.assertIsNone(self.make("Scoring", [{"behaviorID": "A", "scoring": {"flatScore": 0}}]).choose())

    def test_equal_scores_keep_the_first(self):
        class Rng(random.Random):
            def uniform(self, a, b):
                return 0.0

        self.context.rng = Rng()
        c = self.make("Scoring", [
            {"behaviorID": "B", "scoring": {"flatScore": 1.0}},
            {"behaviorID": "A", "scoring": {"flatScore": 1.0}}])
        self.assertEqual(c.choose(), "B")

    def test_sorted_choices(self):
        c = self.make("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0}},
            {"behaviorID": "B", "scoring": {"flatScore": 2.0}},
            {"behaviorID": "C", "scoring": {"flatScore": 0.0}}])
        self.assertEqual(c.get_sorted_choices(), ["B", "A"])

    def test_repetition_penalty(self):
        penalty = {"nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]}
        c = self.make("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0, "repetitionPenalty": penalty}},
            {"behaviorID": "B", "scoring": {"flatScore": 0.5}}])
        self.assertEqual(c._score(0), 1.0)         # never stopped
        c.started("A")
        self.clock.now = 5
        c.stopped("A")
        self.assertEqual(c._score(0), 0.0)         # just stopped: fully penalized
        self.assertEqual(c.choose(), "B")
        self.clock.now = 10
        self.assertAlmostEqual(c._score(0), 0.5)
        self.clock.now = 20
        self.assertAlmostEqual(c._score(0), 1.0)

    def test_apply_repetition_penalty_stops_the_behavior(self):
        penalty = {"nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]}
        c = self.make("Scoring", [{"behaviorID": "A", "scoring": {"flatScore": 1.0, "repetitionPenalty": penalty}}])
        self.clock.now = 3
        c.apply_repetition_penalty("A")
        self.assertEqual(c._score(0), 0.0)
        self.clock.now = 8
        c.apply_repetition_penalty(0)
        self.assertEqual(c._score(0), 0.0)
        with self.assertRaises(TypeError):
            c.apply_repetition_penalty(1.5)

    def test_stop_without_immediate_penalty(self):
        penalty = {"nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]}
        c = self.make("Scoring", [{"behaviorID": "A", "scoring": {"flatScore": 1.0, "repetitionPenalty": penalty}}])
        c.started("A")
        c.stopped("A", immediate_penalty=False)
        self.assertEqual(c._score(0), 1.0)
        self.clock.now = 0.5
        self.assertEqual(c._score(0), 1.0)
        self.clock.now = 2
        self.assertAlmostEqual(c._score(0), 0.2)

    def test_running_bonus_keeps_current_behavior(self):
        c = self.make("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0}},
            {"behaviorID": "B", "scoring": {"flatScore": 1.05}}],
            scoreBonusForCurrentBehavior={"nodes": [{"x": 0, "y": 0.5}]})
        c.started("A")
        for _ in range(20):
            self.assertEqual(c.choose(), "A")        # 0.5 + 1 + 0.1 beats 1.05 + noise

    def test_running_penalty(self):
        c = self.make("Scoring", [
            {"behaviorID": "A", "scoring": {"flatScore": 1.0, "runningPenalty": {"nodes": [
                {"x": 0, "y": 1}, {"x": 10, "y": 0}]}}},
            {"behaviorID": "B", "scoring": {"flatScore": 0.5}}])
        c.started("A")
        self.clock.now = 10
        self.assertEqual(c.choose(), "B")        # running score 0: not above 0

    def test_reset(self):
        c = self.make("StrictPriority", ["A"])
        c.started("A")
        c.reset()
        self.runnable["A"] = False
        self.assertIsNone(c.choose())

    def test_choose_takes_the_context(self):
        c = chooser("StrictPriority", ["A"])
        self.assertEqual(c.choose(SelectionContext(runnable=lambda b: True)), "A")
        self.assertIsNone(c.choose(SelectionContext(runnable=lambda b: False)))


class TestStrategy(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.context = SelectionContext(clock=self.clock, rng=random.Random(0))

    def strategy(self, **data):
        return ActivityStrategy.from_json(dict(type="Simple", **data))

    def test_defaults(self):
        strategy = ActivityStrategy("Simple")
        self.assertEqual(strategy.should_end_secs, 60.0)
        self.assertEqual(strategy.cooldown_secs, -1.0)

    def test_cooldown(self):
        strategy = self.strategy(cooldownBaseSecs=30.0)
        self.assertTrue(strategy.wants_to_start(self.context, None))
        self.clock.now = 100
        self.assertFalse(strategy.wants_to_start(self.context, 80))
        self.clock.now = 110
        self.assertTrue(strategy.wants_to_start(self.context, 80))

    def test_cooldown_randomness_is_redrawn_from_base(self):
        strategy = self.strategy(cooldownBaseSecs=10.0, cooldownRandomnessSecs=10.0)
        self.clock.now = 1000
        for _ in range(5):
            self.assertTrue(strategy.wants_to_start(self.context, 0))
            self.assertTrue(10 <= strategy.cooldown_secs <= 20)

    def test_can_end_and_should_end(self):
        strategy = self.strategy(activityCanEndDurationSecs=5.0, activityShouldEndDurationSecs=20.0)
        self.assertFalse(strategy.wants_to_end(self.context, 0))
        self.clock.now = 10
        self.assertFalse(strategy.wants_to_end(self.context, 0))
        self.clock.now = 21
        self.assertTrue(strategy.wants_to_end(self.context, 0))

    def test_default_should_end_is_60_seconds(self):
        strategy = self.strategy()
        self.clock.now = 59
        self.assertFalse(strategy.wants_to_end(self.context, 0))
        self.clock.now = 61
        self.assertTrue(strategy.wants_to_end(self.context, 0))

    def test_recent_on_treads(self):
        strategy = self.strategy(requiredRecentOnTreadsEventSecs=5.0)
        self.clock.now = 100
        self.assertFalse(strategy.wants_to_start(self.context, None))                    # never put on treads
        self.assertTrue(strategy.wants_to_start(self.context, None, last_on_treads=98.0))
        self.assertFalse(strategy.wants_to_start(self.context, None, last_on_treads=90.0))
        self.assertFalse(strategy.wants_to_start(self.context, 99.0, last_on_treads=98.0))  # before the end
        self.context.last_on_treads = lambda: 99.0
        self.assertTrue(strategy.wants_to_start(self.context, None))

    def test_unsupported_types(self):
        strategy = ActivityStrategy.from_json(dict(type="Spark"))
        self.assertFalse(strategy.wants_to_start(self.context, None))
        self.assertTrue(strategy.wants_to_end(self.context, 0))

    def needs(self):
        with tempfile.TemporaryDirectory() as directory:
            make_resources(directory)
            needs = NeedsModel(load_needs_config(directory), clock=self.clock)
        self.context.needs = needs
        return needs

    def test_needs_strategy(self):
        needs = self.needs()
        strategy = ActivityStrategy.from_json(dict(
            type="Needs", activityShouldEndDurationSecs=-1.0,
            wantsToRunStrategyConfig=dict(strategyType="InNeedsBracket", need="Energy", needBracket="Critical"),
            higherPriorityStrategyConfig=dict(strategyType="InNeedsBracket", need="Repair", needBracket="Critical")))
        self.assertFalse(strategy.wants_to_start(self.context, None))
        needs.levels[NeedId.ENERGY] = 0.1                   # below the Warning threshold: Critical
        self.assertTrue(strategy.wants_to_start(self.context, None))
        self.assertFalse(strategy.wants_to_end(self.context, 0))
        needs.levels[NeedId.REPAIR] = 0.1
        self.assertTrue(strategy.wants_to_end(self.context, 0))      # higher priority need
        needs.levels[NeedId.REPAIR] = 1.0
        needs.levels[NeedId.ENERGY] = 1.0
        self.assertTrue(strategy.wants_to_end(self.context, 0))      # no longer in need

    def test_need_based_cooldown(self):
        needs = self.needs()
        strategy = self.strategy(
            needId="Play", cooldownBaseSecs=1.0,
            needCooldownGraph={"nodes": [{"x": 0.0, "y": 1500.0}, {"x": 1.0, "y": 600.0}]},
            needCooldownRandomnessGraph={"nodes": [{"x": 0.0, "y": 0.0}]})
        strategy.type = "NeedBasedCooldown"
        needs.levels[NeedId.PLAY] = 0.5
        strategy.update_needs(self.context)
        self.assertAlmostEqual(strategy.cooldown_secs, 1050.0)
        needs.levels[NeedId.PLAY] = 1.0
        strategy.update_needs(self.context)
        self.assertAlmostEqual(strategy.cooldown_secs, 600.0)


def make_activities() -> dict[str, Activity]:
    data: dict[str, dict[str, Any]] = {
        "Top": dict(activityID="Top", activityType="StrictPriority", activityStrategy=dict(type="Simple"),
                    subActivities=[dict(activityID="Low", activityPriority=20),
                                   dict(activityID="High", activityPriority=10)]),
        "High": dict(activityID="High", activityType="BehaviorsOnly",
                     activityStrategy=dict(type="Simple", cooldownBaseSecs=100.0, activityShouldEndDurationSecs=30.0),
                     behaviorChooser=dict(type="StrictPriority", behaviors=["H1"])),
        "Low": dict(activityID="Low", activityType="BehaviorsOnly", activityStrategy=dict(type="Simple"),
                    behaviorChooser=dict(type="StrictPriority", behaviors=["L1"])),
    }
    return {k: from_dict(v) for k, v in data.items()}


class TestActivities(unittest.TestCase):

    def test_types_and_strategy(self):
        activities = make_activities()
        self.assertIsInstance(activities["Top"], StrictPriorityActivity)
        self.assertIsInstance(activities["High"], BehaviorsActivity)
        self.assertEqual(activities["High"].strategy, "Simple")
        self.assertEqual(activities["High"].strategy_config.cooldown_base_secs, 100.0)

    def test_priority_order_and_cooldown(self):
        clock = Clock()
        activities = make_activities()
        top = activities["Top"]
        assert isinstance(top, StrictPriorityActivity)
        self.assertEqual([s for s, _ in top.sub_activities], ["High", "Low"])
        context = SelectionContext(clock=clock)
        self.assertEqual(top.choose(context, activities), "H1")
        self.assertEqual(top.current, "High")
        clock.now = 10
        self.assertEqual(top.choose(context, activities), "H1")     # still running
        clock.now = 31                                              # should end: High goes in cooldown
        self.assertEqual(top.choose(context, activities), "L1")
        self.assertEqual(top.current, "Low")
        clock.now = 140
        self.assertEqual(top.choose(context, activities), "H1")     # cooldown over: higher priority again
        top.reset()
        self.assertIsNone(top.current)

    def test_nothing_runnable(self):
        activities = make_activities()
        top = activities["Top"]
        self.assertIsNone(top.choose(SelectionContext(clock=Clock(), runnable=lambda b: False), activities))

    def test_activity_without_chooser(self):
        activity = Activity("X", "Other", "Simple")
        self.assertIsNone(activity.get_chooser())
        self.assertIsNone(activity.choose(SelectionContext()))

    def test_socialize_and_needs_activities_have_a_chooser(self):
        chooser_data = dict(type="StrictPriority", behaviors=["X"])
        socialize = from_dict(dict(
            activityID="S", activityType="Socialize", activityStrategy=dict(type="Simple"),
            behaviorChooser=chooser_data, interludeBehaviorChooser=chooser_data, maxNumFindFacesSearchIterations=1,
            requiredObjectives=[]))
        needs = from_dict(dict(
            activityID="N", activityType="NeedsExpression", activityStrategy=dict(type="Simple"),
            behaviorChooser=chooser_data))
        for activity in (socialize, needs):
            self.assertEqual(activity.choose(SelectionContext()), "X")

    def test_feeding_universal_chooser(self):
        activity = from_dict(dict(
            activityID="F", activityType="Feeding", activityStrategy=dict(type="Simple"),
            universalChooser=dict(type="StrictPriority", behaviors=["X", "Y"])))
        self.assertEqual(activity.choose(SelectionContext(runnable=lambda b: b == "Y")), "Y")


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
            activities = load_activities(directory)
            behaviors = load_behavior_configs(directory)
        self.assertEqual(sorted(activities), ["One", "Three", "Two"])
        one = activities["One"]
        assert isinstance(one, BehaviorsActivity)
        assert one.behavior_chooser is not None
        self.assertEqual(one.behavior_chooser.behaviors[0]["scoring"]["flatScore"], 2)
        two = activities["Two"]
        assert isinstance(two, StrictPriorityActivity)
        self.assertEqual(two.sub_activities, [("One", 1)])
        self.assertEqual(behaviors["A"].need_bracket, "Warning")
        self.assertEqual(behaviors["A"].required_unlock_id, "U")
        self.assertEqual(behaviors["A"].anim_triggers, ["T"])
        self.assertIsNotNone(behaviors["A"].cooldown)
        self.assertEqual(behaviors["A"].params["behaviorClass"], "ExpressNeeds")
