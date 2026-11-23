
import unittest

import numpy as np

from pycozmo import lights
from pycozmo.lights import Color, encode_color, make_light_state, ms_to_frames, offset_to_frames, white_balance


class TestCubeEncoding(unittest.TestCase):

    def test_frames(self):
        self.assertEqual([ms_to_frames(x) for x in (0, 1, 30, 31, 100, 5000)], [0, 1, 1, 2, 4, 167])
        self.assertEqual(ms_to_frames(-1), 255)
        self.assertEqual(ms_to_frames(lights.SOLID_PERIOD), 69)       # 71582789, kept on 8 bits
        self.assertEqual([offset_to_frames(x) for x in (0, 1, 30, 31, -30, -1)], [0, 1, 1, 2, 0, 255])

    def test_white_balance(self):
        self.assertEqual(white_balance((0, 255, 255, 255)), (0, 255, 255, 255))      # no red: unchanged
        r, g, b, a = white_balance((255, 255, 255, 255))
        scaled = int(np.float32(np.float32(np.float32(255) * np.float32(0.003921569)) * np.float32(0.6))
                     * np.float32(255.0))
        self.assertEqual((r, g, b, a), (255, scaled, scaled, 255))
        self.assertIn(scaled, (152, 153))
        self.assertEqual(white_balance((10, 0, 0, 255)), (10, 0, 0, 255))

    def test_color(self):
        self.assertEqual(encode_color((255, 0, 0, 0)), 31 << 10)
        self.assertEqual(encode_color((0, 255, 0, 0)), 31 << 5)
        self.assertEqual(encode_color((0, 0, 255, 0)), 31)
        self.assertEqual(encode_color((0, 0, 0, 255)), 0x8000)
        self.assertEqual(encode_color((8, 0, 0, 0)), 1 << 10)       # divided by 8, not scaled
        self.assertEqual(encode_color((7, 0, 0, 0)), 0)

    def test_color_to_cube_int16(self):
        self.assertEqual(Color(rgb=(255, 0, 0)).to_cube_int16(), (31 << 10) | 0x8000)
        self.assertEqual(Color(rgb=(255, 0, 0)).to_int16(), 31 << 10)       # unchanged
        self.assertEqual(lights.off.to_cube_int16(), 0)

    def test_make_light_state(self):
        red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
        solid = ms_to_frames(lights.SOLID_PERIOD)
        state = make_light_state(red, blue, 100, 200, 30, 60, 31)
        self.assertEqual((state.on_frames, state.off_frames, state.transition_on_frames,
                          state.transition_off_frames, state.offset), (4, 7, 1, 2, 2))
        self.assertEqual((state.on_color, state.off_color), (encode_color(red), encode_color(blue)))
        black = make_light_state(red, blue, 0, 0)
        self.assertEqual((black.on_color, black.off_color, black.on_frames, black.off_frames), (0, 0, solid, solid))
        off_solid = make_light_state(red, blue, 0, 50)
        self.assertEqual((off_solid.on_color, off_solid.off_color, off_solid.on_frames, off_solid.off_frames),
                         (encode_color(blue), encode_color(blue), solid, 2))
        on_solid = make_light_state(red, blue, 100, 0)
        self.assertEqual((on_solid.on_color, on_solid.off_color, on_solid.on_frames, on_solid.off_frames),
                         (encode_color(red), encode_color(red), 4, solid))
