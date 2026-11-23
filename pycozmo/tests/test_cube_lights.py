
import json
import os
import tempfile
import unittest
from typing import Any

from pycozmo.cube_lights import (
    CubeLightManager, CubeLightPlayer, LightAnimation, LightPattern, load_cube_animation_triggers,
    load_cube_light_animations,
    pattern_to_light_states)
from pycozmo import lights
from pycozmo.protocol_encoder import ObjectType
from types import SimpleNamespace
from pycozmo.lights import encode_color, ms_to_frames
from pycozmo.tests.test_mood_needs import Clock


def pattern_json(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = dict(
        onColors=[[255, 0, 0, 255]] * 4, offColors=[[0, 0, 0, 0]] * 4, onPeriod_ms=[100] * 4,
        offPeriod_ms=[200] * 4, transitionOnPeriod_ms=[0] * 4, transitionOffPeriod_ms=[0] * 4, offset=[0] * 4,
        rotationPeriod_ms=0)
    data.update(changes)
    return data


def entry(duration: int = 0, overridable: bool | None = None, **changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = dict(pattern=pattern_json(**changes), duration_ms=duration)
    if overridable is not None:
        result["canBeOverridden"] = overridable
    return result


def pattern(duration: int = 0, overridable: bool = True, **changes: Any) -> LightPattern:
    return LightPattern.from_json(entry(duration, overridable, **changes))


class TestPatternToStates(unittest.TestCase):

    def test_pattern(self):
        states, rotation = pattern_to_light_states(pattern(rotationPeriod_ms=100, offset=[0, 30, 31, 0]))
        self.assertEqual(rotation, 4)
        self.assertEqual(states[0].on_frames, 4)
        self.assertEqual(states[0].off_frames, 7)
        self.assertEqual(states[0].on_color, encode_color((255, 0, 0, 255)))
        self.assertEqual(states[0].off_color, 0)
        self.assertEqual([s.offset for s in states], [0, 1, 2, 0])

    def test_solid_leds(self):
        states, _ = pattern_to_light_states(pattern(
            onColors=[[255, 0, 0, 255], [255, 0, 0, 255], [255, 0, 0, 255], [255, 0, 0, 255]],
            offColors=[[0, 0, 255, 255], [0, 0, 255, 255], [0, 0, 255, 255], [0, 0, 255, 255]],
            onPeriod_ms=[0, 0, 100, 100], offPeriod_ms=[0, 50, 0, 50]))
        solid_frames = ms_to_frames(lights.SOLID_PERIOD)
        red, blue = encode_color((255, 0, 0, 255)), encode_color((0, 0, 255, 255))
        # Both periods 0: black. Only the on period 0: the off color. Only the off period 0: the on color.
        self.assertEqual((states[0].on_color, states[0].off_color), (0, 0))
        self.assertEqual((states[0].on_frames, states[0].off_frames), (solid_frames, solid_frames))
        self.assertEqual((states[1].on_color, states[1].off_color), (blue, blue))
        self.assertEqual((states[1].on_frames, states[1].off_frames), (solid_frames, 2))
        self.assertEqual((states[2].on_color, states[2].off_color), (red, red))
        self.assertEqual((states[2].on_frames, states[2].off_frames), (4, solid_frames))
        self.assertEqual((states[3].on_color, states[3].off_color), (red, blue))


class TestParsing(unittest.TestCase):

    def test_pattern_fields(self):
        parsed = pattern(duration=600, overridable=False, rotationPeriod_ms=70)
        self.assertEqual((parsed.duration_ms, parsed.can_be_overridden, parsed.rotation_period_ms), (600, False, 70))
        self.assertTrue(pattern().can_be_overridden)         # default
        self.assertEqual(parsed.on_colors[0], (255, 0, 0, 255))

    def test_missing_fields(self):
        bad = entry()
        del bad["pattern"]["offset"]
        with self.assertRaises(ValueError):
            LightPattern.from_json(bad)
        with self.assertRaises(ValueError):
            LightPattern.from_json({"pattern": pattern_json()})
        with self.assertRaises(ValueError):
            LightPattern.from_json(entry(onPeriod_ms=[1, 2, 3]))

    def test_load(self):
        with tempfile.TemporaryDirectory() as root:
            lights_dir = os.path.join(root, "cozmo_resources", "config", "engine", "lights", "cubeLights", "sub")
            os.makedirs(lights_dir)
            maps_dir = os.path.join(root, "cozmo_resources", "assets", "cubeAnimationGroupMaps")
            os.makedirs(maps_dir)
            with open(os.path.join(lights_dir, "good.json"), "w") as f:
                f.write("// comment\n" + json.dumps({"good": [entry(100), entry(0)]}))
            with open(os.path.join(lights_dir, "two.json"), "w") as f:
                f.write(json.dumps({"b": [entry()], "a": [entry(5)]}))
            with open(os.path.join(lights_dir, "bad.json"), "w") as f:
                bad = entry()
                del bad["pattern"]["onColors"]
                f.write(json.dumps({"bad": [bad]}))
            with open(os.path.join(maps_dir, "CubeAnimationTriggerMap.json"), "w") as f:
                f.write(json.dumps({"Pairs": [{"CladEvent": "Connected", "AnimName": "good"}]}))
            animations = load_cube_light_animations(root)
            self.assertEqual(sorted(animations), ["a", "good"])
            self.assertEqual([p.duration_ms for p in animations["good"].patterns], [100, 0])
            self.assertEqual(animations["a"].patterns[0].duration_ms, 5)
            self.assertEqual(animations["good"].patterns[1].name, "good1")
            self.assertEqual(load_cube_animation_triggers(root), {"Connected": "good"})


class TestPlayer(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.applied: list[Any] = []
        self.player = CubeLightPlayer(lambda states, rotation: self.applied.append((states[0].on_color, rotation)),
                                      clock=self.clock)
        self.done: list[str] = []

    def animation(self, name: str, *entries: tuple[int, int, bool]) -> LightAnimation:
        """ Patterns identified by the red value of their first LED: (duration, red, can be overridden). """
        patterns = [pattern(d, o, onColors=[[red, 0, 0, 0]] * 4) for d, red, o in entries]
        return LightAnimation(name, patterns)

    def reds(self):
        return [color >> 10 << 3 for color, _ in self.applied]

    def test_sequence_and_end(self):
        self.assertTrue(self.player.play(self.animation("a", (1000, 8, True), (500, 16, True)),
                                         on_finished=lambda: self.done.append("a")))
        self.assertEqual(self.reds(), [8])
        self.clock.now = 0.5
        self.player.update()
        self.assertEqual(self.reds(), [8])
        self.clock.now = 1.2
        self.player.update()                                  # second pattern, from the time of the update
        self.assertEqual(self.reds(), [8, 16])
        self.clock.now = 1.6
        self.player.update()
        self.assertEqual(self.reds(), [8, 16])
        self.clock.now = 1.7
        self.player.update()
        self.assertEqual(self.done, ["a"])
        self.assertIsNone(self.player.current)

    def test_zero_duration_stays(self):
        self.player.play(self.animation("a", (0, 8, True)))
        self.clock.now = 1000
        self.player.update()
        self.assertEqual(self.player.current.name, "a")      # type: ignore[union-attr]
        self.assertEqual(self.reds(), [8])

    def test_previous_animation_resumes(self):
        self.player.play(self.animation("base", (0, 8, True)))
        self.player.play(self.animation("flash", (600, 24, True)), on_finished=lambda: self.done.append("flash"))
        self.assertEqual(self.reds(), [8, 24])
        self.clock.now = 0.7
        self.player.update()
        self.assertEqual(self.reds(), [8, 24, 8])
        self.assertEqual(self.player.current.name, "base")   # type: ignore[union-attr]
        self.assertEqual(self.done, ["flash"])

    def test_indefinite_animation_replaces_the_stack(self):
        self.player.play(self.animation("base", (0, 8, True)))
        self.player.play(self.animation("flash", (600, 24, True)))
        self.player.play(self.animation("solid", (0, 16, True)))
        self.player.stop()
        self.assertIsNone(self.player.current)
        self.assertEqual(self.reds(), [8, 24, 16])

    def test_cannot_be_overridden(self):
        self.player.play(self.animation("locked", (0, 8, False)))
        self.assertFalse(self.player.play(self.animation("other", (0, 16, True))))
        self.assertEqual(self.reds(), [8])
        self.assertEqual(self.player.current.name, "locked")   # type: ignore[union-attr]

    def test_stop(self):
        self.player.play(self.animation("base", (0, 8, True)))
        self.player.play(self.animation("flash", (600, 24, True)))
        self.player.stop()
        self.assertEqual(self.reds(), [8, 24, 8])
        self.player.stop_all()
        self.assertIsNone(self.player.current)
        self.player.update()
        self.assertFalse(self.player.play(LightAnimation("empty")))

    def test_for_cube(self):
        calls: list[Any] = []

        class FakeClient:
            def set_cube_lights(self, object_id, states, *, rotation_period_frames=0):
                calls.append((object_id, len(states), rotation_period_frames))

        player = CubeLightPlayer.for_cube(FakeClient(), 3, clock=self.clock)
        player.play(LightAnimation("a", [pattern(0, rotationPeriod_ms=100)]))
        self.assertEqual(calls, [(3, 4, 4)])


class FakeCube:
    def __init__(self) -> None:
        self.handlers: list[Any] = []
        self.calls: list[tuple[int, int, int]] = []     # object id, on color of the first LED, rotation

    def add_handler(self, cls: Any, handler: Any) -> None:
        self.handlers.append((cls, handler))

    def set_cube_lights(self, object_id, states, *, rotation_period_frames=0):
        self.calls.append((object_id, states[0].on_color, rotation_period_frames))


def connection(object_id: int, connected: bool = True, object_type: Any = ObjectType.Block_LIGHTCUBE1) -> Any:
    return SimpleNamespace(object_id=object_id, connected=connected, object_type=object_type, factory_id=1)


class TestManager(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.cli = FakeCube()
        red = LightAnimation("connected", [pattern(0, onColors=[[255, 0, 0, 255]] * 4)])
        blue = LightAnimation("flash", [pattern(500, onColors=[[0, 0, 255, 255]] * 4)])
        self.manager = CubeLightManager(
            self.cli, {"connected": red, "flash": blue}, {"Connected": "connected", "Flash": "flash"}, clock=self.clock)

    def test_attach(self):
        self.manager.attach()
        self.assertEqual(len(self.cli.handlers), 1)

    def test_connection_plays_the_connected_animation(self):
        self.manager.on_connection(self.cli, connection(2))
        self.assertEqual(self.manager.object_ids, [2])
        self.assertEqual([c[0] for c in self.cli.calls], [2])
        self.manager.on_connection(self.cli, connection(2, connected=False))
        self.assertEqual(self.manager.object_ids, [])

    def test_only_cubes_get_a_player(self):
        self.manager.on_connection(self.cli, connection(3, object_type=ObjectType.Charger_Basic))
        self.assertEqual(self.manager.object_ids, [])
        self.assertEqual(self.cli.calls, [])

    def test_play_by_trigger_name_and_animation_name(self):
        self.manager.on_connection(self.cli, connection(1))
        self.manager.on_connection(self.cli, connection(2, object_type=ObjectType.Block_LIGHTCUBE2))
        self.cli.calls.clear()
        self.assertEqual(self.manager.play("Flash"), 2)                 # all the cubes
        self.assertEqual(sorted(c[0] for c in self.cli.calls), [1, 2])
        self.cli.calls.clear()
        self.assertEqual(self.manager.play("flash", 2), 1)              # one cube, by the name of the animation
        self.assertEqual([c[0] for c in self.cli.calls], [2])
        self.assertEqual(self.manager.play("Flash", 7), 0)              # not connected
        self.assertEqual(self.manager.play("Unknown"), 0)

    def test_update_ends_the_animation_and_resumes(self):
        self.manager.on_connection(self.cli, connection(1))
        self.manager.play("Flash")
        self.clock.now = 0.6
        self.manager.update()
        # Back to the connected animation: the red of the first play, not the blue.
        self.assertEqual(self.cli.calls[-1][1], self.cli.calls[0][1])
        self.assertNotEqual(self.cli.calls[1][1], self.cli.calls[0][1])

    def test_stop_turns_the_lights_off(self):
        self.manager.on_connection(self.cli, connection(1))
        self.manager.stop()
        self.assertEqual(self.cli.calls[-1][1], lights.off_light.on_color)
        self.manager.update()
        self.assertEqual(self.manager.play("Flash"), 1)                 # the cube still has its player

    def test_errors_are_logged_not_raised(self):
        self.manager.on_connection(self.cli, connection(1))

        def broken(*args, **kwargs):
            raise OSError("not connected")

        self.cli.set_cube_lights = broken      # type: ignore[method-assign]
        self.assertEqual(self.manager.play("Flash"), 0)
        self.manager.update()
        self.manager.stop()
