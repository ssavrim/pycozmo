import unittest
from dataclasses import replace
from pycozmo.companion import CompanionBrain, Observation


def obs(t=0, **kw):
    value = Observation(now=t, link_ok=True, camera_ok=True, unsafe=False, on_charger=False,
                        low_battery=False, face=False, poked=False, cube_tapped=False, motion=False,
                        searching=False, search_reason=None)
    return replace(value, **kw)


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = CompanionBrain()
        self.brain.start(0)

    def test_safety_preempts_everything(self):
        self.assertEqual(self.brain.step(obs(1, unsafe=True, face=True, searching=True)), 'halt')
        self.assertFalse(self.brain.enabled)

    def test_charge_does_not_search(self):
        self.assertIsNone(self.brain.step(obs(50, on_charger=True)))
        self.assertEqual(self.brain.state, 'observing')

    def test_low_battery_stops_search(self):
        self.assertEqual(self.brain.step(obs(5, low_battery=True, searching=True)), 'stop_search')
        self.assertEqual(self.brain.state, 'resting')

    def test_greeting_cooldown(self):
        self.assertEqual(self.brain.step(obs(1, face=True)), 'greet')
        self.assertIsNone(self.brain.step(obs(2, face=True)))
        self.assertEqual(self.brain.step(obs(46, face=True)), 'greet')

    def test_search_after_absence_with_cooldown(self):
        self.assertIsNone(self.brain.step(obs(11)))
        self.assertEqual(self.brain.step(obs(12)), 'search')
        self.assertIsNone(self.brain.step(obs(13, searching=True)))
        self.assertIsNone(self.brain.step(obs(14)))
        self.assertIsNone(self.brain.step(obs(33)))
        self.assertEqual(self.brain.step(obs(34)), 'search')

    def test_touch_debounced(self):
        self.assertEqual(self.brain.step(obs(1, poked=True)), 'react')
        self.assertIsNone(self.brain.step(obs(2, poked=True)))

    def test_motion_reacts_only_with_cooldown(self):
        self.assertEqual(self.brain.step(obs(1, motion=True)), 'react')
        self.assertIsNone(self.brain.step(obs(2, motion=True)))

    def test_cube_reaction_debounced(self):
        self.assertEqual(self.brain.step(obs(1, cube_tapped=True)), 'react')
        self.assertIsNone(self.brain.step(obs(2, cube_tapped=True)))


if __name__ == '__main__':
    unittest.main()
