
import os
import tempfile
import unittest
from queue import Queue
from threading import RLock
from types import SimpleNamespace
from typing import Any

import numpy as np

from pycozmo import activity, behavior, markers, robot
from pycozmo.brain import Brain
from pycozmo.emotions import DecayGraph, EmotionType, Mood, Node
from pycozmo.tests.test_mood_needs import Clock


class FakeClient:
    def __init__(self) -> None:
        self.robot_status = 0
        self.activated: list[str] = []
        self.deactivated: list[str] = []

    def activate_behavior(self, b: Any) -> None:
        self.activated.append(b.get_id())

    def deactivate_behavior(self, b: Any) -> None:
        self.deactivated.append(b.get_id())


class Implemented(behavior.Behavior):
    """ A behavior that is not the placeholder class. """


def make_behavior(cls: Any, behavior_id: str) -> behavior.Behavior:
    behavior_object: behavior.Behavior = cls(FakeClient(), {"behaviorID": behavior_id})
    return behavior_object


def make_brain(triggers: list[dict[str, Any]], behavior_ids: list[str], freeplay: bool = False) -> Brain:
    """ A brain without the resources, the threads and the robot. """
    brain = object.__new__(Brain)
    brain.cli = FakeClient()    # type: ignore[assignment]
    brain.freeplay = freeplay
    brain._lock = RLock()
    clock = Clock()
    graph = DecayGraph([Node(0.0, 0.0), Node(30.0, 1.0)])
    brain.emotion_types = {"Confident": EmotionType("Confident", graph, graph, clock=clock)}
    brain.mood = Mood(brain.emotion_types, {}, clock=clock)
    brain.needs = None      # type: ignore[assignment]
    brain.behaviors = {b: make_behavior(Implemented, b) for b in behavior_ids}
    brain.behavior = None
    brain.running_reaction = None
    brain.behavior_chooser = None
    brain.activities = {}
    brain.freeplay_activity = None
    brain.context = activity.SelectionContext(
        clock=clock, mood=brain.mood, runnable=brain.is_behavior_runnable,
        running=brain.is_behavior_running, running_reaction=lambda: brain.running_reaction)
    brain.reaction_manager = behavior.ReactionTriggerManager(
        [behavior.ReactionTrigger.from_json(t) for t in triggers], brain.context)
    brain.reaction_queue = Queue()
    brain.marker_watcher = None
    brain.visible_markers = []
    brain._object_seen = {}
    brain._cube_moved_time = None
    return brain


def marker(marker_type: markers.MarkerType) -> markers.Marker:
    return markers.Marker(marker_type, np.zeros((4, 2), dtype=np.float32), 0.0, 10)


def posted(brain: Brain) -> list[str]:
    result = []
    while not brain.reaction_queue.empty():
        result.append(brain.reaction_queue.get())
    return result


class FakeCubeLights:
    def __init__(self) -> None:
        self.played: list[tuple[str, int | None]] = []

    def play(self, name: str, object_id: int | None = None) -> int:
        self.played.append((name, object_id))
        return 1


FRUSTRATION: list[dict[str, Any]] = [
    dict(reactionTrigger="Frustration", behaviorID="Minor",
         frustrationParams=dict(maxConfidence=-0.6, cooldownTime_s=60.0)),
    dict(reactionTrigger="Frustration", behaviorID="Major",
         frustrationParams=dict(maxConfidence=-0.9, cooldownTime_s=60.0)),
]


