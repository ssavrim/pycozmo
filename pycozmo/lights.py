"""

Helper routines for working with colors and lights.

"""

import numpy as np

from . import protocol_encoder


__all__ = [
    'Color',
    'FRAME_MS',
    'SOLID_PERIOD',
    'ms_to_frames',
    'offset_to_frames',
    'white_balance',
    'encode_color',
    'make_light_state',

    'green',
    'red',
    'blue',
    'white',
    'off',

    'green_light',
    'red_light',
    'blue_light',
    'white_light',
    'off_light',

    'is_white_shade',
    'side_light',
]


LED_ENC_BLUE = 0x001f
LED_ENC_GREEN = 0x03e0
LED_ENC_RED = 0x7c00
LED_ENC_IR = 0x8000

LED_ENC_BLUE_SHIFT = 0
LED_ENC_GREEN_SHIFT = 5
LED_ENC_RED_SHIFT = 10
LED_ENC_IR_SHIFT = 15

#: Duration of a frame of the cube, in milliseconds.
FRAME_MS = 30
#: Period of the LEDs that never change.
SOLID_PERIOD = 0x7fffffff

RGBA = tuple[int, int, int, int]


def ms_to_frames(ms: int) -> int:
    """ Milliseconds to frames of the cube: rounded up, on 32 bits then kept on 8 bits; -1 gives 255. """
    if ms == -1:
        return 0xff
    return (((ms + FRAME_MS - 1) & 0xffffffff) // FRAME_MS) & 0xff


def offset_to_frames(ms: int) -> int:
    """ Same as ms_to_frames for the offset of a LED: signed, kept on 16 bits. """
    if ms == -1:
        return 0xff
    value = ms + FRAME_MS - 1
    frames = abs(value) // FRAME_MS * (1 if value >= 0 else -1)
    frames &= 0xffff
    return frames - 0x10000 if frames >= 0x8000 else frames


def white_balance(color: RGBA) -> RGBA:
    """ Green and blue are scaled down to 0.6 when the red value is not 0. """
    r, g, b, a = color
    if r > 0:
        inv = np.float32(0.003921569)
        scale = np.float32(0.6)
        full = np.float32(255.0)
        g = int(np.float32(np.float32(np.float32(g) * inv) * scale) * full)
        b = int(np.float32(np.float32(np.float32(b) * inv) * scale) * full)
    return r, g, b, a


def encode_color(color: RGBA, cube: bool = False) -> int:
    """
    16-bit color of a LED, 5 bits per channel.

    By default the channels are scaled from 0-255 to 0-31 and the alpha is ignored. With ``cube``, the color is
    encoded as the light patterns of the original application do: white balance, channels divided by 8, and bit 15
    set when the alpha is not 0.
    """
    if not cube:
        r, g, b, _ = color
        return ((r * 31 // 255) << LED_ENC_RED_SHIFT) | ((g * 31 // 255) << LED_ENC_GREEN_SHIFT) | (
            (b * 31 // 255) << LED_ENC_BLUE_SHIFT)
    r, g, b, a = white_balance(color)
    value = ((r >> 3) << LED_ENC_RED_SHIFT) | ((g >> 3) << LED_ENC_GREEN_SHIFT) | (b >> 3)
    if a != 0:
        value |= LED_ENC_IR
    return value


def make_light_state(
        on_color: RGBA, off_color: RGBA, on_period_ms: int, off_period_ms: int,
        transition_on_ms: int = 0, transition_off_ms: int = 0, offset_ms: int = 0) -> protocol_encoder.LightState:
    """
    A LightState of a cube LED from colors and durations in milliseconds.

    A LED whose on period is 0 is solid in its off color (black when the off period is also 0), and a LED whose off
    period is 0 is solid in its on color; a solid LED gets both colors equal and the period SOLID_PERIOD.
    """
    if on_period_ms == 0:
        if off_period_ms == 0:
            on_color = off_color = (0, 0, 0, 0)
            off_period_ms = SOLID_PERIOD
        else:
            on_color = off_color
        on_period_ms = SOLID_PERIOD
    elif off_period_ms == 0:
        off_color = on_color
        off_period_ms = SOLID_PERIOD
    return protocol_encoder.LightState(
        on_color=encode_color(on_color, cube=True),
        off_color=encode_color(off_color, cube=True),
        on_frames=ms_to_frames(on_period_ms),
        off_frames=ms_to_frames(off_period_ms),
        transition_on_frames=ms_to_frames(transition_on_ms),
        transition_off_frames=ms_to_frames(transition_off_ms),
        offset=offset_to_frames(offset_ms))


class Color:
    """
    A Color to be used with a Light.

    Either int_color or rgb may be used to specify the actual color.
    Any alpha components (from int_color) are ignored - all colors are fully opaque.

    Args:
        int_color (int): A 32 bit value holding the binary RGBA value.
        rgb (tuple): A tuple holding the integer values from 0-255 for (red, green, blue)
        name (str): A name to assign to this color
    """

    def __init__(self,
                 int_color: int | None = None,
                 rgb: tuple[int, int, int] | None = None,
                 name: str | None = None) -> None:
        self.name = name
        if int_color is not None:
            self._int_color = int(int_color) | 0xff
        elif rgb is not None:
            self._int_color = (int(rgb[0]) << 24) | (int(rgb[1]) << 16) | (int(rgb[2]) << 8) | 0xff
        else:
            self._int_color = 0

    @property
    def int_color(self) -> int:
        return self._int_color

    def to_int16(self, cube: bool = False) -> int:
        """ 16-bit color; ``cube`` selects the encoding of the cube light patterns (see encode_color). """
        c = self._int_color
        return encode_color(((c >> 24) & 0xff, (c >> 16) & 0xff, (c >> 8) & 0xff, c & 0xff), cube=cube)

    @classmethod
    def from_int16(cls, value: int) -> "Color":
        r = (value & LED_ENC_RED) >> LED_ENC_RED_SHIFT
        g = (value & LED_ENC_GREEN) >> LED_ENC_GREEN_SHIFT
        b = (value & LED_ENC_BLUE) >> LED_ENC_BLUE_SHIFT
        rgb = (
            r * 255 // 31,
            g * 255 // 31,
            b * 255 // 31
        )
        obj = cls(rgb=rgb)
        return obj

    def __repr__(self):
        return "Color(name={}, int_color=0x{:08x})".format(self.name, self._int_color)


#: Green color.
green = Color(name="green", int_color=0x00ff00ff)
#: Red color.
red = Color(name="red", int_color=0xff0000ff)
#: BLue color.
blue = Color(name="blue", int_color=0x0000ffff)
#: White color.
white = Color(name="white", int_color=0xffffffff)       # Does not work well with cubes?
#: Off/no color.
off = Color(name="off")

#: Green light.
green_light = protocol_encoder.LightState(on_color=green.to_int16(), off_color=green.to_int16())
#: Red light.
red_light = protocol_encoder.LightState(on_color=red.to_int16(), off_color=red.to_int16())
#: Blue light.
blue_light = protocol_encoder.LightState(on_color=blue.to_int16(), off_color=blue.to_int16())
#: White light.
white_light = protocol_encoder.LightState(on_color=white.to_int16(), off_color=white.to_int16())
#: Off/no light.
off_light = protocol_encoder.LightState(on_color=off.to_int16(), off_color=off.to_int16())


def is_white_shade(color: int) -> bool:
    """ Whether a 16-bit LED color is white or grey: the three channels are equal, give or take two steps. """
    r = (color & LED_ENC_RED) >> LED_ENC_RED_SHIFT
    g = (color & LED_ENC_GREEN) >> LED_ENC_GREEN_SHIFT
    b = (color & LED_ENC_BLUE) >> LED_ENC_BLUE_SHIFT
    return max(r, g, b) > 0 and max(r, g, b) - min(r, g, b) <= 2


def side_light(light: protocol_encoder.LightState) -> protocol_encoder.LightState:
    """
    The light for the left and right LEDs of the backpack, which are red only: off when the light is a shade of
    white (they would show red next to a white line), the light itself otherwise.
    """
    if is_white_shade(light.on_color) or is_white_shade(light.off_color):
        return off_light
    return light
