
import importlib.util
import io
import math
import unittest
from types import SimpleNamespace
from typing import Any

from PIL import Image

from pycozmo import anim_encoder, camera, event, protocol_encoder, robot
from pycozmo.mcp_server import ANIMATION_TIMEOUT, TURN_COAST_DEG, Limits, RobotController, ToolError, build_server


class Clock:
    """ A clock that the fake sleep advances. """

    def __init__(self) -> None:
        self.now = 0.0
        self.on_sleep: Any = None       # what the fake robot does while the controller sleeps

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(seconds)


class FakeClient:
    def __init__(self) -> None:
        self.robot_status = 0
        self.robot_picked_up = False
        self.battery_voltage = 4.0
        self.battery_percent = 80
        self.head_angle = SimpleNamespace(radians=0.0)
        self.pose = SimpleNamespace(position=SimpleNamespace(x=1.0, y=2.0), origin_id=1,
                                    rotation=SimpleNamespace(angle_z=SimpleNamespace(radians=math.pi / 2)))
        self.available_objects: dict[int, Any] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.handlers: list[tuple[Any, Any]] = []
        self.on_step: Any = None        # called by the fake wheels, to change the state while the robot moves
        self.animation_groups = {"B": object(), "A": object()}
        self.anim_ends = True           # the fake robot reports the end of an animation at once
        self.anim_clip: str | None = "a_01"
        self.turning = 0.0              # radians per second
        self.camera_since: float | None = None
        self.clock: Any = None

    def advance(self, seconds: float) -> None:
        angle = self.pose.rotation.angle_z
        angle.radians = math.remainder(angle.radians + self.turning * seconds, math.tau)

    def turn_in_place(self, speed: Any) -> None:
        self.calls.append(("turn", speed.degrees))
        self.turning = speed.radians

    def camera_settle_remaining(self) -> float | None:
        if self.camera_since is None:
            return None
        return float(max(0.0, self.camera_since + camera.SETTLE_TIME - self.clock.now))

    def add_handler(self, cls: Any, handler: Any) -> Any:
        self.handlers.append((cls, handler))
        if cls is event.EvtNewRawCameraImage:       # the camera streams: an image arrives
            shade = 200 if self.camera_settle_remaining() == 0.0 else 0
            handler(self, Image.new("RGB", (8, 6), (shade, shade, shade)))
        return handler

    def fire_state(self) -> None:
        """ A robot state: the handlers of the state event run, as the client does at each state. """
        for cls, handler in list(self.handlers):
            if cls is event.EvtRobotStateUpdated:
                handler(self)

    def user_handlers(self) -> list[Any]:
        """ The handlers other than the one of the state, which the controller keeps while it is connected. """
        return [h for h in self.handlers if h[0] is not event.EvtRobotStateUpdated]

    def del_handler(self, cls: Any, handler: Any) -> None:
        self.handlers.remove((cls, handler))

    def set_head_angle(self, angle: float) -> None:
        self.calls.append(("head", angle))

    def set_lift_height(self, height: float) -> None:
        self.calls.append(("lift", height))

    def drive_wheels(self, left: float, right: float) -> None:
        self.calls.append(("drive", left, right))

    def stop_all_motors(self) -> None:
        self.calls.append(("stop",))
        self.turning = 0.0

    def set_all_backpack_lights(self, light: Any) -> None:
        self.calls.append(("backpack", light))

    def set_cube_lights(self, object_id: int, states: Any) -> None:
        self.calls.append(("cube", object_id, len(states)))

    def set_object_connection(self, factory_id: int) -> None:
        self.calls.append(("connect_cube", factory_id))
        for cls, handler in list(self.handlers):
            if cls is protocol_encoder.ObjectConnectionState:
                handler(self, SimpleNamespace(factory_id=factory_id, connected=True, object_id=3))

    def enable_camera(self, enable: bool = True) -> None:
        self.calls.append(("camera", enable))
        self.camera_since = self.clock.now if enable else None

    def load_anims(self) -> None:
        pass

    def play_anim_group(self, name: str, *, allowed_types: Any = None) -> str | None:
        self.calls.append(("anim", name, allowed_types))
        if self.anim_clip is not None and self.anim_ends:
            for cls, handler in list(self.handlers):
                if cls is event.EvtAnimationCompleted:
                    handler(self)
        return self.anim_clip

    def cancel_anim(self) -> None:
        self.calls.append(("cancel_anim",))

    def say_text(self, text: str) -> None:
        self.calls.append(("say", text))

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def stop(self) -> None:
        self.calls.append(("close",))


