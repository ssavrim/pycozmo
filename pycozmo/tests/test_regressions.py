import struct
import subprocess
import threading
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

import pycozmo
from pycozmo import audio, procedural_face, protocol_encoder


def test_pcm_encoding_is_a_byte_for_all_16_bit_samples():
    for start in range(-32768, 32768, 744):
        samples = list(range(start, min(start + 744, 32768)))
        encoded = audio.bytes_to_cozmo(struct.pack(f'<{len(samples)}h', *samples), 1, 1)
        assert len(encoded) == 744
        assert list(encoded[:len(samples)]) == [audio.u_law_encoding(s) for s in samples]
    # Silence is code 0 and positive/negative magnitudes are symmetric
    # (no bias, no +1).
    assert audio.u_law_encoding(-32768) == 0xFF
    assert audio.u_law_encoding(32767) == 0x7F
    assert audio.u_law_encoding(0) == 0x00
    assert audio.u_law_encoding(-1) == 0x80
    assert audio.u_law_encoding(1000) == 0x2F
    assert audio.u_law_encoding(-10000) == 0xE3
    assert audio.u_law_encoding(10000) == 0x63


def test_animation_controller_keeps_a_lead_on_the_robot():
    from types import SimpleNamespace
    from pycozmo import anim_controller
    ctrl = anim_controller.AnimationController(SimpleNamespace())
    assert ctrl.frame_sleeps() == 1                 # no report yet: real time
    ctrl.frames_sent = 3
    ctrl._on_animation_state(None, SimpleNamespace(num_audio_frames_played=500))
    assert ctrl.lead() == 0 and ctrl.frame_sleeps() == 0   # fill the lead at once
    ctrl.frames_sent = 3 + ctrl.LEAD_FRAMES
    assert ctrl.frame_sleeps() == 1
    ctrl.frames_sent += ctrl.LEAD_SLACK + 1
    assert ctrl.frame_sleeps() == 2                 # drain an excess
    ctrl._on_animation_state(None, SimpleNamespace(num_audio_frames_played=500 + ctrl.LEAD_SLACK + 1))
    assert ctrl.lead() == ctrl.LEAD_FRAMES


@pytest.mark.parametrize('shape', ['rectangle', 'chord', 'pieslice'])
def test_inverted_face_shapes_are_empty(shape):
    im = Image.new('1', (12, 12))
    draw = ImageDraw.Draw(im)
    args = () if shape == 'rectangle' else (0, 180)
    procedural_face._draw_shape(draw, shape, ((2, 8), (8, 2)), *args, fill=1)
    procedural_face._draw_shape(draw, shape, ((8, 2), (2, 8)), *args, fill=1)
    assert im.getbbox() is None
    procedural_face._draw_shape(draw, shape, ((2, 2), (8, 8)), *args, fill=1)
    expected = Image.new('1', (12, 12))
    getattr(ImageDraw.Draw(expected), shape)(((2, 2), (8, 8)), *args, fill=1)
    assert im.tobytes() == expected.tobytes()


def test_wait_for_robot_requires_completed_initialization():
    client = pycozmo.Client()
    client.robot_fw_sig = {'version': 2381}
    client.serial_number = 123
    with pytest.raises(pycozmo.exception.ConnectionTimeout):
        client.wait_for_robot(timeout=0.01)
    with patch.object(client.conn, 'send'), patch.object(client.anim_controller, 'start'), \
            patch('pycozmo.client.time.sleep'):
        client._initialize_robot()
    client.wait_for_robot(timeout=0.01)


def test_wait_for_robot_supports_early_and_late_ready():
    client = pycozmo.Client()
    client._robot_ready.set()
    client.wait_for_robot(timeout=0)
    client._robot_ready.clear()
    timer = threading.Timer(0.02, client._robot_ready.set)
    timer.start()
    try:
        client.wait_for_robot(timeout=1)
    finally:
        timer.join()


def test_disconnect_clears_robot_readiness():
    client = pycozmo.Client()
    client._robot_found.set()
    client._robot_ready.set()
    with patch.object(client.conn, 'disconnect'):
        client.disconnect()
    with pytest.raises(pycozmo.exception.ConnectionTimeout):
        client.wait_for_robot(timeout=0)


def test_manual_initialization_waits_for_robot_found():
    client = pycozmo.Client(auto_initialize=False)
    with pytest.raises(pycozmo.exception.ConnectionTimeout):
        client.wait_for_robot(timeout=0)
    client._robot_found.set()
    client.wait_for_robot(timeout=0)


def test_late_cube_disconnect_does_not_remove_reused_slot():
    client = pycozmo.Client()
    typ = protocol_encoder.ObjectType.Block_LIGHTCUBE1
    client._on_object_connection_state(None, protocol_encoder.ObjectConnectionState(
        object_id=1, factory_id=1, object_type=typ, connected=True))
    client._on_object_connection_state(None, protocol_encoder.ObjectConnectionState(
        object_id=1, factory_id=2, object_type=typ, connected=True))
    client._on_object_connection_state(None, protocol_encoder.ObjectConnectionState(
        object_id=1, factory_id=1, object_type=typ, connected=False))
    assert client.connected_objects[1]['factory_id'] == 2
    client._on_object_connection_state(None, protocol_encoder.ObjectConnectionState(
        object_id=1, factory_id=2, object_type=typ, connected=False))
    assert client.connected_objects == {}
    client._on_object_connection_state(None, protocol_encoder.ObjectConnectionState(
        object_id=1, factory_id=3, object_type=typ, connected=True))
    client._on_object_connection_state(None, protocol_encoder.ObjectConnectionState(
        object_id=1, factory_id=0, object_type=typ, connected=False))
    assert client.connected_objects == {}


