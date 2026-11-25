"""

Edges of the table: where the cliff sensor saw a drop, and the floor that the robot has stood on (docs/specs/edges.md).

The cliff sensor looks down just ahead of the robot; nothing watches the sides or the back. ``TableMemory`` keeps two
memories, in the frame of the current pose origin (a pick up starts a new origin, and both are forgotten):

- the edges: where the sensor saw a drop, taken as a short straight segment across the heading at that moment;
- the floor: the footprint of the robot at each recorded pose.

Blind moves can be checked with it, before and while they run: backing up only onto floor that the robot has stood on,
turning on the spot only with every known edge out of reach of the body, driving forward only until the front nears a
known edge. ``CliffRetreat`` is the reflex for when the sensor sees a drop: stop, back off (within the known floor),
then turn away once the body can turn without swinging over the edge.

Ported from Cozmonaute (ssavrim/cozmonaute), where it was validated on a robot. It only computes: the caller feeds it
the poses and the flags of the robot, and sends the wheel commands that ``CliffRetreat`` returns.

"""

import math
import random
from collections import deque
from typing import Any

from . import robot


__all__ = [
    "TableMemory",
    "CliffRetreat",
    "FRONT_MM",
    "REAR_MM",
    "HALF_WIDTH_MM",
    "SENSOR_MM",
    "SWEEP_MM",
    "TURN_MARGIN_MM",
]


#: The robot footprint around its pose origin, which sits near the front: most of the body, and the corners that swing
#: widest in a turn, are behind it. Millimeters.
FRONT_MM = 22.1
REAR_MM = 55.9
HALF_WIDTH_MM = 27.1
#: The cliff sensor, under the front: where a detected drop lies ahead of the pose origin.
SENSOR_MM = 20.0
#: A seen edge is taken as straight this far on each side of the drop.
EDGE_HALF_SPAN_MM = 80.0
#: The corner that is the farthest from the center of a turn on the spot.
SWEEP_MM = math.hypot(max(FRONT_MM, REAR_MM), HALF_WIDTH_MM)
TURN_MARGIN_MM = 15.0
FLOOR_TOLERANCE_MM = 5.0

#: A command of ``CliffRetreat``: ('wheels', left, right), ('stop',), ('react', situation) or ('done', outcome).
Command = tuple[Any, ...]


def _angle(value: float) -> float:
    """ An angle in radians, wrapped to -pi..pi. """
    return math.atan2(math.sin(value), math.cos(value))


