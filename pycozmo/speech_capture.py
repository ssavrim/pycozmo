"""

Rebuild the audio that the Cozmo app streams to the robot from a packet capture, and cut it into
utterances, for example to build a speech dataset of the robot's voice.

The app synthesizes speech itself and streams it to the robot as ``OutputAudio`` packets (744
samples at 22,050 Hz per animation frame), with ``OutputSilence`` packets for silent frames. The
captured packets therefore hold the exact audio sent to the speaker, without microphone noise.

"""

import csv
import math
import pathlib
import wave
from dataclasses import dataclass
from typing import BinaryIO, Iterable, Iterator, Sequence

import dpkt
import numpy as np

from . import audio
from . import protocol_encoder
from .frame import Frame
from .protocol_ast import FrameType
from .protocol_declaration import FRAME_ID


__all__ = [
    "SAMPLE_RATE",
    "FRAME_SAMPLES",
    "FRAME_DURATION",
    "AudioFrame",
    "Utterance",
    "read_pcap_payloads",
    "extract_audio_frames",
    "split_utterances",
    "read_texts",
    "write_dataset",
]


#: Sample rate of robot audio (Hz).
SAMPLE_RATE = 22050
#: Samples per OutputAudio packet.
FRAME_SAMPLES = 744
#: Duration of one audio packet (s).
FRAME_DURATION = FRAME_SAMPLES / SAMPLE_RATE

_DLT_NULL = 0
_DLT_EN10MB = 1
_DLT_RAW = (12, 14, 101)
_DLT_LOOP = 108
_DLT_LINUX_SLL = 113
_DLT_LINUX_SLL2 = 276


@dataclass
class AudioFrame:
    """ One audio packet sent to the robot: capture time (s) and PCM samples, None for silence. """
    time: float
    pcm: np.ndarray | None


@dataclass
class Utterance:
    """ A run of audio between pauses. Times are capture times (s) of the first and last packet. """
    start_time: float
    end_time: float
    pcm: np.ndarray

    @property
    def duration(self) -> float:
        return len(self.pcm) / SAMPLE_RATE


def _ip_payload(linktype: int, buf: bytes) -> dpkt.Packet | None:
    """ The IPv4 packet of a captured link-layer frame, or None. """
    try:
        if linktype == _DLT_EN10MB:
            eth = dpkt.ethernet.Ethernet(buf)
            ip = eth.data
        elif linktype == _DLT_LINUX_SLL:
            ip = dpkt.sll.SLL(buf).data
        elif linktype == _DLT_LINUX_SLL2:
            ip = dpkt.ip.IP(buf[20:])
        elif linktype in _DLT_RAW:
            ip = dpkt.ip.IP(buf)
        elif linktype in (_DLT_NULL, _DLT_LOOP):
            ip = dpkt.ip.IP(buf[4:])
        else:
            raise ValueError("Unsupported capture link type {}.".format(linktype))
    except (dpkt.UnpackError, IndexError):
        return None
    return ip if isinstance(ip, dpkt.ip.IP) else None


def _pcap_reader(f: BinaryIO) -> Iterator[tuple[int, float, bytes]]:
    magic = f.read(4)
    f.seek(0)
    if magic == b"\x0a\x0d\x0d\x0a":
        ng = dpkt.pcapng.Reader(f)
        linktype = ng.datalink()
        for ts, buf in ng:
            yield linktype, float(ts), bytes(buf)
    else:
        reader = dpkt.pcap.Reader(f)
        linktype = reader.datalink()
        for ts, buf in reader:
            yield linktype, float(ts), bytes(buf)


def read_pcap_payloads(path: str | pathlib.Path) -> Iterator[tuple[float, tuple, bytes]]:
    """ Cozmo UDP payloads of a pcap or pcapng file: (time, flow, payload).

    ``flow`` identifies the UDP conversation (addresses and ports, in the capture's direction).
    """
    with open(path, "rb") as f:
        for linktype, ts, buf in _pcap_reader(f):
            ip = _ip_payload(linktype, buf)
            if ip is None or ip.p != dpkt.ip.IP_PROTO_UDP:
                continue
            udp = ip.data
            if not isinstance(udp, dpkt.udp.UDP):
                continue
            payload = bytes(udp.data)
            if payload[:len(FRAME_ID)] != FRAME_ID:
                continue
            yield ts, (bytes(ip.src), udp.sport, bytes(ip.dst), udp.dport), payload


