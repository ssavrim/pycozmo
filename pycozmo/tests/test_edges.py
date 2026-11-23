
import math
import random
import unittest

from pycozmo.edges import CliffRetreat, TableMemory, FRONT_MM, SENSOR_MM, SWEEP_MM, TURN_MARGIN_MM
from pycozmo.robot import RobotStatusFlag


CLIFF = int(RobotStatusFlag.CLIFF_DETECTED)
PICKED_UP = int(RobotStatusFlag.IS_PICKED_UP)
FALLING = int(RobotStatusFlag.IS_FALLING)
ON_CHARGER = int(RobotStatusFlag.IS_ON_CHARGER)


class TestTableMemory(unittest.TestCase):

    def test_backing_up_only_onto_floor_already_stood_on(self):
        t = TableMemory()
        t.note_pose(0, 0, 0, 1)
        self.assertLessEqual(t.backup_room(0, 0, 0, 1), 5.0)        # just put down: tolerance only
        for x in range(-100, 1, 10):
            t.note_pose(x, 0, 0, 1)
        self.assertAlmostEqual(t.backup_room(0, 0, 0, 1), 100.0)
        self.assertLessEqual(t.backup_room(0, 0, math.pi, 1), 5.0)   # turned around: nothing known that way
        self.assertEqual(t.backup_room(0, 0, 0, 2), 0.0)            # another origin: nothing known

    def test_a_new_origin_forgets_floor_and_edges(self):
        t = TableMemory()
        t.note_pose(0, 0, 0, 1)
        t.note_edge(0, 0, 0, 1, 0.0)
        t.note_pose(500, 500, 0, 2)
        self.assertEqual(len(t.poses), 1)
        self.assertEqual(t.edges_known(2, 1.0), [])

    def test_turning_needs_the_body_corners_clear_of_known_edges(self):
        t = TableMemory()
        t.note_edge(0, 0, 0, 1, 0.0)                               # drop 20 mm ahead of x = 0
        self.assertFalse(t.can_turn(0, 0, 1, 1.0))
        self.assertFalse(t.can_turn(-40, 50, 1, 1.0))              # beside, still along the same edge
        self.assertTrue(t.can_turn(-60, 0, 1, 1.0))                # backed off 6 cm
        self.assertTrue(t.can_turn(0, 0, 2, 1.0))                  # other origin
        self.assertTrue(t.can_turn(0, 0, 1, 1.0 + TableMemory.EDGE_TTL_S + 1))
        self.assertGreaterEqual(SENSOR_MM + CliffRetreat.BACKUP_MM, SWEEP_MM + TURN_MARGIN_MM)

    def test_nearby_detections_merge_and_free_distance_ahead_stops_before_an_edge(self):
        t = TableMemory()
        t.note_edge(0, 0, 0, 1, 0.0)
        t.note_edge(5, 0, 0, 1, 1.0)
        self.assertEqual(len(t.edges), 1)
        ahead = t.clear_ahead(-300, 0, 0, 1, 2.0)
        self.assertLess(ahead, 300 - FRONT_MM + SENSOR_MM)
        self.assertGreater(ahead, 150)
        self.assertEqual(t.clear_ahead(-300, 0, math.pi, 1, 2.0), 300)   # driving away

    def test_no_edge_is_infinitely_far(self):
        t = TableMemory()
        self.assertEqual(t.edge_distance(0, 0, 1, 0.0), math.inf)
        self.assertTrue(t.can_turn(0, 0, 1, 0.0))

    def test_poses_are_recorded_when_the_robot_moved_or_turned(self):
        t = TableMemory()
        t.note_pose(0, 0, 0, 1)
        t.note_pose(3, 0, 0, 1)                                    # less than 10 mm
        t.note_pose(0, 0, math.radians(5), 1)                      # less than 10 degrees
        self.assertEqual(len(t.poses), 1)
        t.note_pose(11, 0, 0, 1)
        t.note_pose(11, 0, math.radians(12), 1)
        self.assertEqual(len(t.poses), 3)

    def test_the_memories_are_bounded(self):
        t = TableMemory()
        for i in range(TableMemory.MAX_POSES + 50):
            t.note_pose(i * 20.0, 0, 0, 1)
        for i in range(TableMemory.MAX_EDGES + 5):
            t.note_edge(i * 100.0, 0, 0, 1, 0.0)
        self.assertEqual(len(t.poses), TableMemory.MAX_POSES)
        self.assertEqual(len(t.edges), TableMemory.MAX_EDGES)