def make(allow_drive: bool = True, limits: Limits | None = None,
         edge_guard: bool = False) -> tuple[RobotController, FakeClient, Clock]:
    """ A controller on a fake robot; the edge guard is off except in TestEdgeGuard (a fresh memory cannot back up). """
    clock = Clock()
    fake = FakeClient()
    fake.clock = clock
    clock.on_sleep = fake.advance
    controller = RobotController(connect_fn=lambda: fake, limits=limits, allow_drive=allow_drive,
                                 sleep=clock.sleep, clock=clock, edge_guard=edge_guard)
    return controller, fake, clock


class TestConnection(unittest.TestCase):

    def test_tools_need_a_connection(self):
        controller, _, _ = make()
        for call in (controller.status, controller.stop, lambda: controller.move_head(0),
                     lambda: controller.drive(10)):
            with self.assertRaises(ToolError):
                call()

    def test_connect_returns_the_status(self):
        controller, fake, _ = make()
        status = controller.connect()
        self.assertTrue(status["connected"])
        self.assertEqual(status["battery_percent"], 80)
        self.assertEqual(status["pose_mm_deg"], [1.0, 2.0, 90.0])
        self.assertFalse(status["picked_up"])
        self.assertTrue(status["drive_allowed"])
        self.assertIs(controller.client, fake)

    def test_failed_connection(self):
        def fail() -> Any:
            raise OSError("no robot")

        controller = RobotController(connect_fn=fail)
        with self.assertRaises(ToolError):
            controller.connect()

    def test_disconnect_stops_first(self):
        controller, fake, _ = make()
        controller.connect()
        self.assertEqual(controller.disconnect(), {"connected": False})
        self.assertEqual(fake.calls, [("stop",), ("disconnect",), ("close",)])
        with self.assertRaises(ToolError):
            controller.status()

    def test_status_flags(self):
        controller, fake, _ = make()
        controller.connect()
        fake.robot_status = robot.RobotStatusFlag.IS_ON_CHARGER | robot.RobotStatusFlag.CLIFF_DETECTED
        status = controller.status()
        self.assertTrue(status["on_charger"])
        self.assertTrue(status["cliff_detected"])


