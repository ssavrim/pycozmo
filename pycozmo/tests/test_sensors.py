import math

import pycozmo
from pycozmo import protocol_encoder, sensors
from pycozmo.robot import RobotOrientation, RobotStatusFlag

STILL = RobotStatusFlag.LIFT_IN_POS | RobotStatusFlag.HEAD_IN_POS

# Readings measured on a robot resting flat, with the head at -24.5, -0.4 and +29.9 degrees.
FLAT = [((-4348, 263, 9429), -24.5), ((-460, 195, 10331), -0.4), ((4610, 216, 9139), 29.9)]


def test_flat_readings_give_gravity_up_whatever_the_head_angle():
    for accel, head in FLAT:
        x, y, z = sensors.body_gravity(accel, math.radians(head))
        assert abs(x) < 700 and 10000 < z < 10600
        assert sensors.detect_orientation(accel, math.radians(head)) == RobotOrientation.ON_THREADS


def test_sides_upside_down_and_pitch():
    g = 10300
    assert sensors.detect_orientation((0, g, 0), 0.0) == RobotOrientation.ON_LEFT_SIDE
    assert sensors.detect_orientation((0, -g, 0), 0.0) == RobotOrientation.ON_RIGHT_SIDE
    assert sensors.detect_orientation((0, 0, -g), 0.0) == RobotOrientation.UPSIDE_DOWN
    assert sensors.detect_orientation((0, 0, g), 0.0, pitch_rad=1.3) == RobotOrientation.ON_BACK
    assert sensors.detect_orientation((0, 0, g), 0.0, pitch_rad=-1.3) == RobotOrientation.ON_FACE
    assert sensors.detect_orientation(None, 0.0, previous=RobotOrientation.ON_BACK) == RobotOrientation.ON_BACK


def test_hysteresis_keeps_the_state_between_thresholds():
    tilted = (0, 10300 * math.sin(math.radians(45)), 10300 * math.cos(math.radians(45)))   # z/g = 0.71
    assert sensors.detect_orientation(tilted, 0.0) == RobotOrientation.ON_THREADS
    assert sensors.detect_orientation(tilted, 0.0, previous=RobotOrientation.ON_LEFT_SIDE) \
        == RobotOrientation.ON_LEFT_SIDE


def test_client_orientation_ignores_heading():
    client = pycozmo.Client()
    changes = []
    client.add_handler(pycozmo.event.EvtRobotOrientationChange, lambda cli, o: changes.append(o))
    for heading in (0.0, 1.2, -2.5):
        pkt = protocol_encoder.RobotState(pose_angle_rad=heading, accel_x=-460, accel_y=195, accel_z=10331,
                                          head_angle_rad=math.radians(-0.4), status=STILL, cliff_data_raw=[0] * 4)
        client._on_robot_state(client, pkt)
    assert changes == []
    pkt = protocol_encoder.RobotState(accel_x=0, accel_y=10300, accel_z=0, status=STILL, cliff_data_raw=[0] * 4)
    client._on_robot_state(client, pkt)
    assert changes == [RobotOrientation.ON_LEFT_SIDE]


def feed(detector, samples, still=True, start=0.0, dt=0.033):
    taps, now = [], start
    for accel, gyro in samples:
        if detector.update(now, accel, gyro, still):
            taps.append(round(now, 2))
        now += dt
    return taps, now


def test_tap_detector_finds_a_tap_after_settling_and_only_once():
    rest = [((0, 0, 10300), (0, 0, 0))] * 30
    noise = [((0, 0, 10300 + (40 if i % 2 else -40)), (0, 0, 0)) for i in range(30)]
    detector = sensors.TapDetector()
    taps, now = feed(detector, rest + noise)
    assert taps == []
    jolt = [((0, 0, 11500), (0.2, 0, 0)), ((0, 0, 9800), (0.1, 0, 0))]
    taps, now = feed(detector, jolt + rest, start=now)
    assert len(taps) == 1


def test_tap_detector_ignores_motion_and_needs_stillness_first():
    detector = sensors.TapDetector()
    jolt = [((0, 0, 12000), (0.3, 0, 0))] * 5
    assert feed(detector, jolt, still=False)[0] == []
    # Just stopped: within the settle time a jolt is the robot's own motion.
    assert feed(detector, [((0, 0, 10300), (0, 0, 0))] + jolt)[0] == []


def test_battery_percent_curve():
    assert sensors.estimate_battery_percent(3.40) == 0
    assert sensors.estimate_battery_percent(3.60) == 10
    assert sensors.estimate_battery_percent(3.925) == 76
    assert sensors.estimate_battery_percent(4.3) == 100
    assert sensors.estimate_battery_percent(0.0) is None
    client = pycozmo.Client()
    client.battery_voltage = 3.8
    assert client.battery_percent == 52
    client.robot_status = RobotStatusFlag.IS_ON_CHARGER
    assert client.battery_percent is None
