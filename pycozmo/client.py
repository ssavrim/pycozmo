"""

Cozmo protocol client and high-level API.

"""

from threading import Event, Lock
from typing import Any
import json
import time
import io

import numpy as np
from PIL import Image

from .logger import logger, logger_robot, logger_animation
from . import protocol_encoder
from . import protocol_utils
from . import event
from . import camera
from . import object
from . import util
from . import robot
from . import exception
from . import filter
from . import protocol_declaration
from . import conn
from . import lights
from . import image_encoder
from . import anim
from . import anim_encoder
from . import sensors
from . import audio
from . import anim_controller
from . import robot_debug


__all__ = [
    "Client",
    "CUBE_GAMMA",
]


#: Gamma of the cube LEDs, as set by the original application.
CUBE_GAMMA = 0x80


class Client(event.Dispatcher):
    """ Cozmo protocol client and high-level API class. """

    def __init__(self,
                 robot_addr: tuple[str, int] | None = None,
                 protocol_log_messages: list | None = None,
                 auto_initialize: bool = True,
                 enable_animations: bool = True,
                 enable_procedural_face: bool = True) -> None:
        super().__init__()
        # Whether to automatically initialize the robot when connection is established.
        self.auto_initialize = bool(auto_initialize)
        self._robot_found = Event()
        self._robot_ready = Event()

        self.conn = conn.Connection(robot_addr, protocol_log_messages)
        self.conn.add_child_dispatcher(self)
        self.anim_controller = anim_controller.AnimationController(self)
        self.anim_controller.enable_animations(auto_initialize and enable_animations)
        self.anim_controller.enable_procedural_face(auto_initialize and enable_animations and enable_procedural_face)

        self.serial_number_head: int | None = None
        self.robot_fw_sig: dict[str, Any] | None = None
        self.serial_number: int | None = None
        self.body_hw_version: int | None = None
        self.body_color: protocol_encoder.BodyColor | None = None
        # Camera parameters.
        self.camera_config: camera.CameraConfig | None = None
        # Saved object IDs.
        self.saved_objects: list[int] = []
        # Robot state
        # Heading in X-Y plane.
        self.pose_frame_id = 0
        self.pose = util.Pose(0.0, 0.0, 0.0, angle_z=util.Angle(radians=0.0), origin_id=1)
        self.pose_pitch = util.Angle(radians=0.0)
        self.head_angle = util.Angle(radians=robot.MIN_HEAD_ANGLE.radians)
        self.left_wheel_speed = util.Speed(mmps=0.0)
        self.right_wheel_speed = util.Speed(mmps=0.0)
        self.lift_position = robot.LiftPosition(height=robot.MIN_LIFT_HEIGHT)
        self.battery_voltage = 0.0
        self.accel = util.Vector3(0.0, 0.0, 0.0)
        self.gyro = util.Vector3(0.0, 0.0, 0.0)
        self.robot_status = 0
        self.robot_orientation = robot.RobotOrientation.ON_THREADS
        self.tap_detector = sensors.TapDetector()
        self.robot_picked_up = False
        self.robot_moving = False
        # Animation state
        self.num_anim_bytes_played = 0
        self.num_audio_frames_played = 0
        self.enabled_anim_tracks = 0
        self.tag = 0
        self.client_drop_count = 0
        # Camera state
        self.last_image_timestamp: int | None = None
        # Object state
        self.available_objects: dict[int, object.Object] = dict()
        self.connected_objects: dict[int, dict[str, Any]] = dict()
        self._cube_light_lock = Lock()
        self._cube_gamma: dict[int, int] = dict()
        # Filters
        self.packet_type_filter = filter.Filter()
        self.packet_type_filter.deny_ids({protocol_declaration.PacketType.PING.value})
        self.packet_id_filter = filter.Filter()
        self._reset_partial_state()
        # Animations
        self._clip_metadata: dict[str, anim_encoder.ClipMetadata] = {}
        self._clips: dict[str, anim_encoder.AnimClip] = {}
        self._ppclips: dict[str, anim.PreprocessedClip] = {}
        self._next_anim_id = 1
        self._current_anim_id: int | None = None
        self.animation_groups: dict[str, anim.AnimationGroup] = {}
        self._anim_cooldowns: dict[str, float] = {}   # clip name -> end of cooldown (time.monotonic)

    def start(self) -> None:
        logger.debug("Starting client...")
        self.add_handler(protocol_encoder.HardwareInfo, self._on_hardware_info)
        self.add_handler(protocol_encoder.FirmwareSignature, self._on_firmware_signature)
        self.add_handler(protocol_encoder.BodyInfo, self._on_body_info)
        self.add_handler(protocol_encoder.ImageChunk, self._on_image_chunk)
        self.add_handler(protocol_encoder.RobotState, self._on_robot_state)
        self.add_handler(protocol_encoder.AnimationState, self._on_animation_state)
        self.add_handler(protocol_encoder.ObjectAvailable, self._on_object_available)
        self.add_handler(protocol_encoder.ObjectConnectionState, self._on_object_connection_state)
        self.add_handler(protocol_encoder.DebugData, self._on_debug_data)
        self.add_handler(protocol_encoder.AnimationEvent, self._on_animation_event)
        self.add_handler(protocol_encoder.NvStorageOpResult, self._on_nv_storage_op_result)
        self.add_handler(event.EvtRobotPickedUpChange, self._on_robot_picked_up)
        self.add_handler(event.EvtRobotWheelsMovingChange, self._on_robot_moving)
        self.conn.start()

    def stop(self) -> None:
        logger.debug("Stopping client...")
        self.conn.stop()
        self.anim_controller.stop()
        self.del_all_handlers()

    def connect(self) -> None:
        logger.debug("Connecting...")
        self._robot_found.clear()
        self._robot_ready.clear()
        self.conn.connect()

    def disconnect(self) -> None:
        logger.debug("Disconnecting...")
        self._robot_found.clear()
        self._robot_ready.clear()
        self.conn.disconnect()

    def _enable_robot(self) -> None:
        # Enable
        pkt = protocol_encoder.Enable()
        self.conn.send(pkt)
        self.conn.send(pkt)  # This repetition seems to trigger BodyInfo

    def _initialize_robot(self) -> None:
        # Get camera configuration
        pkt: protocol_encoder.Packet = protocol_encoder.NvStorageOp(
            tag=protocol_encoder.NvEntryTag.NVEntry_CameraCalib, length=1, op=protocol_encoder.NvOperation.NVOP_READ)
        self.conn.send(pkt)
        # Set world frame origin to (0,0,0), frame ID to 0, and origin ID to 1.
        pkt = protocol_encoder.SetOrigin()
        self.conn.send(pkt)
        # Set timestamp to 0. Also enables RobotState and ObjectAvailable events. Requires Enable (0x25).
        pkt = protocol_encoder.SyncTime()
        self.conn.send(pkt)

        # TODO: Proper waiting for motor calibration to complete.
        time.sleep(0.5)

        self.anim_controller.start()

        self._robot_ready.set()
        self.dispatch(event.EvtRobotReady, self)

    def _on_hardware_info(self, cli: Any, pkt: protocol_encoder.HardwareInfo) -> None:
        del cli
        self.serial_number_head = pkt.serial_number_head

    def _on_firmware_signature(self, cli: Any, pkt: protocol_encoder.FirmwareSignature) -> None:
        del cli
        fw_sig = json.loads(pkt.signature)
        self.robot_fw_sig = fw_sig
        logger.info("Firmware version %s.", fw_sig["version"])
        if fw_sig.get("build") == "FACTORY":
            logger.warning("Factory/recovery firmware detected. Functionality is degraded.")
        elif fw_sig["version"] < protocol_declaration.FIRMWARE_VERSION:
            logger.warning(
                "Old firmware detected. PyCozmo works best with v{}. Functionality may be degraded.".format(
                    protocol_declaration.FIRMWARE_VERSION))
        self._enable_robot()

    def _on_body_info(self, cli: Any, pkt: protocol_encoder.BodyInfo) -> None:
        del cli
        self.serial_number = pkt.serial_number
        self.body_hw_version = pkt.body_hw_version
        self.body_color = pkt.body_color
        logger.info("Body S/N 0x%08x, HW version %i, color %i.",
                    pkt.serial_number, pkt.body_hw_version, pkt.body_color.value)
        if self.auto_initialize:
            self._initialize_robot()
        self._robot_found.set()
        self.dispatch(event.EvtRobotFound, self)

    def wait_for_robot(self, timeout: float = 5.0) -> None:
        """Wait for initialization, including events that arrived before this call.

        With automatic initialization disabled, wait for body information instead.
        The timeout applies to the complete wait.
        """
        ready = self._robot_ready if self.auto_initialize else self._robot_found
        if not ready.wait(timeout):
            message = "Failed to initialize Cozmo." if self.auto_initialize else "Failed to connect to Cozmo."
            raise exception.ConnectionTimeout(message)

    def _reset_partial_state(self) -> None:
        self._partial_image_timestamp: int | None = None
        self._partial_data: np.ndarray | None = None
        self._partial_image_id: int | None = None
        self._partial_invalid = False
        self._partial_size = 0
        self._partial_image_encoding: protocol_encoder.ImageEncoding | None = None
        self._partial_image_resolution: protocol_encoder.ImageResolution | None = None
        self._last_chunk_id = -1

    def _on_image_chunk(self, cli: Any, pkt: protocol_encoder.ImageChunk) -> None:
        del cli
        if self._partial_image_id is not None and pkt.chunk_id == 0:
            if not self._partial_invalid:
                logger.debug("Lost final chunk of image - discarding.")
            self._partial_image_id = None

        if self._partial_image_id is None:
            if pkt.chunk_id != 0:
                if not self._partial_invalid:
                    logger.debug("Received chunk of broken image.")
                self._partial_invalid = True
                return
            # Discard any previous in-progress image
            self._reset_partial_state()
            self._partial_image_timestamp = pkt.frame_timestamp
            self._partial_image_id = pkt.image_id
            self._partial_image_encoding = protocol_encoder.ImageEncoding(pkt.image_encoding)
            self._partial_image_resolution = protocol_encoder.ImageResolution(pkt.image_resolution)

            image_resolution = protocol_encoder.ImageResolution(pkt.image_resolution)
            width, height = camera.RESOLUTIONS[image_resolution]
            max_size = width * height * 3  # 3 bytes per pixel (RGB)
            self._partial_data = np.empty(max_size, dtype=np.uint8)

        if pkt.chunk_id != (self._last_chunk_id + 1) or pkt.image_id != self._partial_image_id:
            logger.debug("Image missing chunks - discarding (last_chunk_id=%d partial_image_id=%s).",
                         self._last_chunk_id, self._partial_image_id)
            self._reset_partial_state()
            self._partial_invalid = True
            return

        if self._partial_data is None:
            # No image in progress.
            return
        offset = self._partial_size
        self._partial_data[offset:offset + len(pkt.data)] = np.frombuffer(pkt.data, dtype=np.uint8)
        self._partial_size += len(pkt.data)
        self._last_chunk_id = pkt.chunk_id

        if pkt.chunk_id == pkt.image_chunk_count - 1:
            self._process_completed_image()
            self._reset_partial_state()

    def _process_completed_image(self) -> None:
        if self._partial_data is None or self._partial_image_resolution is None:
            # No image in progress.
            return
        data = self._partial_data[0:self._partial_size]

        # The first byte of the image is whether or not it is in color
        is_color_image = data[0] != 0

        if self._partial_image_encoding == protocol_encoder.ImageEncoding.JPEGMinimizedGray:
            width, height = camera.RESOLUTIONS[self._partial_image_resolution]

            if is_color_image:
                # Color images are half width
                width = width // 2
                data = camera.minicolor_to_jpeg(data, width, height)
            else:
                data = camera.minigray_to_jpeg(data, width, height)

        image = Image.open(io.BytesIO(data.tobytes())).convert('RGB')

        # Color images need to be resized to the proper resolution
        if is_color_image:
            size = camera.RESOLUTIONS[self._partial_image_resolution]
            image = image.resize(size)

        self._latest_image = image
        self.last_image_timestamp = self._partial_image_timestamp
        self.dispatch(event.EvtNewRawCameraImage, self, image)

    def _on_robot_state(self, cli: Any, pkt: protocol_encoder.RobotState) -> None:
        del cli
        self.pose_frame_id = pkt.pose_frame_id
        self.pose = util.Pose(pkt.pose_x, pkt.pose_y, pkt.pose_z,
                              angle_z=util.Angle(radians=pkt.pose_angle_rad), origin_id=pkt.pose_origin_id)
        self.pose_pitch = util.Angle(radians=pkt.pose_pitch_rad)
        self.head_angle = util.Angle(radians=pkt.head_angle_rad)
        self.left_wheel_speed = util.Speed(mmps=pkt.lwheel_speed_mmps)
        self.right_wheel_speed = util.Speed(mmps=pkt.rwheel_speed_mmps)
        self.lift_position = robot.LiftPosition(angle=util.Angle(radians=pkt.lift_height_rad))
        self.battery_voltage = pkt.battery_voltage
        self.accel = util.Vector3(pkt.accel_x, pkt.accel_y, pkt.accel_z)
        self.gyro = util.Vector3(pkt.gyro_x, pkt.gyro_y, pkt.gyro_z)
        old_status = self.robot_status
        self.robot_status = pkt.status
        self.dispatch(event.EvtRobotStateUpdated, self)
        # Dispatch status flag change events.
        for flag, evt in event.STATUS_EVENTS.items():
            if (old_status & flag) != (pkt.status & flag):
                state = (pkt.status & flag) != 0
                logger.debug("%s: %i", robot.RobotStatusFlagNames[flag], state)
                self.dispatch(evt, self, state)
        # Orientation, from gravity in the body frame (the pose angle is a heading).
        robot_orientation = sensors.detect_orientation(
            (pkt.accel_x, pkt.accel_y, pkt.accel_z), pkt.head_angle_rad, pkt.pose_pitch_rad,
            previous=self.robot_orientation)
        if self.robot_orientation != robot_orientation:
            self.robot_orientation = robot_orientation
            self.dispatch(event.EvtRobotOrientationChange, self, robot_orientation)
        # Taps, only while the robot is still.
        flags = pkt.status
        still = (flags & (robot.RobotStatusFlag.LIFT_IN_POS | robot.RobotStatusFlag.HEAD_IN_POS)
                 == robot.RobotStatusFlag.LIFT_IN_POS | robot.RobotStatusFlag.HEAD_IN_POS
                 and not flags & (robot.RobotStatusFlag.IS_PICKED_UP | robot.RobotStatusFlag.IS_FALLING
                                  | robot.RobotStatusFlag.IS_ANIMATING | robot.RobotStatusFlag.ARE_WHEELS_MOVING)
                 and abs(pkt.lwheel_speed_mmps) + abs(pkt.rwheel_speed_mmps) < 1.0
                 and robot_orientation == robot.RobotOrientation.ON_THREADS)
        if self.tap_detector.update(time.monotonic(), (pkt.accel_x, pkt.accel_y, pkt.accel_z),
                                    (pkt.gyro_x, pkt.gyro_y, pkt.gyro_z), still):
            self.dispatch(event.EvtRobotTapped, self)

    @property
    def battery_percent(self) -> int | None:
        """ Indicative charge in %, from the battery voltage; None on the charger or if unknown. """
        if self.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER:
            return None
        return sensors.estimate_battery_percent(self.battery_voltage)

    def _on_robot_picked_up(self, cli: Any, state: bool) -> None:
        del cli
        if state:
            self.robot_picked_up = True
        else:
            # Robot put down - reset world frame origin.
            self.robot_picked_up = False
            pkt = protocol_encoder.SetOrigin(
                pose_frame_id=self.pose_frame_id + 1, pose_origin_id=self.pose.origin_id + 1)
            self.conn.send(pkt)

    def _on_robot_moving(self, cli: Any, state: bool) -> None:
        self.robot_moving = state

    def _on_animation_state(self, cli: Any, pkt: protocol_encoder.AnimationState) -> None:
        del cli
        self.num_anim_bytes_played = pkt.num_anim_bytes_played
        self.num_audio_frames_played = pkt.num_audio_frames_played
        self.enabled_anim_tracks = pkt.enabled_anim_tracks
        self.tag = pkt.tag
        self.client_drop_count = pkt.client_drop_count

    def _on_animation_event(self, cli: Any, pkt: protocol_encoder.AnimationEvent) -> None:
        del cli
        # Ignore events of an animation other than the current one.
        if self._current_anim_id is not None and pkt.anim_id != self._current_anim_id:
            logger.debug("Ignoring %s of animation %d.", pkt.event_id.name, pkt.anim_id)
            return
        self.dispatch(event.EvtAnimationEvent, self, pkt.event_id, pkt.timestamp)

    def _on_object_available(self, cli: Any, pkt: protocol_encoder.ObjectAvailable) -> None:
        del cli
        factory_id = pkt.factory_id
        object_type = protocol_encoder.ObjectType(pkt.object_type)
        obj = object.Object(factory_id=factory_id, object_type=object_type)
        if factory_id not in self.available_objects:
            self.available_objects[factory_id] = obj
            logger.debug("Object of type %s with S/N 0x%08x available.", str(obj.object_type), obj.factory_id)

    def _on_object_connection_state(self, cli: Any, pkt: protocol_encoder.ObjectConnectionState) -> None:
        del cli
        # A cube that connects again may have lost its gamma.
        with self._cube_light_lock:
            self._cube_gamma.pop(pkt.object_id, None)
        if pkt.connected:
            # Connected
            self.connected_objects[pkt.object_id] = {
                "factory_id": pkt.factory_id,
                "object_type": pkt.object_type,
            }
        else:
            # Disconnected
            current = self.connected_objects.get(pkt.object_id)
            if current and (pkt.factory_id == 0 or current["factory_id"] == pkt.factory_id):
                del self.connected_objects[pkt.object_id]

    def set_object_connection(self, factory_id: int, connect: bool = True) -> None:
        """Request a connection to a discovered cube or other Bluetooth object.

        Completion is asynchronous; listen for ObjectConnectionState and use its
        object_id when addressing the cube. Some robots reuse a single object slot.
        """
        self.conn.send(protocol_encoder.ObjectConnect(factory_id=factory_id, connect=connect))

    def set_cube_lights(self, object_id: int, states: Any, *, rotation_period_frames: int = 0,
                        gamma: int | None = CUBE_GAMMA) -> None:
        """Set four cube lights by connected object ID.

        Calls to this method serialize selection and light packets. Direct sends
        of CubeId or CubeLights must also be coordinated by the application.

        The gamma of the cube LEDs is sent before the first lights of a cube (and again after the cube connects
        again), as the original application does; without it, white shows as yellow and red. Pass None to not send it.
        """
        select = protocol_encoder.CubeId(object_id=object_id, rotation_period_frames=rotation_period_frames)
        command = protocol_encoder.CubeLights(states=states)
        with self._cube_light_lock:
            if gamma is not None and self._cube_gamma.get(object_id) != gamma:
                self.conn.send(protocol_encoder.SetCubeGamma(gamma=gamma))
                self._cube_gamma[object_id] = gamma
            self.conn.send(select)
            self.conn.send(command)

    def set_object_accel_stream(self, object_id: int, enable: bool = True) -> None:
        """Enable or disable ObjectAccel packets for a connected object ID."""
        self.conn.send(protocol_encoder.StreamObjectAccel(object_id=object_id, enable=enable))

    def _on_debug_data(self, cli: Any, pkt: protocol_encoder.DebugData) -> None:
        del cli
        msg = robot_debug.get_debug_message(pkt.name_id, pkt.format_id, pkt.args)
        logger_robot.log(robot_debug.get_log_level(pkt.level), msg)

    def _on_nv_storage_op_result(self, cli: Any, pkt: protocol_encoder.NvStorageOpResult) -> None:
        logger.debug("NV storage operation result: {}".format(pkt))
        if pkt.op == protocol_encoder.NvOperation.NVOP_READ:
            if pkt.result == protocol_encoder.NvResult.NV_OKAY:
                if pkt.tag == protocol_encoder.NvEntryTag.NVEntry_CameraCalib and len(pkt.data) == 56:
                    values = protocol_utils.BinaryReader(pkt.data).read_farray("f", 14)
                    self.camera_config = camera.CameraConfig(
                        values[0], values[1],
                        values[2], values[3],
                        57.82, 45.0, 1, 67, 0.1, 3.984375)
                    # Get saved cube IDs
                    req = protocol_encoder.NvStorageOp(
                        tag=protocol_encoder.NvEntryTag.NVEntry_SavedCubeIDs, length=28,
                        op=protocol_encoder.NvOperation.NVOP_READ)
                    self.conn.send(req)
                elif pkt.tag == protocol_encoder.NvEntryTag.NVEntry_SavedCubeIDs and len(pkt.data) == 28:
                    values = protocol_utils.BinaryReader(pkt.data).read_farray("L", 7)
                    self.saved_objects = [value for value in values[-3:] if value]
                    logger.debug("Saved cube IDs: {}".format(
                        ", ".join("0x{:08x}".format(i) for i in self.saved_objects)))
                    # Remove handler.
                    self.del_handler(protocol_encoder.NvStorageOpResult, self._on_nv_storage_op_result)

    def set_head_angle(self, angle: float, accel: float = 10.0, max_speed: float = 10.0,
                       duration: float = 0.0) -> None:
        pkt = protocol_encoder.SetHeadAngle(angle_rad=angle, accel_rad_per_sec2=accel,
                                            max_speed_rad_per_sec=max_speed, duration_sec=duration)
        self.conn.send(pkt)

    def move_head(self, speed: float) -> None:
        pkt = protocol_encoder.MoveHead(speed_rad_per_sec=speed)
        self.conn.send(pkt)

    def set_lift_height(self, height: float, accel: float = 10.0, max_speed: float = 10.0,
                        duration: float = 0.0) -> None:
        pkt = protocol_encoder.SetLiftHeight(height_mm=height, accel_rad_per_sec2=accel,
                                             max_speed_rad_per_sec=max_speed, duration_sec=duration)
        self.conn.send(pkt)

    def move_lift(self, speed: float) -> None:
        pkt = protocol_encoder.MoveLift(speed_rad_per_sec=speed)
        self.conn.send(pkt)

    def drive_wheels(self, lwheel_speed: float, rwheel_speed: float,
                     lwheel_acc: float | None = 0.0, rwheel_acc: float | None = 0.0,
                     duration: float | None = None) -> None:
        pkt = protocol_encoder.DriveWheels(lwheel_speed_mmps=lwheel_speed, rwheel_speed_mmps=rwheel_speed,
                                           lwheel_accel_mmps2=lwheel_acc, rwheel_accel_mmps2=rwheel_acc)
        self.conn.send(pkt)
        if duration is not None:
            time.sleep(duration)
            self.stop_all_motors()

    def stop_all_motors(self) -> None:
        pkt = protocol_encoder.StopAllMotors()
        self.conn.send(pkt)

    def go_to_pose(self, pose: util.Pose, relative_to_robot: bool = False) -> None:
        """ Move to a specific pose (position and orientation). """

        if relative_to_robot:
            pose = util.Pose(self.pose.position.x, self.pose.position.y,
                             self.pose.position.z, angle_z=self.pose.rotation.angle_z).define_pose_relative_this(pose)

        pkt: protocol_encoder.Packet = protocol_encoder.AppendPathSegLine(
            from_x=self.pose.position.x, from_y=self.pose.position.y,
            to_x=pose.position.x, to_y=pose.position.y,
            speed_mmps=100.0, accel_mmps2=20.0, decel_mmps2=20.0)
        self.conn.send(pkt)
        pkt = protocol_encoder.AppendPathSegPointTurn(
            x=pose.position.x, y=pose.position.y,
            angle_rad=pose.rotation.angle_z.radians,
            angle_tolerance_rad=0.01,
            speed_mmps=40.0, accel_mmps2=20.0, decel_mmps2=20.0)
        self.conn.send(pkt)
        pkt = protocol_encoder.ExecutePath(event_id=1)
        self.conn.send(pkt)

        e = Event()

        def event_wait(_: Any, pkt2: protocol_encoder.PathFollowingEvent) -> None:
            if pkt2.event_type != protocol_encoder.PathEventType.PATH_STARTED:
                e.set()

        self.add_handler(protocol_encoder.PathFollowingEvent, event_wait)
        e.wait()

    def set_backpack_lights(self,
                            left_light: protocol_encoder.LightState,
                            front_light: protocol_encoder.LightState,
                            center_light: protocol_encoder.LightState,
                            rear_light: protocol_encoder.LightState,
                            right_light: protocol_encoder.LightState) -> None:
        pkt: protocol_encoder.Packet = protocol_encoder.LightStateCenter(states=(front_light, center_light, rear_light))
        self.conn.send(pkt)
        pkt = protocol_encoder.LightStateSide(states=(left_light, right_light))
        self.conn.send(pkt)

    def set_center_backpack_lights(self, light: protocol_encoder.LightState) -> None:
        self.set_backpack_lights(lights.off_light, light, light, light, lights.off_light)

    def set_all_backpack_lights(self, light: protocol_encoder.LightState) -> None:
        """ Set the five backpack lights. The left and right LEDs are red only: they stay off for white and grey. """
        side = lights.side_light(light)
        self.set_backpack_lights(side, light, light, light, side)

    def set_backpack_lights_off(self) -> None:
        self.set_backpack_lights(lights.off_light, lights.off_light, lights.off_light,
                                 lights.off_light, lights.off_light)

    def set_head_light(self, enable: bool) -> None:
        pkt = protocol_encoder.SetHeadLight(enable=enable)
        self.conn.send(pkt)

    def enable_camera(self, enable: bool = True, color: bool = False) -> None:
        """ Enable or disable camera image streaming in color or grayscale. """
        image_send_mode = protocol_encoder.ImageSendMode.Stream if enable else protocol_encoder.ImageSendMode.Off
        pkt: protocol_encoder.Packet = protocol_encoder.EnableCamera(image_send_mode=image_send_mode)
        self.conn.send(pkt)
        pkt = protocol_encoder.EnableColorImages(enable=color)
        self.conn.send(pkt)

    def clear_screen(self) -> None:
        """ Clear the screen and resume procedural face, if enabled. """
        self.anim_controller.clear_screen()

    def display_image(self, im: Image.Image, duration: float | None = None) -> None:
        """
        Display a 128x32 binary image on the robot screen.

        Procedural face, if enabled, is paused while the image is shown. If duration is given, the image is shown
        for duration seconds and the screen is cleared. Otherwise, the image is shown until clear_screen() is called
        or another image is displayed.
        """
        encoder = image_encoder.ImageEncoder(im)
        buf = bytes(encoder.encode())
        pkt = protocol_encoder.DisplayImage(image=buf)
        self.anim_controller.display_image(pkt)
        if duration is not None:
            time.sleep(duration)
            self.clear_screen()

    def _load_clips(self, fspec: str) -> None:

        start_time = time.perf_counter()

        if fspec.endswith(".bin"):
            clips = anim_encoder.AnimClips.from_fb_file(fspec)
        elif fspec.endswith(".json"):
            clips = anim_encoder.AnimClips.from_json_file(fspec)
        else:
            raise ValueError("Unsupported animation file format.")
        for clip in clips.clips:
            self._clips[clip.name] = clip

        logger.debug("Loaded {} in {:.02f} s.".format(fspec, time.perf_counter() - start_time))

    def play_anim_ppclip(self, ppclip: anim.PreprocessedClip) -> None:

        # Ensure no other animation is playing.
        self.cancel_anim()

        # Start animation.
        pkt: protocol_encoder.Packet = protocol_encoder.StartAnimation(anim_id=self._next_anim_id)
        self._current_anim_id = self._next_anim_id
        self.anim_controller.play_anim_frame(None, None, (pkt, ))
        # The ID is one byte: wrap around (1-255) instead of failing after 255 animations.
        self._next_anim_id = self._next_anim_id % 255 + 1

        # Send frames to the animation controller, one per 1/30 s. Keyframes whose trigger
        # times fall in the same frame share it: a frame per keyframe stretched the animation
        # and put silent frames between consecutive audio frames (choppy sound).
        frames: dict[int, list[Any]] = {}
        start = min(ppclip.keyframes) if ppclip.keyframes else 0
        for at in sorted(ppclip.keyframes):
            index = round((at - start) * robot.FRAME_RATE / 1000)
            frame = frames.setdefault(index, [None, None, {}])
            for action in ppclip.keyframes[at]:
                if isinstance(action, protocol_encoder.OutputAudio):
                    frame[0] = action
                elif isinstance(action, protocol_encoder.DisplayImage):
                    frame[1] = action
                elif isinstance(action, protocol_encoder.Packet):
                    # The robot takes one keyframe of each kind per frame: the latest wins.
                    frame[2].pop(type(action), None)
                    frame[2][type(action)] = action
        for index in range(max(frames) + 1 if frames else 0):
            audio_pkt, image_pkt, pkts = frames.get(index, (None, None, {}))
            self.anim_controller.play_anim_frame(audio_pkt, image_pkt, list(pkts.values()))

        # End animation.
        pkt = protocol_encoder.EndAnimation()
        self.anim_controller.play_anim_frame(None, None, (pkt, ))

    def play_anim(self, name: str, *, allowed_types: Any = None) -> None:
        """Play an original clip, optionally filtering to specified keyframe types."""
        if not self._clip_metadata:
            raise ValueError("Animations not loaded.")
        elif name not in self._clip_metadata:
            raise ValueError("Unknown clip name.")

        if name not in self._clips:
            self._load_clips(self._clip_metadata[name].fspec)
        if allowed_types is not None:
            ppclip = anim.PreprocessedClip.from_anim_clip(self._clips[name], allowed_types=allowed_types)
        else:
            if name not in self._ppclips:
                self._ppclips[name] = anim.PreprocessedClip.from_anim_clip(self._clips[name])
            ppclip = self._ppclips[name]
        self.play_anim_ppclip(ppclip)

    def cancel_anim(self) -> None:
        self.anim_controller.cancel_anim()

    def play_anim_group(self, anim_group_name: str, *, allowed_types: Any = None) -> str | None:
        """
        Play an animation of a group, optionally filtering to specified keyframe types.

        Returns the name of the clip that plays, or None when the group is unknown or none of its clips can play now.
        """
        logger_animation.info("Playing animation group {}".format(anim_group_name))
        animation_group = self.animation_groups.get(anim_group_name)
        if not animation_group:
            logger_animation.error("Failed to find animation group {}.".format(anim_group_name))
            return None
        member = animation_group.choose_member(cooldowns=self._anim_cooldowns,
                                               head_angle_rad=self.head_angle.radians)
        if member is None:
            logger_animation.error("No animation of group {} can be played.".format(anim_group_name))
            return None
        logger_animation.info("Playing animation {}".format(member.name))
        self.play_anim(member.name, allowed_types=allowed_types)
        return str(member.name)

    def load_anims(self, anim_dir: str | None = None) -> None:
        """Load animation clips; optionally provide a directory of extracted assets.

        Animation groups are loaded only for the standard asset installation.
        """
        standard_assets = anim_dir is None
        if standard_assets:
            util.check_assets()
            anim_dir = str(util.get_cozmo_anim_dir())
        metadata = anim_encoder.get_clip_metadata(str(anim_dir))
        groups = anim.load_animation_groups(str(util.get_cozmo_asset_dir())) if standard_assets else {}
        self._clip_metadata = metadata
        self._clips = {}
        self._ppclips = {}
        self.animation_groups = groups

    def get_anim_names(self) -> set:
        return set(self._clip_metadata.keys())

    @property
    def anim_names(self) -> set:
        return self.get_anim_names()

    def set_volume(self, level: int) -> None:
        """ Set audio output volume to a level in the range 0-65535. """
        pkt = protocol_encoder.SetRobotVolume(level=level)
        self.conn.send(pkt)

    def play_audio(self, fspec: str) -> None:
        pkts = audio.load_wav(fspec)
        self.anim_controller.play_audio(pkts)

    def play_wem(self, fspec: str, decoder: str = "vgmstream-cli") -> None:
        """Decode and play a Wwise WEM using an externally installed decoder."""
        self.anim_controller.play_audio(audio.load_wem(fspec, decoder=decoder))

    def say_text(self, text: str, *, voice: str = "en", words_per_minute: int = 175,
                 executable: str = "espeak-ng") -> None:
        """Speak text using a separately installed eSpeak NG executable."""
        packets = audio.synthesize_speech(text, voice=voice, words_per_minute=words_per_minute,
                                          executable=executable)
        self.anim_controller.play_audio(packets)

    def activate_behavior(self, behavior):
        self.add_child_dispatcher(behavior)
        behavior.activate()

    def deactivate_behavior(self, behavior):
        self.del_child_dispatcher(behavior)
        behavior.deactivate()

    def enable_animations(self, enabled: bool = True) -> None:
        self.anim_controller.enable_animations(enabled)

    def enable_procedural_face(self, enabled: bool = True) -> None:
        self.anim_controller.enable_procedural_face(enabled)