class TestObserve(unittest.TestCase):

    def test_a_cliff_flag_records_an_edge_and_the_pose(self):
        t = TableMemory()
        t.observe(0, 0, 0, 1, 0, 0.0)
        t.observe(30, 0, 0, 1, CLIFF, 1.0)
        self.assertEqual(len(t.edges), 1)
        self.assertAlmostEqual(t.edges[0][0], 30 + SENSOR_MM)
        self.assertEqual(len(t.poses), 2)

    def test_a_lift_drops_the_edges_seen_just_before_and_mutes_the_sensor(self):
        t = TableMemory()
        t.observe(0, 0, 0, 1, 0, 0.0)
        t.observe(0, 0, 0, 1, CLIFF, 20.0)                         # the drop that comes before the lift is reported
        t.observe(0, 0, 0, 1, CLIFF | PICKED_UP, 21.0)
        self.assertEqual(t.edges_known(1, 21.0), [])               # seen within the lead of 2.5 s: forgotten
        t.observe(0, 0, 0, 1, CLIFF, 21.5)                         # put down: muted
        self.assertEqual(t.edges_known(1, 21.5), [])
        t.observe(0, 0, 0, 1, CLIFF, 21.0 + TableMemory.PUT_DOWN_GRACE_S + 0.1)
        self.assertEqual(len(t.edges_known(1, 30.0)), 1)

    def test_only_the_recent_edges_are_dropped_by_a_lift(self):
        t = TableMemory()
        t.observe(0, 0, 0, 1, 0, 0.0)
        t.note_edge(500, 0, 0, 1, 10.0)
        t.note_edge(0, 500, 0, 1, 19.5)
        t.observe(0, 0, 0, 1, PICKED_UP, 20.0)
        self.assertEqual([e[3] for e in t.edges_known(1, 20.0)], [10.0])

    def test_no_pose_while_picked_up_or_falling(self):
        t = TableMemory()
        t.observe(0, 0, 0, 1, 0, 0.0)
        t.observe(500, 0, 0, 1, PICKED_UP, 1.0)
        t.observe(900, 0, 0, 1, FALLING, 2.0)
        self.assertEqual(len(t.poses), 1)

    def test_the_charger_edge_is_not_a_table_edge(self):
        t = TableMemory()
        t.observe(0, 0, 0, 1, CLIFF | ON_CHARGER, 0.0)
        self.assertEqual(len(t.edges), 0)


class TestCliffRetreat(unittest.TestCase):

    def test_backs_off_then_turns_away(self):
        r = CliffRetreat(0, 0, 0, 0.0, room_mm=100, rng=random.Random(1))
        self.assertEqual(r.first(), [("stop",), ("wheels", -40.0, -40.0)])
        self.assertEqual(r.step(-30, 0, 0, False, 1.0, True), [("wheels", -40.0, -40.0)])
        out = r.step(-60, 0, 0, False, 2.4, True)
        self.assertEqual(out[:2], [("stop",), ("react", "cliff")])
        self.assertEqual(out[2][0], "wheels")
        self.assertEqual(out[2][1], -out[2][2])                      # in place
        target = r.target
        self.assertEqual(r.step(-60, 0, target, False, 4.0, True), [("stop",), ("done", "safe")])

    def test_backing_is_limited_to_known_floor_and_no_turn_without_room(self):
        r = CliffRetreat(0, 0, 0, 0.0, room_mm=0)
        self.assertEqual(r.goal_mm, CliffRetreat.MIN_BACKUP_MM)
        self.assertEqual(r.step(-16, 0, 0, False, 1.0, False),
                         [("stop",), ("react", "cliff"), ("done", "backed")])

    def test_still_over_the_drop_gives_up(self):
        r = CliffRetreat(0, 0, 0, 0.0, room_mm=100)
        self.assertEqual(r.step(0, 0, 0, True, 4.5, True), [("stop",), ("done", "stuck")])
        self.assertEqual(r.step(0, 0, 0, True, 5.0, True), [])

    def test_the_turn_ends_on_a_cliff_or_a_timeout(self):
        r = CliffRetreat(0, 0, 0, 0.0, room_mm=100, rng=random.Random(2))
        r.step(-60, 0, 0, False, 2.0, True)
        self.assertEqual(r.step(-60, 0, 0, True, 3.0, True), [("stop",), ("done", "stuck")])
        r = CliffRetreat(0, 0, 0, 0.0, room_mm=100, rng=random.Random(2))
        r.step(-60, 0, 0, False, 2.0, True)
        self.assertEqual(r.step(-60, 0, 0, False, 2.0 + CliffRetreat.TURN_TIMEOUT_S + 0.1, True),
                         [("stop",), ("done", "safe")])
        self.assertEqual(r.step(-60, 0, 0, False, 9.0, True), [])


if __name__ == "__main__":
    unittest.main()