class TestMotion(unittest.TestCase):

    def setUp(self):
        self.controller, self.fake, self.clock = make()
        self.controller.connect()

    def test_head_and_lift_are_limited(self):
        self.assertEqual(self.controller.move_head(90)["head_angle_deg"], robot.MAX_HEAD_ANGLE.degrees)
        self.assertEqual(self.controller.move_head(-90)["head_angle_deg"], robot.MIN_HEAD_ANGLE.degrees)
        self.controller.move_lift(5)
        self.controller.move_lift(-1)
        self.assertEqual(self.fake.calls[2][1], robot.MAX_LIFT_HEIGHT.mm)
        self.assertEqual(self.fake.calls[3][1], robot.MIN_LIFT_HEIGHT.mm)

    def test_bad_numbers_are_refused(self):
        for value in ("a", None, float("nan"), float("inf")):
            with self.assertRaises(ToolError):
                self.controller.move_head(value)             # type: ignore[arg-type]
            with self.assertRaises(ToolError):
                self.controller.drive(value)                 # type: ignore[arg-type]

    def test_drive_is_bounded_and_stops(self):
        result = self.controller.drive(1000, 500)
        limits = Limits()
        self.assertEqual(result["distance_mm"], limits.max_drive_mm)
        self.assertEqual(result["speed_mmps"], limits.max_speed_mmps)
        self.assertEqual(self.fake.calls[0], ("drive", limits.max_speed_mmps, limits.max_speed_mmps))
        self.assertEqual(self.fake.calls[-1], ("stop",))
        self.assertAlmostEqual(result["seconds"], limits.max_drive_mm / limits.max_speed_mmps, places=2)
        self.assertIsNone(result["stopped_by"])

    def test_drive_backward(self):
        self.controller.drive(-50, 25)
        self.assertEqual(self.fake.calls[0], ("drive", -25.0, -25.0))

    def test_speed_zero_is_refused(self):
        with self.assertRaises(ToolError):
            self.controller.drive(50, 0)
        self.assertEqual(self.fake.calls, [])

    def test_turn_stops_at_the_heading_asked(self):
        for angle in (90.0, -90.0, 30.0, -180.0):
            self.fake.calls.clear()
            result = self.controller.turn(angle)
            self.assertEqual(result["angle_deg"], angle)
            self.assertIsNone(result["stopped_by"])
            # The fake robot stops at once: it ends short by the coast of a real robot.
            self.assertAlmostEqual(result["turned_deg"], angle - math.copysign(TURN_COAST_DEG, angle), delta=1.5)
            self.assertEqual(self.fake.calls, [("turn", math.copysign(Limits().turn_speed_dps, angle)), ("stop",)])

    def test_small_and_null_turns(self):
        self.assertAlmostEqual(self.controller.turn(4)["turned_deg"], 2.0, delta=1.5)
        self.fake.calls.clear()
        self.assertEqual(self.controller.turn(0)["turned_deg"], 0.0)
        self.assertEqual(self.fake.calls, [])

    def test_turn_that_does_not_reach_the_heading_is_stopped_by_time(self):
        self.clock.on_sleep = None                  # blocked treads: the heading does not change
        result = self.controller.turn(90)
        self.assertEqual(result["stopped_by"], "time")
        self.assertEqual(result["turned_deg"], 0.0)
        self.assertEqual(self.fake.calls[-1], ("stop",))
        self.assertLessEqual(result["seconds"], Limits().max_seconds + 0.1)

    def test_picked_up_robot_does_not_drive(self):
        self.fake.robot_picked_up = True
        with self.assertRaises(ToolError):
            self.controller.drive(50)
        with self.assertRaises(ToolError):
            self.controller.turn(30)
        self.assertEqual(self.fake.calls, [])

    def test_cliff_stops_the_robot_that_does_not_start(self):
        self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        with self.assertRaises(ToolError):
            self.controller.drive(50)
        self.assertEqual(self.fake.calls, [])

    def test_cliff_during_a_drive_stops_it(self):
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            if self.clock.now > 0.5:
                self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED

        self.controller._sleep = sleep
        result = self.controller.drive(200, 40)
        self.assertEqual(result["stopped_by"], "cliff")
        self.assertEqual(self.fake.calls[-1], ("stop",))
        self.assertLess(self.clock.now, 1.0)

    def test_pick_up_during_a_turn_stops_it(self):
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            if self.clock.now > 0.3:
                self.fake.robot_picked_up = True

        self.controller._sleep = sleep
        self.assertEqual(self.controller.turn(180)["stopped_by"], "picked up")

    def test_stop_is_sent_even_if_the_wheels_fail(self):
        def broken(*args: Any) -> None:
            raise OSError("lost")

        self.controller._sleep = broken
        with self.assertRaises(OSError):
            self.controller.drive(50)
        self.assertEqual(self.fake.calls[-1], ("stop",))

    def test_time_is_capped(self):
        limits = Limits(max_seconds=1.0, max_drive_mm=1000.0)
        controller, fake, clock = make(limits=limits)
        controller.connect()
        result = controller.drive(-1000, 10)
        self.assertEqual(result["seconds"], 1.0)
        self.assertLessEqual(clock.now, 1.1)
        # The answer gives the distance that the wheels cover in that time, not the one asked.
        self.assertEqual(result["distance_mm"], -10.0)
        self.assertEqual(result["limited_by"], "time")

    def test_turn_time_is_capped(self):
        controller, _, _ = make(limits=Limits(max_seconds=1.0))
        controller.connect()
        result = controller.turn(-90)
        self.assertEqual(result["angle_deg"], -Limits().turn_speed_dps)
        self.assertEqual(result["limited_by"], "time")
        self.assertNotIn("limited_by", controller.turn(30))

    def test_stop_during_a_drive_ends_it(self):
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            if self.clock.now > 0.5:
                self.controller.stop()      # as the SDK does from another thread

        self.controller._sleep = sleep
        result = self.controller.drive(200, 40)
        self.assertEqual(result["stopped_by"], "stop")
        self.assertLess(self.clock.now, 1.0)
        self.assertEqual(self.fake.calls[-1], ("stop",))

    def test_a_stop_before_a_drive_does_not_cancel_it(self):
        self.controller.stop()
        self.assertIsNone(self.controller.drive(20)["stopped_by"])

    def test_no_drive_mode(self):
        controller, fake, _ = make(allow_drive=False)
        controller.connect()
        with self.assertRaises(ToolError):
            controller.drive(10)
        with self.assertRaises(ToolError):
            controller.turn(10)
        controller.move_head(10)                           # the head still works
        self.assertEqual([c[0] for c in fake.calls], ["head"])

    def test_stop(self):
        self.assertEqual(self.controller.stop(), {"stopped": True})
        self.assertEqual(self.fake.calls, [("stop",), ("cancel_anim",)])


