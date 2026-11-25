import shutil
import struct
import subprocess
import wave
from unittest.mock import patch

import pytest

import pycozmo
from pycozmo import audio


def _write_voice_wav(command, **kwargs):
    output = command[command.index('-w') + 1]
    with wave.open(output, 'wb') as dest:
        dest.setnchannels(1)
        dest.setsampwidth(2)
        dest.setframerate(44100)
        dest.writeframes(struct.pack('<4h', -32768, 0, 200, 100))


def test_synthesis_passes_utf8_stdin_and_resamples_audio():
    with patch('pycozmo.audio.subprocess.run', side_effect=_write_voice_wav) as run:
        packets = audio.synthesize_speech('Bonjour, ça va ?', voice='fr', words_per_minute=145,
                                          executable='/custom/espeak-ng')
    args = run.call_args.args[0]
    assert args[:5] == ['/custom/espeak-ng', '-v', 'fr', '-s', '145']
    assert args[-1] == '--stdin'
    assert run.call_args.kwargs['input'] == 'Bonjour, ça va ?'.encode('utf-8')
    assert len(packets) == 1
    expected = audio.bytes_to_cozmo(struct.pack('<2h', -32768, 200), 1, 1)
    assert packets[0].samples == expected


def test_client_sends_synthesized_packets_to_audio_controller():
    client = pycozmo.Client()
    packets = [object()]
    with patch('pycozmo.audio.synthesize_speech', return_value=packets) as synthesize, \
            patch.object(client.anim_controller, 'play_audio') as play:
        client.say_text('Bonjour', voice='fr', words_per_minute=145)
    synthesize.assert_called_once_with('Bonjour', voice='fr', words_per_minute=145, executable='espeak-ng')
    play.assert_called_once_with(packets)


@pytest.mark.parametrize('kwargs', [
    {'text': ''}, {'text': ' '}, {'text': 'x' * 2001}, {'text': 1},
    {'text': 'hello', 'voice': ''}, {'text': 'hello', 'voice': '--help'},
    {'text': 'hello', 'words_per_minute': True}, {'text': 'hello', 'words_per_minute': 79},
    {'text': 'hello', 'words_per_minute': 451},
])
def test_synthesis_rejects_invalid_input_before_running_executable(kwargs):
    with patch('pycozmo.audio.subprocess.run') as run:
        with pytest.raises(ValueError):
            audio.synthesize_speech(**kwargs)
    run.assert_not_called()


def test_synthesis_reports_missing_executable_and_timeout():
    with patch('pycozmo.audio.subprocess.run', side_effect=FileNotFoundError):
        with pytest.raises(ValueError, match='executable not found'):
            audio.synthesize_speech('hello')
    with patch('pycozmo.audio.subprocess.run', side_effect=subprocess.TimeoutExpired('espeak-ng', 20)):
        with pytest.raises(ValueError, match='synthesis failed'):
            audio.synthesize_speech('hello')


@pytest.mark.skipif(shutil.which('espeak-ng') is None, reason='eSpeak NG is not installed')
def test_real_espeak_synthesizes_speech():
    packets = audio.synthesize_speech('Bonjour Cozmo, ça va ?', voice='fr')
    assert packets and all(len(packet.samples) == 744 for packet in packets)
    assert len({sample for packet in packets for sample in packet.samples}) > 4
