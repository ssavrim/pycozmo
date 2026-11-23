"""

MCP server - lets an AI assistant that speaks the Model Context Protocol use the Cozmo robot (docs/specs/mcp-server.md).

The tools are plain methods of ``RobotController``, which can be used and tested without the MCP SDK. ``build_server``
exposes them through the SDK (``pip install pycozmo[mcp]``) and ``main`` runs the server on the standard input and
output of the process: nothing listens on the network.

The robot moves only in short, bounded steps and is stopped as soon as it is picked up or detects a cliff.

"""

import argparse
import functools
import importlib
import io
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import event
from . import lights
from . import protocol_encoder
from . import robot
from . import util
from .logger import logger


__all__ = [
    "Limits",
    "RobotController",
    "ToolError",
    "build_server",
    "main",
]


class ToolError(Exception):
    """ A tool cannot do what it was asked; the message is for the assistant. """


@dataclass(frozen=True)
class Limits:
    """ What the robot is allowed to do in one call. """
    #: Longest distance of a drive, in millimeters.
    max_drive_mm: float = 200.0
    #: Fastest wheel speed, in millimeters per second (the robot can do 220).
    max_speed_mmps: float = 60.0
    #: Speed of a drive when none is given.
    default_speed_mmps: float = 40.0
    #: Largest turn, in degrees.
    max_turn_deg: float = 180.0
    #: Angular speed of a turn, in degrees per second.
    turn_speed_dps: float = 60.0
    #: Longest time that the wheels run in one call, in seconds.
    max_seconds: float = 6.0


COLORS = {
    "off": lights.off_light,
    "red": lights.red_light,
    "green": lights.green_light,
    "blue": lights.blue_light,
    "white": lights.white_light,
}
CUBE_COLORS = {"off": (0, 0, 0, 0), "red": (255, 0, 0, 255), "green": (0, 255, 0, 255), "blue": (0, 0, 255, 255),
               "white": (255, 255, 255, 255)}
CUBE_TYPES = frozenset((protocol_encoder.ObjectType.Block_LIGHTCUBE1, protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                        protocol_encoder.ObjectType.Block_LIGHTCUBE3))
#: Poll interval of a movement, in seconds: how fast a pick up or a cliff stops it.
POLL_INTERVAL = 0.05