class TestExpression(unittest.TestCase):

    def setUp(self):
        self.controller, self.fake, self.clock = make()
        self.controller.connect()

    def test_backpack_colors(self):
        self.controller.set_backpack_color("Red")
        self.assertEqual(self.fake.calls[0][0], "backpack")
        with self.assertRaises(ToolError):
            self.controller.set_backpack_color("purple")

    def test_say(self):
        self.assertEqual(self.controller.say(" hello ")["said"], "hello")
        for text in ("", "   ", "x" * 301):
            with self.assertRaises(ToolError):
                self.controller.say(text)

    def test_list_animations_sorted(self):
        self.assertEqual(self.controller.list_animations(), ["A", "B"])

    def test_animation_never_drives_the_wheels(self):
        result = self.controller.play_animation("A")
        self.assertEqual(result, {"animation": "A", "clip": "a_01", "wheels": False, "finished": True,
                                  "stopped_by": None})
        _, name, allowed = self.fake.calls[0]
        self.assertEqual(name, "A")
        self.assertNotIn(anim_encoder.AnimBodyMotion, allowed)
        self.assertNotIn(anim_encoder.AnimTurnToRecordedHeading, allowed)
        self.assertIn(anim_encoder.AnimHeadAngle, allowed)
        self.assertEqual(self.fake.user_handlers(), [])

    def test_unknown_animation_is_refused_at_once(self):
        with self.assertRaises(ToolError):
            self.controller.play_animation("nope")
        self.assertEqual(self.clock.now, 0.0)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.user_handlers(), [])

    def test_animation_that_cannot_play_now_is_refused(self):
        self.fake.anim_clip = None
        with self.assertRaises(ToolError):
            self.controller.play_animation("A")
        self.assertEqual(self.clock.now, 0.0)

    def test_animation_that_does_not_end_is_cancelled(self):
        self.fake.anim_ends = False
        result = self.controller.play_animation("A")
        self.assertFalse(result["finished"])
        self.assertGreaterEqual(self.clock.now, ANIMATION_TIMEOUT)
        self.assertEqual(self.fake.calls[-1], ("cancel_anim",))

    def test_stop_during_an_animation_ends_it(self):
        self.fake.anim_ends = False
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            if self.clock.now > 1.0:
                self.controller.stop()

        self.controller._sleep = sleep
        result = self.controller.play_animation("B")
        self.assertEqual(result["stopped_by"], "stop")
        self.assertFalse(result["finished"])
        self.assertLess(self.clock.now, 2.0)

    def test_capture_image_is_a_jpeg_and_the_handler_is_removed(self):
        data = self.controller.capture_image()
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertEqual(Image.open(io.BytesIO(data)).size, (8, 6))
        self.assertEqual(self.fake.user_handlers(), [])

    def test_capture_image_waits_for_the_camera_to_settle_once(self):
        for expected_time in (camera.SETTLE_TIME, camera.SETTLE_TIME):
            image = Image.open(io.BytesIO(self.controller.capture_image()))
            self.assertGreater(image.convert("L").tobytes()[0], 150)
            self.assertAlmostEqual(self.clock.now, expected_time)
        self.assertEqual(self.fake.calls, [("camera", True)])

    def test_cubes(self):
        types = protocol_encoder.ObjectType
        self.fake.available_objects = {0x1234: SimpleNamespace(object_type=types.Block_LIGHTCUBE2),
                                       0x9999: SimpleNamespace(object_type=types.Charger_Basic)}
        self.assertEqual(self.controller.status()["cubes_available"], ["0x00001234"])
        result = self.controller.connect_cube(wait=1.0)
        self.assertEqual(result["object_id"], 3)
        self.assertEqual(self.controller.status()["cubes_connected"], [3])
        self.controller.set_cube_color(3, "white")
        self.assertEqual(self.fake.calls[-1], ("cube", 3, 4))
        with self.assertRaises(ToolError):
            self.controller.set_cube_color(4, "white")
        with self.assertRaises(ToolError):
            self.controller.set_cube_color(3, "purple")
        self.assertEqual(self.fake.user_handlers(), [])

    def test_no_cube(self):
        with self.assertRaises(ToolError):
            self.controller.connect_cube(wait=1.0)


