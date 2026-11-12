"""

Cozmo audio encoding.

References:
    - https://en.wikipedia.org/wiki/%CE%9C-law_algorithm
    - http://dystopiancode.blogspot.com/2012/02/pcm-law-and-u-law-companding-algorithms.html

"""

from typing import List
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import wave
import time

from . import logger
from . import protocol_encoder


__all__ = [
    "load_wav",
    "load_wem",
    "synthesize_speech",
]


MULAW_MAX = 0x7FFF
MULAW_BIAS = 132


def load_wav(filename: str) -> List[protocol_encoder.OutputAudio]:
    """ Load a WAVE file into a list of OutputAudio packets. """

    start_time = time.perf_counter()

    with wave.open(filename, "r") as w:
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        if sampwidth != 2 or (framerate != 22050 and framerate != 48000):
            raise ValueError('Invalid audio format, only 16 bit samples are supported, ' +
                             'with 22050Hz or 48000Hz frame rates.')

        ratediv = 2 if framerate == 48000 else 1
        channels = w.getnchannels()
        pkts = []

        while True:
            frame_in = w.readframes(744 * ratediv)
            if not frame_in:
                break
            frame_out = bytes_to_cozmo(frame_in, ratediv, channels)
            pkt = protocol_encoder.OutputAudio(samples=frame_out)
            pkts.append(pkt)

    logger.debug("Loaded WAVE file in {:.02f} s.".format(time.perf_counter() - start_time))

    return pkts


def bytes_to_cozmo(byte_string: bytes, rate_correction: int, channels: int) -> bytearray:
    """ Convert a 744 sample, 16-bit audio frame into a U-law encoded frame. """
    out = bytearray(744)
    n = channels * rate_correction
    bs = struct.unpack('<{}h'.format(len(byte_string) // 2), byte_string)[0::n]
    for i, s in enumerate(bs):
        out[i] = u_law_encoding(s)
    return out


def u_law_encoding(sample: int) -> int:
    """ Encode a 16-bit PCM sample as the robot expects it.

    Sign bit 0x80, a 3-bit segment given by the highest set bit of
    ``magnitude >> 8`` and a 4-bit mantissa, with ``magnitude`` = |s|
    (-s - 1 for negatives). Unlike textbook mu-law there is no bias and no
    inversion. The previous bias of 132 plus one turned silence into a +/-132
    square wave at every zero crossing, heard as a constant distortion.
    """
    sample = max(-32768, min(32767, int(sample)))
    sign = 0x80 if sample < 0 else 0
    magnitude = -sample - 1 if sample < 0 else sample
    high = magnitude >> 8
    segment = high.bit_length()
    mantissa = magnitude >> 4 if segment == 0 else (magnitude >> (segment + 3)) & 0x0f
    return sign | (segment << 4) | mantissa


def load_wem(filename: str, decoder: str = "vgmstream-cli", max_seconds: float = 60.0):
    """Decode a Wwise WEM file into audio packets using the external vgmstream CLI.

    The executable is supplied separately. Audio is converted to mono 22,050 Hz
    PCM before encoding. ``max_seconds`` bounds the decoded audio in memory.
    """
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    with tempfile.TemporaryDirectory(prefix="pycozmo-wem-") as tmp:
        wav_path = Path(tmp) / "audio.wav"
        try:
            subprocess.run([decoder, "-o", str(wav_path), str(filename)], check=True,
                           timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise ValueError("vgmstream-cli executable not found") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("WEM decoding failed") from exc
        return _load_resampled_wav(wav_path, max_seconds)


def _load_resampled_wav(filename: Path, max_seconds: float) -> List[protocol_encoder.OutputAudio]:
    """Convert a bounded 16-bit mono/stereo WAV to 22,050 Hz audio packets."""
    with wave.open(str(filename), "rb") as source:
        width, channels, rate, count = (source.getsampwidth(), source.getnchannels(),
                                        source.getframerate(), source.getnframes())
        if width != 2 or channels not in (1, 2) or rate <= 0 or count > rate * max_seconds:
            raise ValueError("Unsupported decoded audio format or duration")
        raw = source.readframes(count)
    if len(raw) != count * channels * width:
        raise ValueError("Incomplete decoded audio")
    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(np.float32).mean(axis=1)
    if rate != 22050 and len(samples):
        positions = np.arange(round(len(samples) * 22050 / rate)) * rate / 22050
        samples = np.interp(positions, np.arange(len(samples)), samples)
    pcm = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
    return [protocol_encoder.OutputAudio(samples=bytes_to_cozmo(pcm[i:i + 1488], 1, 1))
            for i in range(0, len(pcm), 1488)]


def synthesize_speech(text: str, *, voice: str = "en", words_per_minute: int = 175,
                      executable: str = "espeak-ng") -> List[protocol_encoder.OutputAudio]:
    """Synthesize speech with a separately installed eSpeak NG executable.

    The generated audio is converted to Cozmo packets. No voice data or eSpeak
    binaries are included. The generated clip is limited to 60 seconds.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Text must contain 1 to 2000 characters")
    if not isinstance(voice, str) or not voice or len(voice) > 64 or voice.startswith('-'):
        raise ValueError("Invalid voice")
    if isinstance(words_per_minute, bool) or not isinstance(words_per_minute, int) or not 80 <= words_per_minute <= 450:
        raise ValueError("Speech rate must be an integer from 80 to 450 words per minute")
    with tempfile.TemporaryDirectory(prefix="pycozmo-speech-") as tmp:
        wav_path = Path(tmp) / "speech.wav"
        try:
            subprocess.run([executable, "-v", voice, "-s", str(words_per_minute),
                            "-w", str(wav_path), "--stdin"], input=text.encode("utf-8"),
                           check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise ValueError("eSpeak NG executable not found") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("Speech synthesis failed") from exc
        return _load_resampled_wav(wav_path, max_seconds=60.0)
