
import json
import os
import random
import tempfile
import unittest

from pycozmo.emotions import DecayGraph, EmotionEvent, EmotionType, Mood, Node, load_emotion_events, load_emotion_types
from pycozmo.needs import Bracket, NeedId, NeedsModel, load_needs_config


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _write(base: str, name: str, data: str) -> None:
    with open(os.path.join(base, name), 'w') as f:
        f.write(data)


def make_resources(directory: str) -> None:
    base = os.path.join(directory, 'cozmo_resources', 'config', 'engine')
    os.makedirs(base)
    _write(base, 'mood_config.json', json.dumps({
        "decayGraphs": [
            {"emotionType": "default", "nodes": [{"x": 0, "y": 1}, {"x": 10, "y": 1}, {"x": 20, "y": 0}]},
        ],
        "defaultRepetitionPenalty": {"nodes": [{"x": 0, "y": 0}, {"x": 30, "y": 1}]},
    }))
    needs = {"MaximumNeedLevel": 1, "MinimumNeedLevel": 0.03, "DecayPeriodSeconds": 60,
             "FullnessDecayCooldownRepair": 100, "FullnessDecayCooldownEnergy": 100, "FullnessDecayCooldownPlay": 100,
             "BrokenPartThreshold0": 0.9, "BrokenPartThreshold1": 0.6, "BrokenPartThreshold2": 0.3}
    for need, bracket in (("Repair", 0.27), ("Energy", 0.21), ("Play", 0.14)):
        needs["InitialNeedLevel" + need] = 1
        needs["BracketLevel" + need + "Full"] = 0.99
        needs["BracketLevel" + need + "Normal"] = 0.5
        needs["BracketLevel" + need + "Warning"] = bracket
        needs["BracketLevel" + need + "Critical"] = 0
    # The comment checks the tolerant JSON reading, as in the original files.
    _write(base, 'needs_config.json', "// exported from a sheet\n" + json.dumps(needs))
    _write(base, 'needs_decay_config.json', json.dumps({
        "DecayRates": {
            "ConnectedDecayRatesRepair": [{"Threshold": 0.03, "DecayPerMinute": 0.01}],
            # Deliberately unsorted: the loader sorts by descending threshold.
            "ConnectedDecayRatesEnergy": [
                {"Threshold": 0.3, "DecayPerMinute": 0.05}, {"Threshold": 0.6, "DecayPerMinute": 0.1}],
            "ConnectedDecayRatesPlay": [{"Threshold": 0.5, "DecayPerMinute": 0.0}],
            "UnconnectedDecayRatesEnergy": [{"Threshold": 0.6, "DecayPerMinute": 0.001}],
        },
        "DecayModifiers": {
            "ConnectedDecayModifiersRepair": [
                {"Threshold": 0.0, "OtherNeedsAffected": [{"OtherNeedID": "Play", "Multiplier": 1}]},
                {"Threshold": 0.5, "OtherNeedsAffected": [{"OtherNeedID": "Energy", "Multiplier": 2}]},
            ],
        },
    }))
    _write(base, 'needs_action_config.json', json.dumps({"actionDeltas": [
        {"actionId": "Feed", "repairDelta": 0, "repairRange": 0, "energyDelta": 0.3, "energyRange": 0,
         "playDelta": 0, "playRange": 0, "cooldownSecs": 10},
        {"actionId": "RepairHead", "repairDelta": 0.33, "repairRange": 0, "energyDelta": 0, "energyRange": 0,
         "playDelta": 0, "playRange": 0, "cooldownSecs": 0},
        {"actionId": "Play", "repairDelta": 0, "repairRange": 0, "energyDelta": 0, "energyRange": 0,
         "playDelta": 0.5, "playRange": 0.1, "cooldownSecs": 0},
    ]}))


class TestEmotion(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.graph = DecayGraph([Node(0, 1), Node(10, 1), Node(20, 0)])
        self.emotion = EmotionType("Happy", self.graph, self.graph, clock=self.clock)

    def test_decay_follows_graph(self):
        self.emotion.add(0.5)
        self.clock.now = 15
        self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, 0.25)
        self.clock.now = 30
        self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, 0.0)

    def test_decay_is_incremental(self):
        # Decaying in steps gives the same value as decaying at once.
        self.emotion.add(0.5)
        for t in (4, 9, 12, 15):
            self.clock.now = t
            self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, 0.25)

    def test_clamped(self):
        self.emotion.add(2.0)
        self.assertEqual(self.emotion.value, 1.0)
        self.emotion.add(-5.0)
        self.assertEqual(self.emotion.value, -1.0)

    def test_weakening_does_not_restart_decay(self):
        self.emotion.add(0.8)
        self.clock.now = 15     # graph at 0.5
        self.emotion.add(-0.1)  # weakens: the age is kept
        self.assertAlmostEqual(self.emotion.value, 0.4 - 0.1)
        self.clock.now = 20
        self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, 0.0)

    def test_strengthening_restarts_decay(self):
        self.emotion.add(0.5)
        self.clock.now = 15
        self.emotion.add(0.3)   # 0.25 + 0.3, age restarts
        self.assertAlmostEqual(self.emotion.value, 0.55)
        self.clock.now = 25     # 10 s after: graph still 1
        self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, 0.55)

    def test_small_strengthening_keeps_age(self):
        self.emotion.add(0.5)
        self.clock.now = 15
        self.emotion.add(0.04)  # not above 0.05
        self.clock.now = 20
        self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, 0.0)

    def test_sign_change_restarts_decay(self):
        self.emotion.add(0.1)
        self.clock.now = 15
        self.emotion.add(-0.2)  # now negative: restarts
        self.assertAlmostEqual(self.emotion.value, 0.05 - 0.2)
        self.clock.now = 25
        self.emotion.update()
        self.assertAlmostEqual(self.emotion.value, -0.15)


