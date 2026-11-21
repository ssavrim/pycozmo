import csv
import importlib.util
import pathlib
import types
import wave
from typing import Sequence

import dpkt
import numpy as np
import pytest

from pycozmo import audio, protocol_encoder, speech_capture
from pycozmo.frame import Frame
from pycozmo.protocol_ast import FrameType


APP = (b"\xac\x1f\x01\x02", 50000)
ROBOT = (b"\xac\x1f\x01\x01", 5551)
TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "pycozmo_tts_dataset.py"


def _tone(amplitude: int) -> bytes:
    t = np.arange(speech_capture.FRAME_SAMPLES)
    pcm = (amplitude * np.sin(2 * np.pi * 440 * t / speech_capture.SAMPLE_RATE)).astype("<i2").tobytes()
    return bytes(audio.bytes_to_cozmo(pcm, 1, 1))


class _Capture:
    """ Builds the UDP traffic of an app streaming audio to the robot. """

    def __init__(self, linktype: int = 1):
        self.linktype = linktype
        self.records: list[tuple[float, bytes]] = []
        self.time = 1000.0
        self.seq = 0

    def _udp(self, src: tuple[bytes, int], dst: tuple[bytes, int], payload: bytes) -> bytes:
        udp = dpkt.udp.UDP(sport=src[1], dport=dst[1], data=payload)
        udp.ulen = len(udp)
        ip = dpkt.ip.IP(src=src[0], dst=dst[0], p=dpkt.ip.IP_PROTO_UDP, data=udp)
        ip.len = len(ip)
        if self.linktype == 1:
            return bytes(dpkt.ethernet.Ethernet(src=b"\x02" * 6, dst=b"\x04" * 6, data=ip))
        return bytes(ip)

    def engine(self, pkts: Sequence[protocol_encoder.Packet], resend: bytes | None = None) -> bytes:
        if resend is not None:
            payload = resend
        else:
            frame = Frame(FrameType.ENGINE, self.seq, self.seq + len(pkts) - 1, 0, list(pkts))
            payload = frame.to_bytes()
            self.seq += len(pkts)
        self.records.append((self.time, self._udp(APP, ROBOT, payload)))
        self.time += speech_capture.FRAME_DURATION
        return payload

    def robot(self) -> None:
        frame = Frame(FrameType.ROBOT, 0, 0, self.seq, [protocol_encoder.OutputAudio(samples=_tone(20000))])
        self.records.append((self.time, self._udp(ROBOT, APP, frame.to_bytes())))

    def pause(self, seconds: float) -> None:
        self.time += seconds

    def write(self, path: pathlib.Path) -> pathlib.Path:
        with open(path, "wb") as f:
            writer = dpkt.pcap.Writer(f, linktype=self.linktype)
            for ts, buf in self.records:
                writer.writepkt(buf, ts=ts)
        return path


def _speech(cap: _Capture, frames: int, amplitude: int = 12000) -> None:
    for _ in range(frames):
        cap.engine([protocol_encoder.OutputAudio(samples=_tone(amplitude))])


def _silence(cap: _Capture, frames: int) -> None:
    for _ in range(frames):
        cap.engine([protocol_encoder.OutputSilence()])


def _two_sentences(tmp_path: pathlib.Path, linktype: int = 1) -> pathlib.Path:
    cap = _Capture(linktype)
    _silence(cap, 5)
    _speech(cap, 10)
    sent = cap.engine([protocol_encoder.OutputAudio(samples=_tone(12000)),
                       protocol_encoder.OutputAudio(samples=_tone(12000))])
    cap.engine([], resend=sent)          # Retransmission: must not duplicate audio.
    cap.robot()                          # Robot frames are ignored.
    _silence(cap, 30)                    # ~1 s pause inside the stream.
    _speech(cap, 15)
    _silence(cap, 5)
    cap.pause(3.0)                       # The app stops streaming.
    _speech(cap, 3, amplitude=200)       # Below threshold: noise, not speech.
    return cap.write(tmp_path / "capture.pcap")