class TestBrain(unittest.TestCase):

    def test_frustration_reaction_follows_the_confidence(self):
        brain = make_brain(FRUSTRATION, ["Minor", "Major"])
        self.assertIsNone(brain.check_reactions())
        brain.emotion_types["Confident"].set(-0.7)
        self.assertEqual(brain.check_reactions(), "Minor")
        self.assertEqual(brain.running_reaction, "Frustration")
        brain.emotion_types["Confident"].set(-0.95)
        self.assertIsNone(brain.check_reactions())          # no new frustration while one runs
        brain.on_behavior_done(brain.cli)
        self.assertEqual(brain.check_reactions(), "Major")
        self.assertEqual(brain.cli.activated, ["Minor", "Major"])        # type: ignore[attr-defined]

    def test_reaction_with_an_unimplemented_behavior_still_fires(self):
        brain = make_brain(FRUSTRATION, [])
        brain.behaviors = {b: make_behavior(behavior.Behavior, b) for b in ("Minor", "Major")}
        brain.emotion_types["Confident"].set(-0.7)
        self.assertEqual(brain.check_reactions(), "Minor")

    def test_reaction_to_a_posted_trigger(self):
        triggers = [dict(reactionTrigger="RobotPickedUp", behaviorID="Pickup"),
                    dict(reactionTrigger="CliffDetected", behaviorID="Cliff")]
        brain = make_brain(triggers, ["Pickup", "Cliff"])
        brain.process_reaction("RobotPickedUp")
        self.assertEqual(brain.behavior.get_id(), "Pickup")      # type: ignore[union-attr]
        self.assertEqual(brain.running_reaction, "RobotPickedUp")
        # A reaction does not interrupt another one.
        brain.process_reaction("CliffDetected")
        self.assertEqual(brain.behavior.get_id(), "Pickup")      # type: ignore[union-attr]
        # Once the reaction is done, the trigger that was latched is used.
        brain.on_behavior_done(brain.cli)
        self.assertIsNone(brain.behavior)
        self.assertIsNone(brain.running_reaction)
        self.assertEqual(brain.check_reactions(), "Cliff")

    def test_runnable(self):
        brain = make_brain([], ["Real"])
        brain.behaviors["Placeholder"] = make_behavior(behavior.Behavior, "Placeholder")
        brain.behaviors["DriveOffCharger"] = make_behavior(Implemented, "DriveOffCharger")
        self.assertTrue(brain.is_behavior_runnable("Real"))
        self.assertTrue(brain.is_behavior_runnable("Placeholder"))      # a reaction runs anyway
        self.assertTrue(brain.is_behavior_implemented("Real"))
        self.assertFalse(brain.is_behavior_implemented("Placeholder"))
        self.assertFalse(brain.is_behavior_implemented("Unknown"))
        self.assertFalse(brain.is_behavior_runnable("Unknown"))
        self.assertFalse(brain.is_behavior_runnable("DriveOffCharger"))
        brain.cli.robot_status = robot.RobotStatusFlag.IS_ON_CHARGER
        self.assertTrue(brain.is_behavior_runnable("DriveOffCharger"))

    def test_freeplay_choice_and_chooser_notifications(self):
        brain = make_brain([], ["A", "B"], freeplay=True)
        chooser = activity.BehaviorChooser("StrictPriority", ["A", "B"])
        sub = SimpleNamespace(get_chooser=lambda: chooser)
        brain.activities = {"Sub": sub}      # type: ignore[dict-item]
        brain.freeplay_activity = SimpleNamespace(      # type: ignore[assignment]
            current="Sub", choose=lambda context, activities: chooser.choose(context))
        self.assertEqual(brain.choose_behavior(), "A")
        self.assertEqual(brain.running_reaction, None)
        self.assertTrue(brain.is_behavior_running("A"))
        # Nothing is chosen while a behavior runs.
        self.assertIsNone(brain.choose_behavior())
        brain.on_behavior_done(brain.cli)
        self.assertIsNone(brain.behavior)
        self.assertFalse(chooser._states["A"].running)
        self.assertIsNotNone(chooser._states["A"].last_stop)

    def test_freeplay_skips_unimplemented_behaviors(self):
        brain = make_brain([], ["A", "B"], freeplay=True)
        brain.behaviors["A"] = make_behavior(behavior.Behavior, "A")
        chooser = activity.BehaviorChooser("StrictPriority", ["A", "B"])
        brain.activities = {"Sub": SimpleNamespace(get_chooser=lambda: chooser)}      # type: ignore[dict-item]
        brain.freeplay_activity = SimpleNamespace(      # type: ignore[assignment]
            current="Sub", choose=lambda context, activities: chooser.choose(context))
        self.assertEqual(brain.choose_behavior(), "B")

    def test_a_reaction_interrupts_a_freeplay_behavior(self):
        triggers = [dict(reactionTrigger="RobotPickedUp", behaviorID="Pickup")]
        brain = make_brain(triggers, ["A", "Pickup"], freeplay=True)
        brain.activate_behavior("A")
        brain.process_reaction("RobotPickedUp")
        self.assertEqual(brain.behavior.get_id(), "Pickup")      # type: ignore[union-attr]
        self.assertEqual(brain.cli.deactivated, ["A"])           # type: ignore[attr-defined]