class TestEdgeGuard(unittest.TestCase):
    """ The memory of the table edges limits the moves: docs/specs/edges.md. """

    def setUp(self):
        self.controller, self.fake, self.clock = make(edge_guard=True)
        self.controller.connect()
        self.fake.pose.rotation.angle_z.radians = 0.0           # facing +x, at (0, 0)
        self.fake.pose.position.x, self.fake.pose.position.y = 0.0, 0.0

    def walk(self, start: float, stop: float, step: float = 10.0) -> None:
        """ The robot has driven over the floor from x = start to x = stop. """
        x = start
        while x <= stop:
            self.controller.memory.note_pose(x, 0.0, 0.0, 1)
            x += step

    def test_a_robot_that_has_not_moved_does_not_back_up(self):
        self.controller.memory.note_pose(0.0, 0.0, 0.0, 1)
        with self.assertRaises(ToolError):
            self.controller.drive(-50)
        self.assertEqual(self.fake.calls, [])

    def test_backing_up_is_limited_to_the_floor_driven_over(self):
        self.walk(-60.0, 0.0)                                       # 60 mm of floor behind
        result = self.controller.drive(-100, 40)
        self.assertEqual(result["limited_by"], "known floor")
        self.assertLessEqual(-result["distance_mm"], 65.0)
        self.assertGreaterEqual(-result["distance_mm"], 55.0)

    def test_enough_floor_does_not_limit(self):
        self.walk(-150.0, 0.0)
        result = self.controller.drive(-50, 40)
        self.assertEqual(result["distance_mm"], -50.0)
        self.assertNotIn("limited_by", result)

    def test_forward_stops_before_a_known_edge(self):
        self.controller.memory.note_edge(150.0, 0.0, 0.0, 1, 0.0)     # the drop is at x = 170
        result = self.controller.drive(200, 40)
        self.assertEqual(result["limited_by"], "known edge")
        self.assertLess(result["distance_mm"], 150.0)
        self.assertGreater(result["distance_mm"], 60.0)

    def test_an_edge_just_ahead_refuses_to_drive_forward(self):
        self.controller.memory.note_edge(0.0, 0.0, 0.0, 1, 0.0)
        with self.assertRaises(ToolError):
            self.controller.drive(50)
        self.assertEqual(self.fake.calls, [])

    def test_no_edge_forward_is_not_limited(self):
        result = self.controller.drive(100, 40)
        self.assertEqual(result["distance_mm"], 100.0)
        self.assertNotIn("limited_by", result)

    def test_animation_with_wheels_needs_a_clear_distance_from_a_known_edge(self):
        self.controller.memory.note_edge(0.0, 0.0, 0.0, 1, 0.0)
        with self.assertRaises(ToolError):
            self.controller.play_animation("A", wheels=True)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.controller.play_animation("A")["wheels"])     # without the wheels, it plays
        self.fake.pose.position.x = -60.0                           # backed off
        self.assertTrue(self.controller.play_animation("A", wheels=True)["wheels"])

    def test_turning_needs_a_clear_distance_from_a_known_edge(self):
        self.controller.memory.note_edge(0.0, 0.0, 0.0, 1, 0.0)
        with self.assertRaises(ToolError):
            self.controller.turn(90)
        self.assertEqual(self.fake.calls, [])
        self.fake.pose.position.x = -60.0                           # backed off
        self.assertEqual(self.controller.turn(90)["angle_deg"], 90.0)

    def test_backing_stops_when_the_floor_runs_out(self):
        self.walk(-100.0, 0.0)
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            self.fake.pose.position.x = -100.0                      # the robot is at the farthest pose that it recorded

        self.controller._sleep = sleep
        result = self.controller.drive(-80, 20)
        self.assertEqual(result["stopped_by"], "unknown floor")
        self.assertEqual(self.fake.calls[-1], ("stop",))

    def test_the_cliff_flag_of_a_state_records_an_edge(self):
        self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        self.clock.now = 10.0
        self.fake.fire_state()
        self.assertEqual(len(self.controller.memory.edges), 1)
        status = self.controller.status()
        self.assertEqual(status["known_edges"], 1)
        self.assertTrue(status["edge_guard"])

    def test_states_record_the_floor(self):
        for x in range(0, 120, 20):                                 # drives forward: the floor is behind it
            self.fake.pose.position.x = float(x)
            self.fake.fire_state()
        self.assertGreater(self.controller.status()["backup_room_mm"], 0.0)

    def test_a_robot_at_a_cliff_can_back_up_but_not_drive_or_turn(self):
        self.walk(-100.0, 0.0)
        self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        for call in (lambda: self.controller.drive(50), lambda: self.controller.turn(90)):
            with self.assertRaises(ToolError):
                call()
        self.assertEqual(self.fake.calls, [])
        result = self.controller.drive(-50, 40)             # the way out of the edge
        self.assertEqual(result["distance_mm"], -50.0)
        self.assertIsNone(result["stopped_by"])             # the sensor is over the drop while it backs: not a stop
        self.assertEqual(self.fake.calls[0], ("drive", -40.0, -40.0))

    def test_a_robot_at_a_cliff_without_known_floor_cannot_back_up(self):
        self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        with self.assertRaises(ToolError):
            self.controller.drive(-50)
        self.assertEqual(self.fake.calls, [])

    def test_without_the_guard_a_cliff_stops_everything(self):
        controller, fake, _ = make(edge_guard=False)
        controller.connect()
        fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        with self.assertRaises(ToolError):
            controller.drive(-50)

    def test_the_guard_can_be_turned_off(self):
        controller, fake, _ = make(edge_guard=False)
        controller.connect()
        self.assertEqual(controller.drive(-50, 25)["distance_mm"], -50.0)
        self.assertFalse(controller.status()["edge_guard"])

    def test_a_new_connection_forgets_the_edges(self):
        self.controller.memory.note_edge(100.0, 0.0, 0.0, 1, 0.0)
        self.controller.disconnect()
        self.controller.connect()
        self.assertEqual(len(self.controller.memory.edges), 0)

    def test_disconnect_removes_the_state_handler(self):
        self.controller.disconnect()
        self.assertEqual(self.fake.handlers, [])


