# Table edges

How the robot can remember where the table ends: the drops that its cliff sensor saw and the floor that it stood on, and
how to use them to check the moves that it makes blind. Implemented by `pycozmo.edges`, ported from Cozmonaute
(ssavrim/cozmonaute), where it was validated on a robot. It only computes: the caller feeds it the poses and the
status flags of the robot, and sends the wheel commands that it returns.

Confidence: **Specified** (precise rule, tested), **Assumed** (reasonable choice, measured on one robot), **Not done**.

## The sensor

The cliff sensor looks down just ahead of the robot: it reports a drop only when the front is already over it. Nothing
watches the sides or the back. `RobotStatusFlag.CLIFF_DETECTED` is the flag; the firmware does not stop the robot on its
own by default, so a caller that drives near an edge has to stop it (the MCP server and `CliffRetreat` do).

## The memory

`TableMemory` keeps two memories, in the frame of the pose origin of the robot (`Pose.origin_id`); a pick up starts a new
origin and both memories are forgotten (Specified).

- **Edges** (at most 24, forgotten after 30 minutes without being seen again). A drop seen with the robot at a pose is put
  `SENSOR_MM` = 20 mm ahead of it and taken as a straight segment across the heading, 80 mm on each side. Detections
  within 15 mm of an edge are the same edge.
- **Floor** (at most 600 poses). The footprint of the robot at each recorded pose: from 55.9 mm behind the pose origin to
  22.1 mm ahead, 27.1 mm each side, with a tolerance of 5 mm. A pose is recorded when the robot moved 10 mm or turned 10
  degrees since the last one.

`observe(x, y, heading, origin, flags, now)` feeds both memories at each robot state (Specified):

- Not picked up and not falling: the pose is recorded.
- Picked up or falling: the edges seen in the 2.5 seconds before are dropped (the sensor sees a drop before the lift is
  reported), and the sensor is not trusted for 1.5 seconds after (it sees a drop again while the robot is put down).
- Otherwise, the cliff flag records an edge, except on the charger (the edge of its platform looks like a drop while the
  robot climbs on or off).

## The checks

- `backup_room(x, y, heading, origin, limit_mm)`: how far the robot can back up with its rear, center and corners, on
  floor that it has stood on (0 when nothing is known, so a robot just put down cannot back up before it has moved).
- `can_turn(x, y, origin, now)`: whether the body can turn on the spot without swinging over a known edge, that is whether
  the nearest known edge is farther than the corner of the body that is the farthest from the center of the turn
  (`SWEEP_MM` = 60.4 mm) plus 15 mm.
- `clear_ahead(x, y, heading, origin, now)`: how far the robot can drive forward before its front is within 25 mm of a known
  edge, up to 300 mm.
- `edge_distance`, `on_floor`, `edges_known`: the pieces of the checks above.

## The reflex

`CliffRetreat(x, y, heading, now, room_mm)` is for a robot whose sensor sees a drop. `first()` and then `step(x, y,
heading, cliff, now, can_turn)` at each robot state return commands, until `('done', outcome)` (Specified):

1. **Back off** at 40 mm/s by the room that the floor memory gives, at least 15 mm (just enough to lift the sensor off the
   drop) and at most 60 mm (the drop is 20 mm ahead, and the rear corners need room to turn), for at most 4 seconds.
2. If the sensor still sees a drop: `('done', 'stuck')`. If the body cannot turn: `('react', 'cliff')` then `('done',
   'backed')`.
3. Otherwise **turn away** on the spot at 25 mm/s on each wheel, to the left or to the right (random), by 110 to 160
   degrees, for at most 6 seconds, then `('done', 'safe')`; `('done', 'stuck')` if the sensor sees a drop during the turn.

The commands are `('wheels', left, right)` in mm/s, `('stop',)`, `('react', situation)` (the caller plays a reaction) and
`('done', outcome)`. The caller sends them to the robot; nothing here talks to it.

## Open points

| Point | Status |
|---|---|
| Rules of the memories, the checks and the reflex | Specified (tested offline, 16 tests) |
| Footprint, sensor position, speeds, margins and times | Assumed: measured on one robot (firmware 2381) in Cozmonaute, not derived from the original |
| Edges seen by the sides or the back | Not done: only the front sensor exists |
| Use in the MCP server (`drive`, `turn`) | Not done in this change |
| On a robot (`--tests edges`, 2026-11-23, firmware 2381) | Validated once, see below |

## Validation on a robot

`tools/pycozmo_validate.py --tests edges`, on a table, with a hand under the robot: the robot was put about 15 cm from the
edge, facing it, and drove at 15 mm/s, stopping at the first cliff flag. The drop was seen after 157 mm, one edge was noted
and 16 poses recorded; at the drop the memory gave a backup room of 100 mm and `can_turn` was false (the edge was too close
to turn). `CliffRetreat` then ended with `safe`: the operator saw the robot back off and turn away, with its wheels still on
the table at the drop and without any touch. This is one run on one table: the margins were not measured on other surfaces
or edges.
