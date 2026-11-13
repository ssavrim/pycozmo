"""

Interpretation of robot sensor readings: body orientation, taps and battery level.

The inertial measurement unit (IMU) sits in the head, so its readings turn with the head
angle. Values below were measured on one robot over Wi-Fi; they are starting points, not
guarantees for every unit or firmware.

"""

from typing import Optional, Tuple
import math

from . import robot


__all__ = [
    "body_gravity",
    "detect_orientation",
    "TapDetector",
    "BATTERY_CURVE",
    "estimate_battery_percent",
]


def body_gravity(accel: Tuple[float, float, float], head_angle_rad: float) -> Tuple[float, float, float]:
    """ Accelerometer reading (head frame, mm/s^2) rotated into the body frame.

    Returns (x forward, y left, z up). Resting flat, z is about +10,300 mm/s^2 whatever the
    head angle: readings of (-4348, 263, 9429) at -24.5 degrees and (4610, 216, 9139) at
    +29.9 degrees give x within 600 and z between 10,200 and 10,400 mm/s^2.
    """
    ax, ay, az = accel
    c, s = math.cos(head_angle_rad), math.sin(head_angle_rad)
    return ax * c - az * s, ay, ax * s + az * c


def detect_orientation(accel: Tuple[float, float, float], head_angle_rad: float,
                       pitch_rad: Optional[float] = None,
                       previous: robot.RobotOrientation = robot.RobotOrientation.ON_THREADS
                       ) -> robot.RobotOrientation:
    """ Body orientation from gravity, with hysteresis.

    The robot pose angle is a heading and says nothing about lying on a side; gravity in
    the body frame does. Leaving the upright state needs a clear tilt (z below 55% of g),
    returning to it a clearer one (above 80%), so a tilted table or a bump does not make
    the state flicker. Between thresholds the previous orientation is kept.
    """
    if accel is None:
        return previous
    x, y, z = body_gravity(accel, head_angle_rad)
    g = max(math.sqrt(x * x + y * y + z * z), 1.0)
    if pitch_rad is not None and pitch_rad > 1.0:
        return robot.RobotOrientation.ON_BACK
    if pitch_rad is not None and pitch_rad < -1.0:
        return robot.RobotOrientation.ON_FACE
    upright_limit = 0.55 if previous == robot.RobotOrientation.ON_THREADS else 0.8
    if z / g > upright_limit:
        return robot.RobotOrientation.ON_THREADS
    if z / g < -0.7:
        return robot.RobotOrientation.UPSIDE_DOWN
    if abs(y) / g > 0.7:
        return robot.RobotOrientation.ON_LEFT_SIDE if y > 0 else robot.RobotOrientation.ON_RIGHT_SIDE
    if abs(x) / g > 0.7:
        return robot.RobotOrientation.ON_FACE if x > 0 else robot.RobotOrientation.ON_BACK
    return previous


class TapDetector:
    """ Taps on the robot's head or body, from IMU readings.

    The back touch sensor value read 0 on the tested robot, even when stroked, and the
    robot's poke report arrived for collisions while driving but not for light taps. At
    rest the gyroscope read exactly 0 rad/s and the acceleration magnitude stayed within
    about +/-60 mm/s^2 of its mean; three head and three back taps gave gyroscope peaks of
    0.06-0.36 rad/s and magnitude swings of 150-2,500 mm/s^2. All six were detected with
    these thresholds and no false detection.

    Samples only count while the robot is still (no animation, head and lift in position,
    wheels stopped, not picked up): its own motion would look like taps.
    """

    GYRO_RAD_S = 0.04
    ACCEL_MMPS2 = 300.0       # away from the resting magnitude
    REFRACTORY_S = 1.0        # one tap reported per jolt
    SETTLE_S = 0.6            # stillness required after the robot moved itself

    def __init__(self):
        self.baseline = None
        self.still_since = None
        self.last_tap = -1e9

    def update(self, now: float, accel: Tuple[float, float, float], gyro: Tuple[float, float, float],
               still: bool) -> bool:
        """ Feed one robot state sample; True when a tap is detected. """
        if accel is None or gyro is None:
            return False
        magnitude = math.sqrt(sum(a * a for a in accel))
        if not still:
            self.still_since = None
            return False
        if self.still_since is None:
            self.still_since = now
            if self.baseline is None:
                self.baseline = magnitude
            return False
        spin = math.sqrt(sum(g * g for g in gyro))
        jolt = abs(magnitude - self.baseline)
        settled = now - self.still_since >= self.SETTLE_S
        if settled and (spin > self.GYRO_RAD_S or jolt > self.ACCEL_MMPS2) \
                and now - self.last_tap >= self.REFRACTORY_S:
            self.last_tap = now
            return True
        if spin <= self.GYRO_RAD_S and jolt <= self.ACCEL_MMPS2:
            # Follow slow drift (temperature, table tilt) only while nothing happens.
            self.baseline = 0.98 * self.baseline + 0.02 * magnitude
        return False


#: Off-charger battery voltage (V) to estimated charge (%). LiPo-like curve fitted to one
#: robot: about 3.95 V after hours of use, 3.6 V when a recharge is due, restart near 3.4 V.
BATTERY_CURVE = ((3.45, 0), (3.55, 5), (3.60, 10), (3.65, 18), (3.70, 28), (3.75, 40), (3.80, 52),
                 (3.85, 63), (3.90, 72), (3.95, 80), (4.00, 87), (4.05, 93), (4.10, 98), (4.15, 100))


def estimate_battery_percent(voltage: float) -> Optional[int]:
    """ Indicative charge in % from the off-charger battery voltage, or None if unknown.

    On the charger the reading follows the charging circuit, not the charge level.
    """
    if not isinstance(voltage, (int, float)) or not 0 < voltage < 10:
        return None
    points = BATTERY_CURVE
    if voltage <= points[0][0]:
        return 0
    if voltage >= points[-1][0]:
        return 100
    for (v0, p0), (v1, p1) in zip(points, points[1:]):
        if v0 <= voltage <= v1:
            return round(p0 + (p1 - p0) * (voltage - v0) / (v1 - v0))
    return None