@unittest.skipUnless(importlib.util.find_spec("mcp"), "the mcp package is not installed")
class TestServer(unittest.TestCase):

    def test_tools_are_registered(self):
        import asyncio
        controller, _, _ = make()
        server = build_server(controller)
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in (tools.tools if hasattr(tools, "tools") else tools)}
        self.assertEqual(names, {
            "connect", "disconnect", "status", "stop", "move_head", "move_lift", "drive", "turn", "set_backpack_color",
            "say", "list_animations", "play_animation", "camera_image", "find_markers", "connect_cube",
            "set_cube_color"})

    def test_stop_is_served_while_a_drive_runs(self):
        """ Through the SDK, with real time: a stop sent 0.3 s into a 5 s drive ends it. """
        import time
        anyio = importlib.import_module("anyio")        # optional packages: imported at run time, as by the server
        Client = importlib.import_module("mcp").Client
        fake = FakeClient()
        controller = RobotController(connect_fn=lambda: fake, edge_guard=False)
        server = build_server(controller)
        results: dict[str, Any] = {}

        async def drive(client: Any) -> None:
            results["drive"] = await client.call_tool("drive", {"distance_mm": 200, "speed_mmps": 40})

        async def scenario() -> None:
            async with Client(server) as client:
                await client.call_tool("connect", {})
                start = time.monotonic()
                async with anyio.create_task_group() as tg:
                    tg.start_soon(drive, client)
                    await anyio.sleep(0.3)
                    await client.call_tool("stop", {})
                results["seconds"] = time.monotonic() - start

        anyio.run(scenario)
        self.assertLess(results["seconds"], 1.5)
        self.assertEqual(results["drive"].structured_content["stopped_by"], "stop")