def test_u_law_decoding_inverts_encoding():
    assert all(audio.u_law_encoding(audio.u_law_decoding(code)) == code for code in range(256))
    samples = np.array([0, 100, -100, 1000, -1000, 20000, -20000, 32767, -32768])
    decoded = audio.cozmo_to_pcm(bytes(audio.u_law_encoding(int(s)) for s in samples))
    assert decoded.dtype == np.int16
    assert np.all(np.abs(decoded.astype(int) - samples) <= np.maximum(16, np.abs(samples) // 30))
    with pytest.raises(ValueError):
        audio.u_law_decoding(256)


def test_audio_frames_skip_retransmissions_and_robot_frames(tmp_path):
    path = _two_sentences(tmp_path)
    frames = speech_capture.extract_audio_frames(speech_capture.read_pcap_payloads(path))
    voiced = [f for f in frames if f.pcm is not None and np.abs(f.pcm).max() > 5000]
    assert len(voiced) == 10 + 2 + 15
    assert sum(f.pcm is None for f in frames) == 5 + 30 + 5
    assert all(len(f.pcm) == speech_capture.FRAME_SAMPLES for f in frames if f.pcm is not None)


def test_raw_ip_captures_are_read(tmp_path):
    path = _two_sentences(tmp_path, linktype=101)
    frames = speech_capture.extract_audio_frames(speech_capture.read_pcap_payloads(path))
    assert len(frames) == 10 + 2 + 15 + 5 + 30 + 5 + 3


def test_reset_starts_a_new_sequence():
    def payload(first_seq):
        return Frame(FrameType.ENGINE, first_seq, first_seq, 0,
                     [protocol_encoder.OutputAudio(samples=_tone(12000))]).to_bytes()
    reset = Frame(FrameType.RESET, 0, 0, 0, []).to_bytes()
    flow = (APP, ROBOT)
    frames = speech_capture.extract_audio_frames(
        [(0.0, flow, payload(7)), (0.1, flow, reset), (0.2, flow, payload(0)), (0.3, flow, payload(0))])
    assert len(frames) == 2


def test_utterances_split_at_pauses(tmp_path):
    frames = speech_capture.extract_audio_frames(speech_capture.read_pcap_payloads(_two_sentences(tmp_path)))
    utts = speech_capture.split_utterances(frames, padding=0.1)
    assert len(utts) == 2
    pad = round(0.1 / speech_capture.FRAME_DURATION)
    assert len(utts[0].pcm) == (12 + 2 * pad) * speech_capture.FRAME_SAMPLES
    assert len(utts[1].pcm) == (15 + 2 * pad) * speech_capture.FRAME_SAMPLES
    assert utts[0].end_time < utts[1].start_time
    # A longer allowed pause merges both sentences.
    assert len(speech_capture.split_utterances(frames, max_gap=1.5)) == 1
    # A lower threshold keeps the quiet frames after the capture pause as their own utterance.
    assert len(speech_capture.split_utterances(frames, threshold=100, min_duration=0.05)) == 3


def test_invalid_parameters():
    with pytest.raises(ValueError):
        speech_capture.split_utterances([], max_gap=0)
    with pytest.raises(ValueError):
        speech_capture.split_utterances([], threshold=0)


def test_dataset_is_written_in_ljspeech_format(tmp_path):
    frames = speech_capture.extract_audio_frames(speech_capture.read_pcap_payloads(_two_sentences(tmp_path)))
    utts = speech_capture.split_utterances(frames)
    out = speech_capture.write_dataset(utts, tmp_path / "ds", texts=["Hello Anna.", "Ça va ?"])
    assert (out / "metadata.csv").read_text(encoding="utf-8") == "cozmo_0001|Hello Anna.\ncozmo_0002|Ça va ?\n"
    with wave.open(str(out / "wavs" / "cozmo_0002.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22050)
        assert w.getnframes() == len(utts[1].pcm)
    with open(out / "segments.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))
    assert rows[0] == ["id", "start_time", "end_time", "duration"] and len(rows) == 3
    with pytest.raises(ValueError):
        speech_capture.write_dataset(utts, tmp_path / "ds2", texts=["Only one."])
    with pytest.raises(ValueError):
        speech_capture.write_dataset(utts, tmp_path / "ds3", prefix="../x")


def test_texts_file(tmp_path):
    path = tmp_path / "texts.txt"
    path.write_text("# Session 1\nHello Anna.\n\n  Ça va ?  \n", encoding="utf-8")
    assert speech_capture.read_texts(path) == ["Hello Anna.", "Ça va ?"]
    path.write_text("a|b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        speech_capture.read_texts(path)


def _tool() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("pycozmo_tts_dataset", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool(tmp_path, capsys):
    tool = _tool()
    capture = _two_sentences(tmp_path)
    texts = tmp_path / "texts.txt"
    texts.write_text("Hello Anna.\nÇa va ?\n", encoding="utf-8")
    out = tmp_path / "ds"

    assert tool.main([str(capture), "-t", str(texts), "-l", "-o", str(out)]) == 0
    assert not out.exists()
    assert tool.main([str(capture), "-t", str(texts), "-o", str(out)]) == 0
    assert (out / "metadata.csv").exists() and len(list((out / "wavs").iterdir())) == 2
    # Non-empty output directory.
    assert tool.main([str(capture), "-t", str(texts), "-o", str(out)]) == 1
    # Sentence count mismatch.
    texts.write_text("Hello Anna.\n", encoding="utf-8")
    assert tool.main([str(capture), "-t", str(texts), "-o", str(tmp_path / "other")]) == 1
    assert "1 sentences for 2 utterances" in capsys.readouterr().err