class TableMemory:
    """ The edges that the cliff sensor saw and the floor that the robot stood on. """

    MAX_POSES = 600
    MAX_EDGES = 24
    #: An edge is forgotten after this long without being seen again (seconds).
    EDGE_TTL_S = 1800.0
    #: A pose is recorded once the robot moved or turned this much since the last one.
    POSE_STEP_MM = 10.0
    POSE_STEP_RAD = math.radians(10)
    #: Detections closer than this are the same edge.
    EDGE_MERGE_MM = 15.0
    #: The cliff flag can come this long before the picked up flag (seconds): edges seen just before a lift are dropped.
    LIFT_EDGE_LEAD_S = 2.5
    #: After a pick up, the cliff sensor is not trusted for this long (seconds): the wheels settle on the table.
    PUT_DOWN_GRACE_S = 1.5

    def __init__(self) -> None:
        self.origin: int | None = None
        self.poses: deque[tuple[float, float, float]] = deque(maxlen=self.MAX_POSES)       # x, y, heading
        self.edges: deque[tuple[float, float, float, float]] = deque(maxlen=self.MAX_EDGES)   # x, y, heading, seen
        self._grace_until = 0.0

    def _use(self, origin: int) -> None:
        if origin != self.origin:
            self.origin = origin
            self.poses.clear()
            self.edges.clear()

    def observe(self, x: float, y: float, heading: float, origin: int, flags: int, now: float) -> None:
        """
        Feed the pose and the status flags of the robot, at each robot state.

        The pose is recorded unless the robot is picked up or falling.

        A pick up drops the edges seen just before it (the sensor sees a drop before the lift is reported) and mutes
        the sensor for a moment (it sees one again while the robot is put down). Otherwise a cliff flag records an
        edge, except on the charger (its platform edge looks like a drop while the robot climbs on or off).
        """
        lifted = bool(flags & (robot.RobotStatusFlag.IS_PICKED_UP | robot.RobotStatusFlag.IS_FALLING))
        if lifted:
            if now >= self._grace_until:
                self.forget_edges_since(now - self.LIFT_EDGE_LEAD_S)
            self._grace_until = now + self.PUT_DOWN_GRACE_S
        else:
            self.note_pose(x, y, heading, origin)
        if (flags & robot.RobotStatusFlag.CLIFF_DETECTED and not lifted
                and not flags & robot.RobotStatusFlag.IS_ON_CHARGER and now >= self._grace_until):
            self.note_edge(x, y, heading, origin, now)

    def note_pose(self, x: float, y: float, heading: float, origin: int) -> None:
        """ The robot is at this pose: its footprint is floor. """
        self._use(origin)
        if self.poses:
            px, py, ph = self.poses[-1]
            if math.hypot(x - px, y - py) < self.POSE_STEP_MM and abs(_angle(heading - ph)) < self.POSE_STEP_RAD:
                return
        self.poses.append((x, y, heading))

    def note_edge(self, x: float, y: float, heading: float, origin: int, now: float) -> None:
        """ The sensor sees a drop with the robot at this pose. """
        self._use(origin)
        ex, ey = x + SENSOR_MM * math.cos(heading), y + SENSOR_MM * math.sin(heading)
        for i, (ox, oy, oh, _) in enumerate(self.edges):
            if math.hypot(ex - ox, ey - oy) < self.EDGE_MERGE_MM:
                self.edges[i] = (ox, oy, oh, now)
                return
        self.edges.append((ex, ey, heading, now))

    def forget_edges_since(self, since: float) -> None:
        """ Drop the edges seen at or after ``since``. """
        self.edges = deque((e for e in self.edges if e[3] < since), maxlen=self.MAX_EDGES)

    def edges_known(self, origin: int, now: float) -> list[tuple[float, float, float, float]]:
        """ The edges of the origin that were seen within EDGE_TTL_S. """
        return [e for e in self.edges if origin == self.origin and now - e[3] <= self.EDGE_TTL_S]

    def edge_distance(self, x: float, y: float, origin: int, now: float) -> float:
        """ Distance from a point to the nearest known edge segment (infinity if none). """
        best = math.inf
        for ex, ey, heading, _ in self.edges_known(origin, now):
            # The segment lies across the heading, centered on the drop.
            ux, uy = -math.sin(heading), math.cos(heading)
            t = max(-EDGE_HALF_SPAN_MM, min(EDGE_HALF_SPAN_MM, (x - ex) * ux + (y - ey) * uy))
            best = min(best, math.hypot(x - (ex + t * ux), y - (ey + t * uy)))
        return best

    def can_turn(self, x: float, y: float, origin: int, now: float) -> bool:
        """ Whether the body can turn on the spot at this position without swinging over a known edge. """
        return self.edge_distance(x, y, origin, now) >= SWEEP_MM + TURN_MARGIN_MM

    def on_floor(self, px: float, py: float, lateral_tolerance_mm: float = FLOOR_TOLERANCE_MM) -> bool:
        """
        Whether a point is within the footprint of a recorded pose, with a tolerance around it (FLOOR_TOLERANCE_MM along
        the robot, and ``lateral_tolerance_mm`` across it).
        """
        for x, y, heading in self.poses:
            c, s = math.cos(heading), math.sin(heading)
            dx, dy = px - x, py - y
            along, across = dx * c + dy * s, -dx * s + dy * c
            if (-REAR_MM - FLOOR_TOLERANCE_MM <= along <= FRONT_MM + FLOOR_TOLERANCE_MM
                    and abs(across) <= HALF_WIDTH_MM + lateral_tolerance_mm):
                return True
        return False

    def backup_room(self, x: float, y: float, heading: float, origin: int,
                    limit_mm: float = 100.0, step_mm: float = 5.0,
                    lateral_tolerance_mm: float = FLOOR_TOLERANCE_MM) -> float:
        """
        How far the robot can back up with its rear on floor that it has stood on (0 if none is known).

        The rear corners are on the edge of the known strip, so the heading of the robot, which drifts by a few degrees
        while it backs up, moves them sideways by several millimeters: ``lateral_tolerance_mm`` is how far to the side
        of the strip they may be.
        """
        if origin != self.origin or not self.poses:
            return 0.0
        c, s = math.cos(heading), math.sin(heading)
        room, d = 0.0, step_mm
        while d <= limit_mm + 1e-6:
            back = REAR_MM + d
            # The rear center and the rear corners: local (-back, across) in the world frame.
            points = [(x - back * c - across * s, y - back * s + across * c)
                      for across in (-HALF_WIDTH_MM, 0.0, HALF_WIDTH_MM)]
            if not all(self.on_floor(px, py, lateral_tolerance_mm) for px, py in points):
                break
            room, d = d, d + step_mm
        return room

    def clear_ahead(self, x: float, y: float, heading: float, origin: int, now: float,
                    limit_mm: float = 300.0, step_mm: float = 10.0) -> float:
        """ How far the robot can drive forward before its front nears a known edge. """
        c, s = math.cos(heading), math.sin(heading)
        d = 0.0
        while d < limit_mm:
            nd = d + step_mm
            fx, fy = x + (nd + FRONT_MM) * c, y + (nd + FRONT_MM) * s
            if self.edge_distance(fx, fy, origin, now) < TURN_MARGIN_MM + 10.0:
                break
            d = nd
        return d