def extract_audio_frames(payloads: Iterable[tuple[float, tuple, bytes]]) -> list[AudioFrame]:
    """ Audio packets sent by the app to the robot, in order, without retransmissions.

    Engine frames are resent until acknowledged, so every sequenced packet is kept only the first
    time its sequence number is seen, per UDP conversation. A RESET frame starts a new sequence.
    """
    last_seq: dict[tuple, int] = {}
    frames: list[AudioFrame] = []
    for ts, flow, payload in payloads:
        try:
            frame = Frame.from_bytes(payload)
        except (ValueError, IndexError, KeyError):
            continue
        if frame.type == FrameType.RESET:
            last_seq.pop(flow, None)
            continue
        if frame.type != FrameType.ENGINE:
            continue
        for pkt in frame.pkts:
            if pkt.is_oob():
                continue
            last = last_seq.get(flow)
            if last is not None:
                delta = (pkt.seq - last) % 0x10000
                if delta == 0 or delta > 0x8000:
                    # Retransmission or older packet.
                    continue
            last_seq[flow] = pkt.seq
            if isinstance(pkt, protocol_encoder.OutputAudio):
                frames.append(AudioFrame(ts, audio.cozmo_to_pcm(bytes(pkt.samples))))
            elif isinstance(pkt, protocol_encoder.OutputSilence):
                frames.append(AudioFrame(ts, None))
    return frames


def split_utterances(frames: Sequence[AudioFrame], *, max_gap: float = 0.5, min_duration: float = 0.3,
                     threshold: int = 512, padding: float = 0.1) -> list[Utterance]:
    """ Cut audio frames into utterances.

    A frame is voiced when its peak amplitude reaches ``threshold``. Voiced frames separated by
    more than ``max_gap`` seconds of silent frames, or by a pause of more than ``max_gap`` seconds
    in the capture itself, belong to different utterances. Utterances shorter than
    ``min_duration`` seconds of voiced audio are dropped. Each utterance keeps up to ``padding``
    seconds of the surrounding frames.
    """
    if max_gap <= 0 or min_duration < 0 or padding < 0 or not 0 < threshold <= 32768:
        raise ValueError("Invalid segmentation parameters")
    n = len(frames)
    voiced = [f.pcm is not None and int(np.abs(f.pcm.astype(np.int32)).max(initial=0)) >= threshold
              for f in frames]
    # Index i is in breaks when the capture pauses between frames i - 1 and i.
    breaks = [i for i in range(1, n) if frames[i].time - frames[i - 1].time > max_gap]
    max_silent = math.ceil(max_gap / FRAME_DURATION)
    pad = round(padding / FRAME_DURATION)

    runs: list[tuple[int, int]] = []
    current: list[int] | None = None
    next_break = 0
    for i in range(n):
        crossed = False
        while next_break < len(breaks) and breaks[next_break] <= i:
            crossed = crossed or breaks[next_break] == i
            next_break += 1
        if current is not None and crossed:
            runs.append((current[0], current[1]))
            current = None
        if not voiced[i]:
            continue
        if current is None:
            current = [i, i]
        elif i - current[1] - 1 > max_silent:
            runs.append((current[0], current[1]))
            current = [i, i]
        else:
            current[1] = i
    if current is not None:
        runs.append((current[0], current[1]))

    utterances: list[Utterance] = []
    floor = 0
    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    for start, end in runs:
        if (end - start + 1) * FRAME_DURATION < min_duration:
            continue
        low = max([floor, start - pad] + [b for b in breaks if b <= start])
        high = min([n - 1, end + pad] + [b - 1 for b in breaks if b > end])
        pcm = np.concatenate([f.pcm if f.pcm is not None else silence for f in frames[low:high + 1]])
        utterances.append(Utterance(frames[low].time, frames[high].time, pcm.astype(np.int16)))
        floor = high + 1
    return utterances


def read_texts(path: str | pathlib.Path) -> list[str]:
    """ Sentences of a text file: one per non-empty line, lines starting with ``#`` ignored. """
    texts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if "|" in text:
                raise ValueError("Sentences must not contain '|': {}".format(text))
            texts.append(text)
    return texts


def write_dataset(utterances: Sequence[Utterance], out_dir: str | pathlib.Path,
                  texts: Sequence[str] | None = None, prefix: str = "cozmo") -> pathlib.Path:
    """ Write utterances as 22,050 Hz, 16-bit mono WAVE files in ``out_dir/wavs``.

    ``segments.csv`` lists each file with its capture times. With ``texts``, one per utterance in
    the same order, ``metadata.csv`` is written in LJSpeech format (``id|text``), as expected by
    Piper and most TTS training recipes. Returns the output directory.
    """
    if texts is not None and len(texts) != len(utterances):
        raise ValueError("{} sentences for {} utterances.".format(len(texts), len(utterances)))
    if not prefix or not prefix.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid file prefix")
    out = pathlib.Path(out_dir)
    wavs = out / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)
    ids = ["{}_{:04d}".format(prefix, i + 1) for i in range(len(utterances))]
    for uid, utt in zip(ids, utterances):
        with wave.open(str(wavs / (uid + ".wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(utt.pcm.astype("<i2").tobytes())
    with open(out / "segments.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="|", lineterminator="\n")
        writer.writerow(["id", "start_time", "end_time", "duration"])
        for uid, utt in zip(ids, utterances):
            writer.writerow([uid, "{:.3f}".format(utt.start_time), "{:.3f}".format(utt.end_time),
                             "{:.3f}".format(utt.duration)])
    if texts is not None:
        with open(out / "metadata.csv", "w", encoding="utf-8", newline="") as f:
            for uid, text in zip(ids, texts):
                f.write("{}|{}\n".format(uid, text))
    return out
