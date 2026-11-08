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
    """ U-law encode a 16-bit PCM sample. """
    mask = 0x4000
    position = 14
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    sample += MULAW_BIAS
    if sample > MULAW_MAX:
        sample = MULAW_MAX

    while (sample & mask) != mask and position >= 7:
        mask >>= 1
        position -= 1

    lsb = (sample >> (position - 4)) & 0x0f
    return min(255, -(~(sign | ((position - 7) << 4) | lsb)))


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
        with wave.open(str(wav_path), "rb") as source:
            width, channels, rate, count = (source.getsampwidth(), source.getnchannels(),
                                            source.getframerate(), source.getnframes())
            if width != 2 or channels not in (1, 2) or rate <= 0 or count > rate * max_seconds:
                raise ValueError("Unsupported decoded audio format or duration")
            raw = source.readframes(count)
        samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(np.float32).mean(axis=1)
        if rate != 22050 and len(samples):
            positions = np.arange(round(len(samples) * 22050 / rate)) * rate / 22050
            samples = np.interp(positions, np.arange(len(samples)), samples)
        pcm = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        return [protocol_encoder.OutputAudio(samples=bytes_to_cozmo(pcm[i:i + 1488], 1, 1))
                for i in range(0, len(pcm), 1488)]