class TestAnimationWheels(unittest.TestCase):
    """ play_animation(wheels=True): the animation keeps its wheel keyframes, under the checks of a drive. """

    def setUp(self):
        self.controller, self.fake, self.clock = make()
        self.controller.connect()

    def play(self) -> dict[str, Any]:
        return self.controller.play_animation("A", wheels=True)

    def test_all_keyframes_play_and_the_wheels_are_stopped_after(self):
        self.assertTrue(self.play()["wheels"])
        self.assertEqual(self.fake.calls[0], ("anim", "A", None))
        self.assertEqual(self.fake.calls[-1], ("stop",))

    def test_wheels_must_be_a_boolean(self):
        with self.assertRaises(ToolError):
            self.controller.play_animation("A", wheels="yes")       # type: ignore[arg-type]
        self.assertEqual(self.fake.calls, [])

    def test_refused_in_no_drive_mode(self):
        controller, fake, _ = make(allow_drive=False)
        controller.connect()
        with self.assertRaises(ToolError):
            controller.play_animation("A", wheels=True)
        self.assertEqual(fake.calls, [])
        self.assertFalse(controller.play_animation("A")["wheels"])

    def test_refused_when_picked_up_or_at_a_cliff(self):
        self.fake.robot_picked_up = True
        with self.assertRaises(ToolError):
            self.play()
        self.fake.robot_picked_up = False
        self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        with self.assertRaises(ToolError):
            self.play()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.user_handlers(), [])

    def test_a_cliff_during_the_animation_stops_it(self):
        self.fake.anim_ends = False
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            if self.clock.now > 0.5:
                self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED

        self.controller._sleep = sleep
        result = self.play()
        self.assertEqual(result["stopped_by"], "cliff")
        self.assertFalse(result["finished"])
        self.assertLess(self.clock.now, 1.0)
        self.assertEqual(self.fake.calls[-2:], [("cancel_anim",), ("stop",)])

    def test_a_pick_up_during_the_animation_stops_it(self):
        self.fake.anim_ends = False
        original_sleep = self.controller._sleep

        def sleep(seconds: float) -> None:
            original_sleep(seconds)
            if self.clock.now > 0.3:
                self.fake.robot_picked_up = True

        self.controller._sleep = sleep
        self.assertEqual(self.play()["stopped_by"], "picked up")

    def test_without_wheels_a_cliff_does_not_stop_the_animation(self):
        self.fake.robot_status = robot.RobotStatusFlag.CLIFF_DETECTED
        self.assertTrue(self.controller.play_animation("A")["finished"])