class TestMood(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        flat = DecayGraph([Node(0, 1)])
        penalty = DecayGraph([Node(0, 0), Node(30, 1)])
        self.types = {n: EmotionType(n, flat, penalty, clock=self.clock) for n in ("Happy", "Confident")}
        self.events = {
            "Win": EmotionEvent("Win", {"Happy": 0.4, "Confident": 0.2, "Other": 1.0}),
            "Custom": EmotionEvent("Custom", {"Happy": 0.4}, DecayGraph([Node(0, 1)])),
        }
        self.mood = Mood(self.types, self.events, clock=self.clock)

    def test_unknown_event(self):
        self.assertFalse(self.mood.apply_event("Unknown"))

    def test_repetition_penalty(self):
        self.assertTrue(self.mood.apply_event("Win"))       # first occurrence: not penalized
        self.assertAlmostEqual(self.mood.values()["Happy"], 0.4)
        self.clock.now = 15
        self.mood.apply_event("Win")                         # half penalty
        self.assertAlmostEqual(self.mood.values()["Happy"], 0.6)
        self.mood.apply_event("Win")                         # immediate repetition: nothing
        self.assertAlmostEqual(self.mood.values()["Happy"], 0.6)

    def test_event_graph_overrides_default(self):
        self.mood.apply_event("Custom")
        self.mood.apply_event("Custom")
        self.assertAlmostEqual(self.mood.values()["Happy"], 0.8)

    def test_simple_mood(self):
        self.assertEqual(self.mood.simple_mood(), "Default")
        self.types["Happy"].set(0.5)
        self.assertEqual(self.mood.simple_mood(), "Happy")
        self.types["Confident"].set(-0.3)
        self.assertEqual(self.mood.simple_mood(), "Sad")
        self.types["Confident"].set(0.0)
        self.types["Happy"].set(-0.4)
        self.assertEqual(self.mood.simple_mood(), "Sad")

    def test_loaded_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            make_resources(directory)
            types = load_emotion_types(directory, clock=self.clock)
            events_dir = os.path.join(directory, 'cozmo_resources', 'config', 'engine', 'emotionevents')
            os.makedirs(events_dir)
            _write(events_dir, 'e.json', json.dumps({"name": "E", "emotionAffectors": [
                {"emotionType": "Winning", "value": 0.5}], "repetitionPenalty": {"nodes": [{"x": 0, "y": 1}]}}))
            events = load_emotion_events(directory)
        self.assertEqual(
            list(types), ["Happy", "Calm", "Brave", "Confident", "Charged", "Excited", "Social", "Winning",
                          "WantToPlay"])
        self.assertIsNotNone(events["E"].repetition_penalty)
        mood = Mood(types, events, clock=self.clock)
        mood.apply_event("E")
        self.assertAlmostEqual(mood.values()["Winning"], 0.5)


class TestNeeds(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        make_resources(self.directory.name)
        self.config = load_needs_config(self.directory.name)
        self.clock = Clock()

    def model(self, **kwargs):
        return NeedsModel(self.config, clock=self.clock, rng=random.Random(0), **kwargs)

    def test_load(self):
        self.assertEqual(self.config.minimum, 0.03)
        self.assertEqual(self.config.initial_levels[NeedId.PLAY], 1)
        self.assertEqual(self.config.brackets[NeedId.ENERGY][Bracket.WARNING], 0.21)
        self.assertEqual(self.config.actions["Feed"].deltas[NeedId.ENERGY], 0.3)
        self.assertEqual([r.threshold for r in self.config.connected_rates[NeedId.ENERGY]], [0.6, 0.3])
        self.assertEqual(self.config.broken_part_thresholds, [0.9, 0.6, 0.3])
        self.assertEqual(self.config.fullness_cooldown_seconds[NeedId.PLAY], 100)

    def test_decay_only_when_period_elapses(self):
        model = self.model()
        self.clock.now = 59
        self.assertEqual(model.level(NeedId.PLAY), 1)
        self.assertEqual(model.level(NeedId.ENERGY), 1)

    def test_decay_integrates_bands(self):
        # Energy: 0.1/min from 1 to 0.6 (4 min), then 0.05/min from 0.6 to 0.3 (6 min). 5 minutes: 0.6 - 0.05.
        # Repair below 0.5 so that the modifier does not double the decay.
        model = self.model()
        model.levels[NeedId.REPAIR] = 0.4
        self.clock.now = 300
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 0.55)
        self.clock.now = 600
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 0.3)

    def test_decay_stops_at_last_threshold(self):
        model = self.model()
        model.levels[NeedId.REPAIR] = 0.4
        self.clock.now = 60 * 1000
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 0.3)

    def test_no_decay_below_every_threshold(self):
        model = self.model()
        model.levels[NeedId.ENERGY] = 0.2
        self.clock.now = 600
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 0.2)

    def test_zero_rate_does_not_decay(self):
        model = self.model()
        self.clock.now = 6000
        self.assertEqual(model.level(NeedId.PLAY), 1)

    def test_modifier_multiplies_other_need(self):
        # Repair at or above 0.5 doubles the decay of energy; the 0.0 entry targets Play only.
        model = self.model()
        self.clock.now = 120
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 1 - 2 * 0.1 * 2)
        self.clock.now = 0
        model = self.model()
        model.levels[NeedId.REPAIR] = 0.4
        self.clock.now = 120
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 1 - 2 * 0.1)

    def test_unconnected_uses_its_own_rates(self):
        model = self.model(connected=False)
        self.clock.now = 60
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 0.999)
        self.assertEqual(model.level(NeedId.REPAIR), 1)     # no unconnected rate: no decay

    def test_floor(self):
        model = self.model()
        model.levels[NeedId.REPAIR] = 0.04
        self.clock.now = 60 * 100
        self.assertAlmostEqual(model.level(NeedId.REPAIR), 0.03)

    def test_brackets(self):
        model = self.model()
        self.assertEqual(model.bracket(NeedId.REPAIR), Bracket.FULL)
        model.levels[NeedId.REPAIR] = 0.6
        self.assertEqual(model.bracket(NeedId.REPAIR), Bracket.NORMAL)
        model.levels[NeedId.REPAIR] = 0.3
        self.assertEqual(model.bracket(NeedId.REPAIR), Bracket.WARNING)
        model.levels[NeedId.REPAIR] = 0.1
        self.assertEqual(model.bracket(NeedId.REPAIR), Bracket.CRITICAL)

    def test_action_unknown(self):
        self.assertFalse(self.model().apply_action("Unknown"))

    def test_feed_reaching_full_snaps_to_max_and_pauses_decay(self):
        model = self.model()
        model.levels[NeedId.ENERGY] = 0.5
        self.assertTrue(model.apply_action("Feed"))     # 0.8: not full
        self.assertAlmostEqual(model.levels[NeedId.ENERGY], 0.8)
        self.assertTrue(model.apply_action("Feed"))     # 1.1 clamped to 1
        self.assertEqual(model.levels[NeedId.ENERGY], 1)
        # Full at t=0: decay is paused for 100 s, then resumes without counting the pause.
        self.clock.now = 60
        self.assertEqual(model.level(NeedId.ENERGY), 1)
        self.clock.now = 120    # 20 s after the pause, repair 1 doubles the rate: 0.2/min
        self.assertAlmostEqual(model.level(NeedId.ENERGY), 1 - 0.2 * 20 / 60)

    def test_energy_snaps_to_full_bracket(self):
        model = self.model()
        model.levels[NeedId.ENERGY] = 0.75
        model.apply_action("Feed")                      # 1.05 -> 1
        self.assertEqual(model.levels[NeedId.ENERGY], 1)

    def test_play_range(self):
        model = self.model()
        model.levels[NeedId.PLAY] = 0.1
        model.apply_action("Play")
        self.assertTrue(0.1 + 0.4 <= model.levels[NeedId.PLAY] <= 0.1 + 0.6)

    def test_parts_damage_with_decay_and_repair(self):
        model = self.model()
        model.levels[NeedId.REPAIR] = 0.95
        self.clock.now = 60 * 10        # decays 0.1: level 0.85 -> one threshold (0.9) above it
        self.assertAlmostEqual(model.level(NeedId.REPAIR), 0.85)
        self.assertEqual(model.num_damaged_parts, 1)
        model.damaged_parts = [True, False, False]
        model.apply_action("RepairHead")                # head repaired: no damaged part: back to the maximum
        self.assertEqual(model.num_damaged_parts, 0)
        self.assertEqual(model.levels[NeedId.REPAIR], 1)

    def test_repair_level_stays_in_the_band_of_damaged_parts(self):
        model = self.model()
        model.damaged_parts = [True, True, False]       # band (0.3, 0.6)
        model.levels[NeedId.REPAIR] = 0.5
        model.apply_action("RepairHead")                # head was damaged: one part remains: band (0.6, 0.9)
        self.assertEqual(model.num_damaged_parts, 1)
        self.assertTrue(0.6 < model.levels[NeedId.REPAIR] < 0.9)
