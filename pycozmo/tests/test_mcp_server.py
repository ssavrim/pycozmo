
import importlib.util
import io
import math
import unittest
from types import SimpleNamespace
from typing import Any

from PIL import Image

from pycozmo import event, protocol_encoder, robot
from pycozmo.mcp_server import Limits, RobotController, ToolError, build_server


class Clock:
    """ A clock that the fake sleep advances. """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeClient:
    def __init__(self) -> None:
        self.robot_status = 0
        self.robot_picked_up = False
        self.battery_voltage = 4.0
        self.battery_percent = 80
        self.head_angle = SimpleNamespace(radians=0.0)
        self.pose = SimpleNamespace(position=SimpleNamespace(x=1.0, y=2.0),
                                    rotation=SimpleNamespace(angle_z=SimpleNamespace(radians=math.pi / 2)))
        self.available_objects: dict[int, Any] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.handlers: list[tuple[Any, Any]] = []
        self.on_step: Any = None        # called by the fake wheels, to change the state while the robot moves
        self.animation_groups = {"B": object(), "A": object()}

    def add_handler(self, cls: Any, handler: Any) -> Any:
        self.handlers.append((cls, handler))
        return handler

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
        for cls, handler in list(self.handlers):
            if cls is event.EvtNewRawCameraImage:
                handler(self, Image.new("RGB", (8, 6), (10, 20, 30)))

    def load_anims(self) -> None:
        pass

    def say_text(self, text: str) -> None:
        self.calls.append(("say", text))

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def stop(self) -> None:
        self.calls.append(("close",))


def make(allow_drive: bool = True, limits: Limits | None = None) -> tuple[RobotController, FakeClient, Clock]:
    clock = Clock()
    fake = FakeClient()
    controller = RobotController(connect_fn=lambda: fake, limits=limits, allow_drive=allow_drive,
                                 sleep=clock.sleep, clock=clock)
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

    def test_turn(self):
        result = self.controller.turn(90)
        self.assertEqual(result["angle_deg"], 90)
        left, right = self.fake.calls[0][1:]
        self.assertAlmostEqual(left, -right)
        self.assertGreater(right, 0.0)                    # counterclockwise: the right wheel goes forward
        self.assertAlmostEqual(result["seconds"], 90 / Limits().turn_speed_dps)
        self.controller.turn(-1000)
        self.assertGreater(self.fake.calls[-2][1], 0.0)   # clockwise: the left wheel goes forward

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
        result = controller.drive(1000, 10)
        self.assertEqual(result["seconds"], 1.0)
        self.assertLessEqual(clock.now, 1.1)

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
        self.assertEqual(self.fake.calls, [("stop",)])


class TestExpression(unittest.TestCase):

    def setUp(self):
        self.controller, self.fake, _ = make()
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

    def test_capture_image_is_a_jpeg_and_the_handler_is_removed(self):
        data = self.controller.capture_image()
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertEqual(Image.open(io.BytesIO(data)).size, (8, 6))
        self.assertEqual(self.fake.handlers, [])

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
        self.assertEqual(self.fake.handlers, [])

    def test_no_cube(self):
        with self.assertRaises(ToolError):
            self.controller.connect_cube(wait=1.0)


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