class RobotController:
    """
    The tools of the MCP server.

    ``connect_fn`` returns a connected client (by default, one that connects to the robot at ``robot_addr``).
    ``allow_drive=False`` makes the wheels unusable: the robot can still be looked at, spoken to and its head, lift and
    lights moved.
    """

    def __init__(
            self, connect_fn: Callable[[], Any] | None = None, limits: Limits | None = None, allow_drive: bool = True,
            robot_addr: tuple[str, int] | None = None, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits or Limits()
        self.allow_drive = allow_drive
        self._connect_fn = connect_fn or (lambda: _connect(robot_addr))
        self._sleep = sleep
        self._clock = clock
        self._cli: Any = None
        self._lock = threading.RLock()
        self._cube_players: dict[int, Any] = {}

    # Connection

    @property
    def client(self) -> Any:
        if self._cli is None:
            raise ToolError("Not connected to the robot: call connect first.")
        return self._cli

    def connect(self) -> dict[str, Any]:
        """ Connect to the robot (the computer must be on the Wi-Fi network of the robot). """
        with self._lock:
            if self._cli is None:
                try:
                    self._cli = self._connect_fn()
                except Exception as e:
                    raise ToolError("Failed to connect to the robot: {}".format(e)) from e
            return self.status()

    def disconnect(self) -> dict[str, Any]:
        """ Stop the robot and disconnect. """
        with self._lock:
            if self._cli is not None:
                try:
                    self._cli.stop_all_motors()
                    self._cli.disconnect()
                    self._cli.stop()
                except Exception as e:
                    logger.error("Failed to disconnect cleanly. {}".format(e))
                self._cli = None
                self._cube_players.clear()
        return {"connected": False}

    def status(self) -> dict[str, Any]:
        """ State of the robot: battery, pose, head, lift, flags, cubes. """
        cli = self.client
        flags = cli.robot_status
        pose = cli.pose
        result: dict[str, Any] = {
            "connected": True,
            "battery_percent": cli.battery_percent,
            "battery_volts": round(float(cli.battery_voltage), 2),
            "on_charger": bool(flags & robot.RobotStatusFlag.IS_ON_CHARGER),
            "charging": bool(flags & robot.RobotStatusFlag.IS_CHARGING),
            "picked_up": bool(cli.robot_picked_up or flags & robot.RobotStatusFlag.IS_PICKED_UP),
            "cliff_detected": bool(flags & robot.RobotStatusFlag.CLIFF_DETECTED),
            "moving": bool(flags & robot.RobotStatusFlag.IS_MOVING),
            "head_angle_deg": round(math.degrees(cli.head_angle.radians), 1),
            "pose_mm_deg": [round(pose.position.x, 1), round(pose.position.y, 1),
                            round(math.degrees(pose.rotation.angle_z.radians), 1)],
            "drive_allowed": self.allow_drive,
            "cubes_available": sorted(
                "0x{:08x}".format(i) for i, o in dict(cli.available_objects).items() if o.object_type in CUBE_TYPES),
            "cubes_connected": sorted(self._cube_ids()),
        }
        return result

    # Motion

    def stop(self) -> dict[str, Any]:
        """ Stop the wheels, the head and the lift at once. """
        self.client.stop_all_motors()
        return {"stopped": True}

    def move_head(self, angle_deg: float) -> dict[str, Any]:
        """ Tilt the head to an angle in degrees (-25 down to 44.5 up); out of range values are limited. """
        angle = _finite(angle_deg, "angle_deg")
        angle = min(max(angle, robot.MIN_HEAD_ANGLE.degrees), robot.MAX_HEAD_ANGLE.degrees)
        self.client.set_head_angle(math.radians(angle))
        return {"head_angle_deg": angle}

    def move_lift(self, height: float) -> dict[str, Any]:
        """ Raise the lift: 0 is down and 1 is fully up; out of range values are limited. """
        height = min(max(_finite(height, "height"), 0.0), 1.0)
        low, high = robot.MIN_LIFT_HEIGHT.mm, robot.MAX_LIFT_HEIGHT.mm
        self.client.set_lift_height(low + height * (high - low))
        return {"lift": height}

    def drive(self, distance_mm: float, speed_mmps: float | None = None) -> dict[str, Any]:
        """
        Drive straight, forward (positive) or backward (negative), for at most ``max_drive_mm``.

        It stops by itself, and at once when the robot is picked up or detects a cliff.
        """
        distance = _finite(distance_mm, "distance_mm")
        speed = self.limits.default_speed_mmps if speed_mmps is None else _finite(speed_mmps, "speed_mmps")
        speed = min(abs(speed), self.limits.max_speed_mmps)
        if speed <= 0.0:
            raise ToolError("The speed must not be 0.")
        distance = min(max(distance, -self.limits.max_drive_mm), self.limits.max_drive_mm)
        seconds = abs(distance) / speed
        sign = 1.0 if distance >= 0 else -1.0
        return self._run_wheels(sign * speed, sign * speed, seconds, {"distance_mm": distance, "speed_mmps": speed})

    def turn(self, angle_deg: float) -> dict[str, Any]:
        """
        Turn on the spot by an angle in degrees: positive is counterclockwise (to the left).

        It stops by itself, and at once when the robot is picked up or detects a cliff.
        """
        angle = min(max(_finite(angle_deg, "angle_deg"), -self.limits.max_turn_deg), self.limits.max_turn_deg)
        seconds = abs(angle) / self.limits.turn_speed_dps
        # A point turn: the wheels run in opposite directions, at the speed that gives the angular speed.
        wheel = math.radians(self.limits.turn_speed_dps) * robot.TRACK_WIDTH.mm / 2.0
        sign = 1.0 if angle >= 0 else -1.0
        return self._run_wheels(-sign * wheel, sign * wheel, seconds, {"angle_deg": angle})

    def _check_can_drive(self) -> None:
        if not self.allow_drive:
            raise ToolError("The wheels are disabled (the server was started without driving).")
        cli = self.client
        flags = cli.robot_status
        if cli.robot_picked_up or flags & robot.RobotStatusFlag.IS_PICKED_UP:
            raise ToolError("The robot is picked up: put it on the floor first.")
        if flags & robot.RobotStatusFlag.CLIFF_DETECTED:
            raise ToolError("A cliff is detected: the robot does not move.")

    def _run_wheels(self, left: float, right: float, seconds: float, result: dict[str, Any]) -> dict[str, Any]:
        self._check_can_drive()
        cli = self.client
        seconds = min(seconds, self.limits.max_seconds)
        with self._lock:
            stopped_by = None
            cli.drive_wheels(left, right)
            deadline = self._clock() + seconds
            try:
                while self._clock() < deadline:
                    self._sleep(POLL_INTERVAL)
                    flags = cli.robot_status
                    if cli.robot_picked_up or flags & robot.RobotStatusFlag.IS_PICKED_UP:
                        stopped_by = "picked up"
                    elif flags & robot.RobotStatusFlag.CLIFF_DETECTED:
                        stopped_by = "cliff"
                    if stopped_by:
                        break
            finally:
                cli.stop_all_motors()
        result["seconds"] = round(seconds, 2)
        result["stopped_by"] = stopped_by
        return result

    # Expression

    def set_backpack_color(self, color: str) -> dict[str, Any]:
        """ Light the backpack: off, red, green, blue or white. """
        light = COLORS.get(str(color).lower())
        if light is None:
            raise ToolError("Unknown color {!r}: use {}.".format(color, ", ".join(COLORS)))
        self.client.set_all_backpack_lights(light)
        return {"backpack": str(color).lower()}

    def say(self, text: str) -> dict[str, Any]:
        """ Make the robot speak (needs eSpeak NG on the computer). """
        text = str(text).strip()
        if not text or len(text) > 300:
            raise ToolError("The text must have 1 to 300 characters.")
        try:
            self.client.say_text(text)
        except Exception as e:
            raise ToolError("Failed to speak: {}".format(e)) from e
        return {"said": text}

    def list_animations(self) -> list[str]:
        """ The animation groups that the robot can play (needs the prepared resources). """
        cli = self.client
        try:
            cli.load_anims()
            names = sorted(cli.animation_groups)
        except Exception as e:
            raise ToolError("The animations are not available: {}".format(e)) from e
        return names

    def play_animation(self, name: str) -> dict[str, Any]:
        """ Play an animation group (see list_animations) and return when it is over, or after 20 seconds. """
        cli = self.client
        done = threading.Event()
        handler = cli.add_handler(event.EvtAnimationCompleted, lambda *_: done.set())
        try:
            cli.load_anims()
            cli.play_anim_group(str(name))
            finished = done.wait(20.0)
        except Exception as e:
            raise ToolError("Failed to play the animation: {}".format(e)) from e
        finally:
            cli.del_handler(event.EvtAnimationCompleted, handler)
        if not finished:
            cli.cancel_anim()
        return {"animation": str(name), "finished": finished}

    # Camera

    def capture_image(self, timeout: float = 5.0) -> bytes:
        """ A JPEG image of the camera. """
        cli = self.client
        got: list[Any] = []
        ready = threading.Event()

        def on_image(_cli: Any, image: Any) -> None:
            got.append(image)
            ready.set()

        handler = cli.add_handler(event.EvtNewRawCameraImage, on_image)
        try:
            cli.enable_camera(True)
            if not ready.wait(timeout):
                raise ToolError("No image from the camera within {} seconds.".format(timeout))
        finally:
            cli.del_handler(event.EvtNewRawCameraImage, handler)
        buffer = io.BytesIO()
        got[0].convert("RGB").save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()

    def find_markers(self) -> list[dict[str, Any]]:
        """ The markers (cubes, charger) that the camera sees now (needs the marker library of the resources). """
        from . import markers
        try:
            library = markers.load_marker_library(str(util.get_cozmo_asset_dir()))
        except (FileNotFoundError, ValueError) as e:
            raise ToolError("The marker library is not prepared (docs/specs/markers.md): {}".format(e)) from e
        from PIL import Image
        image = Image.open(io.BytesIO(self.capture_image()))
        found = markers.MarkerDetector(library).detect(image)
        result = []
        for marker in found:
            cube = markers.cube_marker(marker.type)
            result.append({"marker": marker.name, "cube": cube[0] if cube else None,
                           "center_px": [round(v) for v in marker.center], "image_size": list(image.size)})
        return result

    # Cubes

    def _cube_ids(self) -> list[int]:
        return sorted(self._cube_players)

    def connect_cube(self, wait: float = 15.0) -> dict[str, Any]:
        """ Connect the first cube that the robot sees (wake it by moving or tapping it). """
        cli = self.client
        deadline = self._clock() + min(max(float(wait), 1.0), 30.0)
        factory_id = None
        while factory_id is None and self._clock() < deadline:
            factory_id = next((i for i, o in dict(cli.available_objects).items() if o.object_type in CUBE_TYPES), None)
            if factory_id is None:
                self._sleep(0.1)
        if factory_id is None:
            raise ToolError("No cube found: wake it by moving or tapping it.")
        connected = threading.Event()
        found: dict[str, int] = {}

        def on_connection(_cli: Any, pkt: Any) -> None:
            if pkt.factory_id == factory_id and pkt.connected:
                found["object_id"] = pkt.object_id
                connected.set()

        handler = cli.add_handler(protocol_encoder.ObjectConnectionState, on_connection)
        try:
            cli.set_object_connection(factory_id)
            if not connected.wait(10.0):
                raise ToolError("The cube did not connect.")
        finally:
            cli.del_handler(protocol_encoder.ObjectConnectionState, handler)
        self._cube_players[found["object_id"]] = True
        return {"object_id": found["object_id"], "factory_id": "0x{:08x}".format(factory_id)}

    def set_cube_color(self, object_id: int, color: str) -> dict[str, Any]:
        """ Light a connected cube (see connect_cube): off, red, green, blue or white. """
        rgba = CUBE_COLORS.get(str(color).lower())
        if rgba is None:
            raise ToolError("Unknown color {!r}: use {}.".format(color, ", ".join(CUBE_COLORS)))
        if object_id not in self._cube_players:
            raise ToolError("Cube {} is not connected: call connect_cube.".format(object_id))
        state = lights.make_light_state(rgba, (0, 0, 0, 0), 1000, 0)
        self.client.set_cube_lights(object_id, (state,) * 4)
        return {"object_id": object_id, "color": str(color).lower()}


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise ToolError("{} must be a number.".format(name)) from e
    if not math.isfinite(number):
        raise ToolError("{} must be a finite number.".format(name))
    return number


def _connect(robot_addr: tuple[str, int] | None) -> Any:
    from . import client as client_module
    cli = client_module.Client(robot_addr=robot_addr, enable_procedural_face=False) if robot_addr else \
        client_module.Client(enable_procedural_face=False)
    cli.start()
    cli.connect()
    cli.wait_for_robot()
    # The first state of the robot (battery, pose) arrives a moment after it is ready.
    deadline = time.monotonic() + 3.0
    while cli.battery_voltage <= 0.0 and time.monotonic() < deadline:
        time.sleep(0.05)
    return cli


def _report_errors(tool: Callable[..., Any], sdk_error: type[Exception]) -> Callable[..., Any]:
    """ A tool whose ToolError reaches the assistant as an error with its message (the SDK hides any other error). """
    @functools.wraps(tool)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return tool(*args, **kwargs)
        except ToolError as e:
            raise sdk_error(str(e)) from e
    return wrapper


def _mcp_classes() -> tuple[Any, Any, Any]:
    """ The server, image and tool error classes of the installed ``mcp`` package (2.x names them differently). """
    try:
        module = importlib.import_module("mcp.server.mcpserver")
        errors = importlib.import_module("mcp.server.mcpserver.exceptions")
        return module.MCPServer, module.Image, errors.ToolError
    except ImportError:
        module = importlib.import_module("mcp.server.fastmcp")
        errors = importlib.import_module("mcp.server.fastmcp.exceptions")
        return module.FastMCP, module.Image, errors.ToolError


def build_server(controller: RobotController) -> Any:
    """ The MCP server (from the ``mcp`` package) with the tools of a controller. """
    Server, Image, SdkToolError = _mcp_classes()
    server = Server(
        "pycozmo",
        instructions=(
            "Controls a Cozmo robot through PyCozmo. Call connect first (the computer must be on the Wi-Fi network of "
            "the robot). Movements are short and bounded, and stop when the robot is picked up or at a cliff; "
            "call stop at any time."))
    c = controller

    def connect() -> dict[str, Any]:
        """Connect to the robot and return its state."""
        return c.connect()

    def disconnect() -> dict[str, Any]:
        """Stop the robot and disconnect."""
        return c.disconnect()

    def status() -> dict[str, Any]:
        """State of the robot: battery, pose, head, flags (picked up, cliff, charger), cubes."""
        return c.status()

    def stop() -> dict[str, Any]:
        """Stop the wheels, head and lift at once."""
        return c.stop()

    def move_head(angle_deg: float) -> dict[str, Any]:
        """Tilt the head to an angle in degrees (-25 down to 44.5 up)."""
        return c.move_head(angle_deg)

    def move_lift(height: float) -> dict[str, Any]:
        """Raise the lift: 0 is down, 1 is fully up."""
        return c.move_lift(height)

    def drive(distance_mm: float, speed_mmps: float | None = None) -> dict[str, Any]:
        """Drive straight by a distance in mm (negative: backward), at most 200 mm at up to 60 mm/s."""
        return c.drive(distance_mm, speed_mmps)

    def turn(angle_deg: float) -> dict[str, Any]:
        """Turn on the spot by an angle in degrees (positive: counterclockwise), at most 180."""
        return c.turn(angle_deg)

    def set_backpack_color(color: str) -> dict[str, Any]:
        """Light the backpack: off, red, green, blue or white."""
        return c.set_backpack_color(color)

    def say(text: str) -> dict[str, Any]:
        """Make the robot speak a short text (needs eSpeak NG)."""
        return c.say(text)

    def list_animations() -> list[str]:
        """Animation groups that the robot can play (needs the prepared resources)."""
        return c.list_animations()

    def play_animation(name: str) -> dict[str, Any]:
        """Play an animation group and wait for its end."""
        return c.play_animation(name)

    def camera_image() -> Any:
        """A JPEG image of what the robot sees."""
        return Image(data=c.capture_image(), format="jpeg")

    def find_markers() -> list[dict[str, Any]]:
        """Cubes and charger that the camera sees now (needs the marker library of the resources)."""
        return c.find_markers()

    def connect_cube() -> dict[str, Any]:
        """Connect the first cube that the robot sees (wake it by moving or tapping it)."""
        return c.connect_cube()

    def set_cube_color(object_id: int, color: str) -> dict[str, Any]:
        """Light a connected cube: off, red, green, blue or white."""
        return c.set_cube_color(object_id, color)

    for tool in (connect, disconnect, status, stop, move_head, move_lift, drive, turn, set_backpack_color, say,
                 list_animations, play_animation, camera_image, find_markers, connect_cube, set_cube_color):
        server.add_tool(_report_errors(tool, SdkToolError))
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP server for the Cozmo robot (standard input and output).")
    parser.add_argument("--no-drive", action="store_true", help="disable the wheels (look, speak, light, head, lift)")
    parser.add_argument("--max-speed", type=float, default=Limits.max_speed_mmps, help="fastest wheel speed, in mm/s")
    parser.add_argument("--max-drive", type=float, default=Limits.max_drive_mm, help="longest drive, in mm")
    args = parser.parse_args(argv)
    limits = Limits(max_speed_mmps=min(max(args.max_speed, 1.0), robot.MAX_WHEEL_SPEED.mmps),
                    max_drive_mm=min(max(args.max_drive, 1.0), 1000.0))
    controller = RobotController(limits=limits, allow_drive=not args.no_drive)
    try:
        server = build_server(controller)
    except ImportError:
        parser.exit(1, "The MCP package is missing: pip install pycozmo[mcp]\n")
    try:
        server.run()
    finally:
        controller.disconnect()
    return 0