def test_original_animation_tracks_can_be_filtered():
    from pycozmo import anim, anim_encoder
    clip = anim_encoder.AnimClip('test', [
        anim_encoder.AnimHeadAngle(trigger_time_ms=10, angle_deg=5),
        anim_encoder.AnimLiftHeight(trigger_time_ms=10, height_mm=10),
    ])
    filtered = anim.PreprocessedClip.from_anim_clip(clip, allowed_types=(anim_encoder.AnimHeadAngle,))
    assert len(filtered.keyframes[10]) == 1
    assert isinstance(filtered.keyframes[10][0], protocol_encoder.AnimHead)
    both = anim.PreprocessedClip.from_anim_clip(clip)
    assert len(both.keyframes[10]) == 2


def test_custom_animation_directory_resets_cache():
    client = pycozmo.Client()
    client._ppclips['old'] = object()
    client.animation_groups = {'old': object()}
    with patch('pycozmo.client.anim_encoder.get_clip_metadata', return_value={'new': 1}) as loader:
        client.load_anims('/custom/animations')
    loader.assert_called_once_with('/custom/animations')
    assert client.anim_names == {'new'}
    assert client._ppclips == {}
    assert client.animation_groups == {}


def test_object_connection_request():
    client = pycozmo.Client()
    with patch.object(client.conn, 'send') as send:
        client.set_object_connection(42)
        packet = send.call_args.args[0]
        assert packet.factory_id == 42 and packet.connect


def test_decode_wem_converts_stereo_and_rate():
    import wave

    def fake_decoder(command, **kwargs):
        with wave.open(command[2], 'wb') as dest:
            dest.setnchannels(2)
            dest.setsampwidth(2)
            dest.setframerate(44100)
            dest.writeframes(struct.pack('<8h', -32768, -32768, 0, 0, 200, 200, 100, 100))
    with patch('pycozmo.audio.subprocess.run', side_effect=fake_decoder):
        packets = audio.load_wem('voice.wem')
    assert len(packets) == 1
    assert packets[0].samples[:2] == audio.bytes_to_cozmo(struct.pack('<2h', -32768, 200), 1, 1)[:2]


def test_decode_wem_reports_missing_decoder():
    with patch('pycozmo.audio.subprocess.run', side_effect=FileNotFoundError):
        with pytest.raises(ValueError, match='executable not found'):
            audio.load_wem('voice.wem')


def test_decode_wem_reports_decoder_timeout():
    with patch('pycozmo.audio.subprocess.run', side_effect=subprocess.TimeoutExpired('vgmstream', 20)):
        with pytest.raises(ValueError, match='decoding failed'):
            audio.load_wem('voice.wem')


def test_decode_wem_rejects_audio_over_duration_limit():
    import wave

    def fake_decoder(command, **_kwargs):
        with wave.open(command[2], 'wb') as dest:
            dest.setnchannels(1)
            dest.setsampwidth(2)
            dest.setframerate(22050)
            dest.writeframes(bytes(22050 * 2))

    with patch('pycozmo.audio.subprocess.run', side_effect=fake_decoder):
        with pytest.raises(ValueError, match='duration'):
            audio.load_wem('voice.wem', max_seconds=0.5)


def test_play_anim_ppclip_places_keyframes_on_consecutive_frames():
    # Consecutive 33 ms keyframes (an audio stream) must not get empty frames
    # in between; a 99 ms gap gets exactly two empty frames.
    from types import SimpleNamespace
    from pycozmo import anim, client, protocol_encoder
    frames = []
    ctrl = SimpleNamespace(play_anim_frame=lambda a, i, p: frames.append(a), cancel_anim=lambda: None)
    cli = SimpleNamespace(anim_controller=ctrl, _next_anim_id=0, cancel_anim=lambda: None)
    audio = [protocol_encoder.OutputAudio(samples=bytes([n]) * 744) for n in range(4)]
    clip = anim.PreprocessedClip({0: [audio[0]], 33: [audio[1]], 66: [audio[2]], 165: [audio[3]]})
    client.Client.play_anim_ppclip(cli, clip)
    body = frames[1:-1]                      # StartAnimation / EndAnimation frames
    assert [f.samples[0] if f else None for f in body] == [0, 1, 2, None, None, 3]


def test_animation_ids_wrap_around_after_255():
    from unittest.mock import patch
    import pycozmo
    from pycozmo import anim
    client = pycozmo.Client()
    client._next_anim_id = 255
    clip = anim.PreprocessedClip()
    with patch.object(client, 'cancel_anim'), patch.object(client.anim_controller, 'play_anim_frame'):
        client.play_anim_ppclip(clip)
        assert client._next_anim_id == 1
        client.play_anim_ppclip(clip)
        assert client._next_anim_id == 2