class TestCubesAndMarkers(unittest.TestCase):

    def test_a_cube_seen_posts_object_position_updated_once(self):
        brain = make_brain([], [])
        brain.on_markers([marker(markers.MarkerType.LIGHTCUBEI_FRONT)])
        self.assertEqual(posted(brain), ["ObjectPositionUpdated"])
        self.assertEqual(len(brain.visible_markers), 1)
        brain.on_markers([marker(markers.MarkerType.LIGHTCUBEI_TOP)])          # same cube, other face
        self.assertEqual(posted(brain), [])
        brain.on_markers([marker(markers.MarkerType.LIGHTCUBEJ_FRONT)])        # another cube
        self.assertEqual(posted(brain), ["ObjectPositionUpdated"])

    def test_a_cube_seen_again_after_the_timeout(self):
        brain = make_brain([], [])
        brain.on_markers([marker(markers.MarkerType.LIGHTCUBEK_BACK)])
        posted(brain)
        brain._object_seen["LIGHTCUBE3"] -= 10.0
        brain.on_markers([marker(markers.MarkerType.LIGHTCUBEK_BACK)])
        self.assertEqual(posted(brain), ["ObjectPositionUpdated"])

    def test_charger_counts_other_markers_do_not(self):
        brain = make_brain([], [])
        brain.on_markers([marker(markers.MarkerType.ARROW), marker(markers.MarkerType.STAR5)])
        self.assertEqual(posted(brain), [])
        brain.on_markers([marker(markers.MarkerType.CHARGER)])
        self.assertEqual(posted(brain), ["ObjectPositionUpdated"])
        brain.on_markers([])
        self.assertEqual(brain.visible_markers, [])

    def test_a_moving_cube_posts_cube_moved_at_a_limited_rate(self):
        brain = make_brain([], [])
        brain.on_object_moved(brain.cli, None)      # type: ignore[arg-type]
        brain.on_object_moved(brain.cli, None)      # type: ignore[arg-type]
        self.assertEqual(posted(brain), ["CubeMoved"])
        brain._cube_moved_time -= 3.0               # type: ignore[operator]
        brain.on_object_moved(brain.cli, None)      # type: ignore[arg-type]
        self.assertEqual(posted(brain), ["CubeMoved"])

    def test_play_cube_animation(self):
        brain = make_brain([], [])
        brain.cube_lights = FakeCubeLights()        # type: ignore[assignment]
        self.assertEqual(brain.play_cube_animation("Flash", 2), 1)
        self.assertEqual(brain.cube_lights.played, [("Flash", 2)])       # type: ignore[attr-defined]

    def test_camera_images_go_to_the_watcher(self):
        brain = make_brain([], [])
        brain.on_camera_image(brain.cli, "image")   # type: ignore[arg-type]
        submitted: list[object] = []
        brain.marker_watcher = SimpleNamespace(submit=submitted.append)       # type: ignore[assignment]
        brain.on_camera_image(brain.cli, "image")   # type: ignore[arg-type]
        self.assertEqual(submitted, ["image"])

    def test_missing_library_disables_the_markers(self):
        brain = make_brain([], [])
        handlers: list[object] = []
        brain.cli.add_handler = lambda cls, handler: handlers.append(cls)     # type: ignore
        with tempfile.TemporaryDirectory() as root:
            brain._init_markers(root)
        self.assertIsNone(brain.marker_watcher)
        self.assertEqual(handlers, [])

    def test_library_enables_the_markers(self):
        from pycozmo.tests.test_markers import make_library
        brain = make_brain([], [])
        handlers: list[object] = []
        brain.cli.add_handler = lambda cls, handler: handlers.append(cls)     # type: ignore
        with tempfile.TemporaryDirectory() as root:
            filename = os.path.join(root, markers.LIBRARY_FILENAME)
            os.makedirs(os.path.dirname(filename))
            make_library().save(filename)
            brain._init_markers(root)
        self.assertIsNotNone(brain.marker_watcher)
        self.assertEqual(len(handlers), 1)