class CliffRetreat:
    """
    The reflex when the cliff sensor sees a drop: back off, then turn away.

    ``first()`` gives the first commands and ``step()`` the next ones, at each robot state, until it returns a
    ('done', outcome) command: 'safe' (backed off and turned), 'backed' (backed off, no room to turn) or 'stuck'
    (still over the drop). The caller sends the wheel commands and plays the reaction.
    """

    BACKUP_MMPS = 40.0
    #: The drop is 20 mm ahead and this is 60 mm more: room for the rear corners to turn.
    BACKUP_MM = 60.0
    #: Even without known floor: just enough to lift the sensor off the drop.
    MIN_BACKUP_MM = 15.0
    BACKUP_TIMEOUT_S = 4.0
    TURN_MMPS = 25.0
    TURN_MIN_DEG, TURN_MAX_DEG = 110.0, 160.0
    TURN_TIMEOUT_S = 6.0

    def __init__(self, x: float, y: float, heading: float, now: float, room_mm: float,
                 rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.start = (x, y)
        self.started = now
        self.goal_mm = min(self.BACKUP_MM, max(room_mm, self.MIN_BACKUP_MM))
        self.phase = "backup"
        self.target = heading
        self.sign = 1.0

    def first(self) -> list[Command]:
        return [("stop",), ("wheels", -self.BACKUP_MMPS, -self.BACKUP_MMPS)]

    def step(self, x: float, y: float, heading: float, cliff: bool, now: float, can_turn: bool) -> list[Command]:
        """ The commands for the robot at this pose; ``cliff`` is the cliff flag, ``can_turn`` TableMemory.can_turn. """
        if self.phase == "done":
            return []
        if self.phase == "backup":
            travelled = math.hypot(x - self.start[0], y - self.start[1])
            if travelled < self.goal_mm and now - self.started < self.BACKUP_TIMEOUT_S:
                return [("wheels", -self.BACKUP_MMPS, -self.BACKUP_MMPS)]
            if cliff:
                self.phase = "done"
                return [("stop",), ("done", "stuck")]
            if not can_turn:
                self.phase = "done"
                return [("stop",), ("react", "cliff"), ("done", "backed")]
            self.sign = self.rng.choice((-1.0, 1.0))
            turn = self.rng.uniform(self.TURN_MIN_DEG, self.TURN_MAX_DEG)
            self.target = _angle(heading + self.sign * math.radians(turn))
            self.phase, self.started = "turn", now
            return [("stop",), ("react", "cliff"), ("wheels", -self.TURN_MMPS * self.sign, self.TURN_MMPS * self.sign)]
        # Turning.
        error = _angle(self.target - heading) * self.sign
        if cliff or error <= math.radians(4) or now - self.started > self.TURN_TIMEOUT_S:
            self.phase = "done"
            return [("stop",), ("done", "stuck" if cliff else "safe")]
        return [("wheels", -self.TURN_MMPS * self.sign, self.TURN_MMPS * self.sign)]
