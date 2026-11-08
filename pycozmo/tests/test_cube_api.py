from concurrent.futures import ThreadPoolExecutor
import threading
import time
from unittest.mock import patch

import pytest

import pycozmo
from pycozmo import lights, protocol_encoder


def test_set_cube_lights_sends_selection_before_four_lights():
    client = pycozmo.Client()
    with patch.object(client.conn, 'send') as send:
        client.set_cube_lights(7, (lights.blue_light,) * 4, rotation_period_frames=10)
    selection, command = [call.args[0] for call in send.call_args_list]
    assert isinstance(selection, protocol_encoder.CubeId)
    assert selection.object_id == 7 and selection.rotation_period_frames == 10
    assert isinstance(command, protocol_encoder.CubeLights)
    assert list(command.states) == [lights.blue_light] * 4


def test_invalid_cube_lights_do_not_send_a_selection():
    client = pycozmo.Client()
    with patch.object(client.conn, 'send') as send:
        with pytest.raises((ValueError, TypeError)):
            client.set_cube_lights(7, (lights.blue_light,))
    send.assert_not_called()


def test_concurrent_cube_lights_do_not_interleave():
    client = pycozmo.Client()
    sent = []
    start = threading.Barrier(2)

    def capture(packet):
        sent.append(packet)
        time.sleep(.005)

    def change(object_id):
        start.wait(timeout=2)
        client.set_cube_lights(object_id, (lights.blue_light,) * 4)

    with patch.object(client.conn, 'send', side_effect=capture):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(change, [1, 2]))
    assert [type(p) for p in sent] == [protocol_encoder.CubeId, protocol_encoder.CubeLights] * 2
    assert {sent[0].object_id, sent[2].object_id} == {1, 2}


def test_set_object_accel_stream_sends_requested_state():
    client = pycozmo.Client()
    with patch.object(client.conn, 'send') as send:
        client.set_object_accel_stream(7, enable=False)
    packet = send.call_args.args[0]
    assert isinstance(packet, protocol_encoder.StreamObjectAccel)
    assert packet.object_id == 7 and packet.enable is False
