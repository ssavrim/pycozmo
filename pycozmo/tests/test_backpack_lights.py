
from unittest.mock import patch

import pycozmo
from pycozmo import lights, protocol_encoder


def light(r, g, b):
    value = lights.Color(rgb=(r, g, b)).to_int16()
    return protocol_encoder.LightState(on_color=value, off_color=value)


def test_white_shades():
    assert lights.is_white_shade(lights.white.to_int16())
    assert lights.is_white_shade(lights.Color(rgb=(100, 100, 100)).to_int16())
    assert lights.is_white_shade(lights.Color(rgb=(255, 240, 255)).to_int16())      # within two steps
    assert not lights.is_white_shade(lights.off.to_int16())
    assert not lights.is_white_shade(lights.red.to_int16())
    assert not lights.is_white_shade(lights.green.to_int16())
    assert not lights.is_white_shade(lights.Color(rgb=(255, 128, 0)).to_int16())    # orange
    assert not lights.is_white_shade(lights.Color(rgb=(255, 255, 0)).to_int16())    # yellow


def test_side_light():
    assert lights.side_light(lights.white_light) is lights.off_light
    assert lights.side_light(light(100, 100, 100)) is lights.off_light
    for colored in (lights.red_light, lights.green_light, lights.blue_light, light(255, 128, 0), lights.off_light):
        assert lights.side_light(colored) is colored
    blinking = protocol_encoder.LightState(on_color=lights.white.to_int16(), off_color=0, on_frames=3, off_frames=3)
    assert lights.side_light(blinking) is lights.off_light


def sent_lights(call_list):
    center = [c.args[0] for c in call_list if isinstance(c.args[0], protocol_encoder.LightStateCenter)][0]
    side = [c.args[0] for c in call_list if isinstance(c.args[0], protocol_encoder.LightStateSide)][0]
    return side.states, center.states


def test_set_all_backpack_lights_turns_the_sides_off_for_white():
    client = pycozmo.Client()
    with patch.object(client.conn, 'send') as send:
        client.set_all_backpack_lights(lights.white_light)
    side, center = sent_lights(send.call_args_list)
    assert list(center) == [lights.white_light] * 3
    assert list(side) == [lights.off_light] * 2


def test_set_all_backpack_lights_keeps_the_sides_for_colors():
    client = pycozmo.Client()
    with patch.object(client.conn, 'send') as send:
        client.set_all_backpack_lights(lights.red_light)
    side, center = sent_lights(send.call_args_list)
    assert list(center) == [lights.red_light] * 3
    assert list(side) == [lights.red_light] * 2
