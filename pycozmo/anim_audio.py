"""

Sound of original animations: from RobotAudio keyframes to OutputAudio packets.

Specification: ``docs/specs/animation-audio.md``.

1. ``choose_audio_event()``: a RobotAudio keyframe plays at most one of its events,
   drawn with its probabilities; it can also play nothing.
2. ``EventResolver``: a Wwise event becomes voices (Play), stops (Stop) and seeks (Seek),
   through random/sequence containers (weights, shuffle, avoid-repeat, sequence),
   switch and layer containers, with volume, pitch, filters, delays and loops.
3. ``AnimAudioRenderer``: voices are decoded (external vgmstream-cli), mixed at the
   robot rate (22,320 samples/s) and cut into one OutputAudio packet per animation frame.

"""

import collections
import dataclasses
import math
import typing
import os
import pathlib
import random
import subprocess
import tempfile
import wave
from typing import Any, Callable, Deque, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple, Union

import numpy as np

from . import anim_encoder
from . import protocol_encoder
from .audiokinetic import hierarchy as wh


__all__ = [
    "SAMPLE_RATE",
    "FRAME_SAMPLES",

    "AudioEventError",
    "choose_audio_event",
    "Segment",
    "Voice",
    "StopCommand",
    "SeekCommand",
    "ResolvedEvent",
    "EventResolver",
    "MediaStore",
    "VgmstreamDecoder",
    "AudioTrack",
    "AnimAudioRenderer",
]


#: Robot audio rate: 744 samples per animation frame, 30 frames per second.
SAMPLE_RATE = 22320
FRAME_SAMPLES = 744
FRAME_MS = 1000.0 / 30
#: Fade applied when a voice is cut by a Stop without fade time or by the end of a clip.
DEFAULT_FADE_MS = 30
#: Maximum resolution depth of nested containers.
MAX_DEPTH = 8

PathLike = Union[str, os.PathLike]


class AudioEventError(ValueError):
    """ A RobotAudio keyframe is invalid. """


def choose_audio_event(keyframe: anim_encoder.AnimRobotAudio, rng: Optional[random.Random] = None) -> Optional[int]:
    """
    Event played by a RobotAudio keyframe, or None for silence.

    Without probabilities, each event has 1/N. Probabilities must match the events and
    add up to at most 1, otherwise the keyframe is invalid.
    """
    ids = [int(i) & 0xffffffff for i in keyframe.audio_event_ids]
    if not ids:
        return None
    probs = [float(p) for p in typing.cast("list[float]", keyframe.probabilities or [])] or [1.0 / len(ids)] * len(ids)
    if len(probs) != len(ids):
        raise AudioEventError("{} probabilities for {} audio events".format(len(probs), len(ids)))
    if sum(probs) > 1.0 + 1e-6:
        raise AudioEventError("Audio event probabilities add up to {:.3f}".format(sum(probs)))
    r = (rng or random).random()
    acc = 0.0
    for event_id, p in zip(ids, probs):
        acc += p
        if r < acc:
            return event_id
    return None


# ---------------------------------------------------------------------------
# Resolution

@dataclasses.dataclass(frozen=True)
class Segment:
    """ One media played once or looped. """
    source_id: int
    volume_db: float = 0.0
    pitch_cents: float = 0.0
    lpf: float = 0.0
    hpf: float = 0.0
    loops: int = 1              # 0: until stopped
    plugin_id: int = 0          # source plugin (tone, silence...) instead of a media


@dataclasses.dataclass
class Voice:
    """ Segments played one after the other from ``start_ms``. """
    start_ms: float
    segments: Iterator[Segment]
    targets: FrozenSet[int]     # object IDs a Stop or Seek can address
    gain: float = 1.0
    endless: bool = False       # loops until stopped


@dataclasses.dataclass(frozen=True)
class StopCommand:
    at_ms: float
    target_id: int
    scope: int
    exceptions: Tuple[int, ...] = ()
    fade_ms: float = DEFAULT_FADE_MS


@dataclasses.dataclass(frozen=True)
class SeekCommand:
    at_ms: float
    target_id: int
    relative: bool
    value: float                # fraction of the duration if relative, else ms


@dataclasses.dataclass
class ResolvedEvent:
    voices: List[Voice] = dataclasses.field(default_factory=list)
    stops: List[StopCommand] = dataclasses.field(default_factory=list)
    seeks: List[SeekCommand] = dataclasses.field(default_factory=list)
    notes: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _ContainerState:
    history: Deque[int] = dataclasses.field(default_factory=collections.deque)
    remaining: List[int] = dataclasses.field(default_factory=list)
    position: int = 0


class EventResolver:
    """
    Resolve Wwise events to voices. Keeps the state of containers (shuffle, avoid-repeat,
    sequence position) between calls; ``reset()`` clears it.
    """

    def __init__(self, hierarchy: wh.Hierarchy, rng: Optional[random.Random] = None) -> None:
        self.hierarchy = hierarchy
        self.rng = rng or random.Random()
        self._state = {}    # type: Dict[int, _ContainerState]

    def reset(self) -> None:
        self._state.clear()

    def _value(self, node: Union[wh.Node, wh.Action], prop: int, default: float = 0.0) -> float:
        value = float(node.props.get(prop, default))
        if prop in node.ranges:
            low, high = node.ranges[prop]
            value += self.rng.uniform(float(low), float(high))
        return value

    def _segment(self, sound: wh.Sound) -> Segment:
        volume = pitch = lpf = hpf = 0.0
        for node in self.hierarchy.parents(sound):
            volume += self._value(node, wh.PROP_VOLUME) + self._value(node, wh.PROP_MAKE_UP_GAIN)
            pitch += self._value(node, wh.PROP_PITCH)
            lpf += self._value(node, wh.PROP_LPF)
            hpf += self._value(node, wh.PROP_HPF)
        loops = int(sound.props.get(wh.PROP_LOOP, 1))
        plugin = sound.plugin_id if sound.plugin_id & 0x0f == 2 else 0
        return Segment(sound.source_id, volume, pitch, min(lpf, 100.0), min(hpf, 100.0), max(loops, 0), plugin)

    def _targets(self, path: Sequence[int]) -> FrozenSet[int]:
        ids = set(path)
        for obj_id in path:
            obj = self.hierarchy.get(obj_id)
            if isinstance(obj, wh.Node):
                ids.update(n.id for n in self.hierarchy.parents(obj))
        return frozenset(ids)

    def _pick(self, c: wh.RanSeqContainer) -> Optional[int]:
        items = list(c.playlist) or [(child, 1) for child in c.children]
        if not items:
            return None
        state = self._state.setdefault(c.id, _ContainerState())
        ids = [i for i, _ in items]
        if c.mode == 1:     # sequence
            chosen = ids[state.position % len(ids)]
            state.position += 1
            return chosen
        weights = dict(items)
        avoid = min(c.avoid_repeat_count, len(ids) - 1)
        pool = ids
        if c.random_mode == 1:      # shuffle: each item once before any repeats
            if not state.remaining:
                state.remaining = list(ids)
            pool = state.remaining
        candidates = [i for i in pool if i not in list(state.history)[-avoid:]] if avoid else list(pool)
        candidates = candidates or list(pool)
        total = sum(max(weights[i], 0) for i in candidates)
        if total <= 0:
            chosen = self.rng.choice(candidates)
        else:
            r = self.rng.random() * total
            chosen = candidates[-1]
            for i in candidates:
                r -= max(weights[i], 0)
                if r < 0:
                    chosen = i
                    break
        if c.random_mode == 1:
            state.remaining.remove(chosen)
        state.history.append(chosen)
        while len(state.history) > max(avoid, 1):
            state.history.popleft()
        return chosen

    def _expand(self, obj_id: int, path: Tuple[int, ...],
                notes: List[str]) -> List[Tuple[Iterator[Segment], Tuple[int, ...], bool]]:
        """ (segments, path, endless) of each simultaneous voice of an object. """
        if len(path) > MAX_DEPTH or obj_id in path:
            notes.append("container nesting too deep at {}".format(obj_id))
            return []
        obj = self.hierarchy.get(obj_id)
        path = path + (obj_id,)
        if isinstance(obj, wh.Sound):
            segment = self._segment(obj)
            return [(iter([segment]), path, segment.loops == 0)]
        if isinstance(obj, wh.RanSeqContainer):
            if obj.loop_count == 1:
                child = self._pick(obj)
                return self._expand(child, path, notes) if child is not None else []
            return [(self._loop(obj, path, notes), path, obj.loop_count == 0)]
        if isinstance(obj, wh.SwitchContainer):
            children = obj.switches.get(obj.default_switch) or obj.children[:1]
            notes.append("switch {} plays its default".format(obj_id))
            return [v for child in children for v in self._expand(child, path, notes)]
        if isinstance(obj, wh.LayerContainer):
            return [v for child in obj.children for v in self._expand(child, path, notes)]
        kind = "missing" if obj is None else "type {}".format(getattr(obj, "type", "?"))
        notes.append("object {} ({}) is not played".format(obj_id, kind))
        return []

    def _loop(self, c: wh.RanSeqContainer, path: Tuple[int, ...], notes: List[str]) -> Iterator[Segment]:
        """ Segments of a looping container: a new pick for each iteration. """
        iteration = 0
        while c.loop_count == 0 or iteration < c.loop_count:
            iteration += 1
            child = self._pick(c)
            voices = self._expand(child, path, notes) if child is not None else []
            if not voices:
                return
            yield from voices[0][0]

    def resolve(self, event_id: int) -> ResolvedEvent:
        """ Resolve an event; times are relative to the event. """
        result = ResolvedEvent()
        event = self.hierarchy.get(event_id)
        if not isinstance(event, wh.Event):
            result.notes.append("event {} not found".format(event_id))
            return result
        for action_id in event.action_ids:
            action = self.hierarchy.get(action_id)
            if not isinstance(action, wh.Action):
                result.notes.append("action {} not found".format(action_id))
                continue
            delay = max(self._value(action, wh.PROP_DELAY_TIME), 0.0)
            if action.action_type == wh.ACTION_PLAY:
                for segments, path, endless in self._expand(action.target_id, (), result.notes):
                    target = self.hierarchy.get(path[-1])
                    initial = float(target.props.get(wh.PROP_INITIAL_DELAY, 0.0)) if isinstance(target, wh.Node) \
                        else 0.0
                    result.voices.append(Voice(delay + initial * 1000.0, segments, self._targets(path),
                                               endless=endless))
            elif action.action_type == wh.ACTION_STOP:
                fade = action.props.get(wh.PROP_TRANSITION_TIME, DEFAULT_FADE_MS)
                result.stops.append(StopCommand(delay, action.target_id, action.scope, action.exceptions, float(fade)))
            elif action.action_type == wh.ACTION_SEEK:
                value = action.seek_value + self.rng.uniform(action.seek_min, action.seek_max)
                result.seeks.append(SeekCommand(delay, action.target_id, action.seek_relative,
                                                min(max(value, 0.0), 1.0) if action.seek_relative else value))
            else:
                result.notes.append("action {} of type {} ignored".format(action_id, action.action_type))
        return result


# ---------------------------------------------------------------------------
# Media

class MediaStore:
    """ Media of sound banks: .wem files of the audio directory, or media embedded in banks. """

    def __init__(self, audio_dir: PathLike, hierarchy: wh.Hierarchy) -> None:
        self.audio_dir = pathlib.Path(audio_dir)
        self.hierarchy = hierarchy
        self._files = {}    # type: Dict[int, pathlib.Path]
        for path in self.audio_dir.rglob("*.wem"):
            if path.stem.isdigit():
                self._files.setdefault(int(path.stem), path)

    def __contains__(self, source_id: int) -> bool:
        return source_id in self._files or source_id in self.hierarchy.media

    def read(self, source_id: int) -> bytes:
        """ WEM data of a media. Raises KeyError if it is not available. """
        path = self._files.get(source_id)
        if path is not None:
            return path.read_bytes()
        media = self.hierarchy.media[source_id]
        with open(media.bank_path, "rb") as f:
            f.seek(media.offset)
            return f.read(media.size)


class VgmstreamDecoder:
    """ Decode WEM data to mono float PCM with a separately installed vgmstream-cli. """

    def __init__(self, executable: str = "vgmstream-cli", max_seconds: float = 60.0) -> None:
        self.executable = os.environ.get("VGMSTREAM_CLI", executable)
        self.max_seconds = max_seconds

    def __call__(self, data: bytes) -> Tuple[np.ndarray, int]:
        with tempfile.TemporaryDirectory(prefix="pycozmo-wem-") as tmp:
            src = pathlib.Path(tmp) / "media.wem"
            dst = pathlib.Path(tmp) / "media.wav"
            src.write_bytes(data)
            try:
                subprocess.run([self.executable, "-o", str(dst), str(src)], check=True, timeout=20,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except FileNotFoundError as e:
                raise ValueError("vgmstream-cli executable not found") from e
            except (subprocess.SubprocessError, OSError) as e:
                raise ValueError("WEM decoding failed") from e
            with wave.open(str(dst), "rb") as w:
                width, channels, rate, count = w.getsampwidth(), w.getnchannels(), w.getframerate(), w.getnframes()
                if width != 2 or rate <= 0 or count > rate * self.max_seconds:
                    raise ValueError("Unsupported decoded audio format or duration")
                raw = w.readframes(count)
        samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(np.float32).mean(axis=1)
        return samples, rate


# ---------------------------------------------------------------------------
# Rendering

@dataclasses.dataclass
class AudioTrack:
    """ OutputAudio packets by clip time (ms), and what happened to each keyframe. """
    packets: Dict[int, protocol_encoder.OutputAudio]
    log: List[str]


def _one_pole(samples: np.ndarray, alpha: float, highpass: bool) -> np.ndarray:
    out = np.empty_like(samples)
    acc = 0.0
    for i, x in enumerate(samples):
        acc += alpha * (x - acc)
        out[i] = x - acc if highpass else acc
    return out


def _filter_alpha(amount: float, highpass: bool) -> float:
    """
    One-pole coefficient for a Wwise filter amount (0-100).

    Approximation: the cutoff moves logarithmically from 20 kHz (LPF 0) or 20 Hz (HPF 0)
    towards 50 Hz (LPF 100) or 10 kHz (HPF 100).
    """
    t = min(max(amount, 0.0), 100.0) / 100.0
    cutoff = 20.0 * (10000.0 / 20.0) ** t if highpass else 20000.0 * (50.0 / 20000.0) ** t
    cutoff = min(cutoff, SAMPLE_RATE / 2.0 - 1.0)
    return 1.0 - math.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)


def _encode_frames(pcm: np.ndarray) -> np.ndarray:
    """ Vectorized ``audio.u_law_encoding`` of int16 samples. """
    s = pcm.astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    magnitude = np.where(s < 0, -s - 1, s)
    high = magnitude >> 8
    segment = np.zeros_like(high)
    for bit in range(8):
        segment = np.where(high >= (1 << bit), bit + 1, segment)
    mantissa = np.where(segment == 0, magnitude >> 4, (magnitude >> (segment + 3)) & 0x0f)
    result: np.ndarray = (sign | (segment << 4) | mantissa).astype(np.uint8)
    return result


class AnimAudioRenderer:
    """ Render the RobotAudio track of a clip to OutputAudio packets. """

    def __init__(self, resolver: EventResolver, store: MediaStore,
                 decoder: Optional[Callable[[bytes], Tuple[np.ndarray, int]]] = None,
                 rng: Optional[random.Random] = None, max_seconds: float = 60.0, cache_size: int = 64) -> None:
        self.resolver = resolver
        self.store = store
        self.decoder = decoder or VgmstreamDecoder()
        self.rng = rng or resolver.rng
        self.max_samples = int(max_seconds * SAMPLE_RATE)
        self._cache = collections.OrderedDict()     # type: collections.OrderedDict
        self._cache_size = cache_size

    @classmethod
    def from_audio_dir(cls, audio_dir: PathLike, **kwargs: Any) -> "AnimAudioRenderer":
        """ Renderer for the sound banks and media of a directory (``SoundbanksInfo.xml``, ``*.bnk``). """
        audio_dir = pathlib.Path(audio_dir)
        banks = sorted(audio_dir.glob("*.bnk")) + sorted(audio_dir.glob("*/*.bnk"))
        hierarchy = wh.Hierarchy.load(banks)
        rng = kwargs.pop("rng", None) or random.Random()
        return cls(EventResolver(hierarchy, rng=rng), MediaStore(audio_dir, hierarchy), rng=rng, **kwargs)

    def _source(self, source_id: int) -> np.ndarray:
        """ Media at the robot rate, cached. """
        if source_id in self._cache:
            self._cache.move_to_end(source_id)
            cached: np.ndarray = self._cache[source_id]
            return cached
        samples, rate = self.decoder(self.store.read(source_id))
        if rate != SAMPLE_RATE and len(samples):
            positions = np.arange(round(len(samples) * SAMPLE_RATE / rate)) * rate / SAMPLE_RATE
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        self._cache[source_id] = samples
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return samples

    def _segment_samples(self, seg: Segment) -> np.ndarray:
        samples = self._source(seg.source_id)
        if seg.pitch_cents and len(samples):
            step = 2.0 ** (seg.pitch_cents / 1200.0)
            positions = np.arange(0, len(samples) - 1, step)
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        if seg.lpf > 0:
            samples = _one_pole(samples, _filter_alpha(seg.lpf, False), False)
        if seg.hpf > 0:
            samples = _one_pole(samples, _filter_alpha(seg.hpf, True), True)
        return samples * np.float32(10.0 ** (seg.volume_db / 20.0))

    def _render_voice(self, voice: Voice, limit: int, offset: float, log: List[str]) -> np.ndarray:
        """ Samples of a voice, at most ``limit`` long; ``offset`` skips a fraction of the first segment. """
        parts = []
        length = 0
        first = True
        empty = 0
        for seg in voice.segments:
            samples = np.zeros(0, np.float32)
            if seg.plugin_id:
                log.append("{:.0f} ms: source plugin {:#x} not rendered".format(voice.start_ms, seg.plugin_id))
            else:
                samples = self._segment_samples(seg)
            if not len(samples):
                # A looping container of empty segments would never end.
                empty += 1
                if empty >= 8:
                    break
                continue
            empty = 0
            if first and offset:
                start = int(len(samples) * offset)
                parts.append(samples[start:])
                length += len(samples) - start
            repeats = seg.loops if seg.loops > 0 else None
            done = 1 if first and offset else 0
            first = False
            while (repeats is None or done < repeats) and length < limit:
                parts.append(samples)
                length += len(samples)
                done += 1
            if length >= limit:
                break
        if not parts:
            return np.zeros(0, np.float32)
        return np.concatenate(parts)[:limit] * np.float32(voice.gain)

    def render(self, clip: anim_encoder.AnimClip) -> AudioTrack:
        log = []
        voices = []     # type: List[Voice]
        stops = []      # type: List[StopCommand]
        seeks = []      # type: List[SeekCommand]
        end_ms = 0.0
        for keyframe in clip.keyframes:
            end_ms = max(end_ms, getattr(keyframe, "trigger_time_ms", 0) + getattr(keyframe, "duration_ms", 0))
        keyframes = sorted((k for k in clip.keyframes if isinstance(k, anim_encoder.AnimRobotAudio)),
                           key=lambda k: k.trigger_time_ms)
        for keyframe in keyframes:
            t = keyframe.trigger_time_ms
            try:
                event_id = choose_audio_event(keyframe, self.rng)
            except AudioEventError as e:
                log.append("{} ms: keyframe ignored ({})".format(t, e))
                continue
            if event_id is None:
                log.append("{} ms: silence (probability)".format(t))
                continue
            resolved = self.resolver.resolve(event_id)
            log.extend("{} ms: event {}: {}".format(t, event_id, n) for n in resolved.notes)
            for v in resolved.voices:
                v.start_ms += t
                v.gain *= float(keyframe.volume)
                voices.append(v)
            stops.extend(dataclasses.replace(s, at_ms=s.at_ms + t) for s in resolved.stops)
            seeks.extend(dataclasses.replace(s, at_ms=s.at_ms + t) for s in resolved.seeks)
            if resolved.voices:
                log.append("{} ms: event {}: {} voice(s)".format(t, event_id, len(resolved.voices)))

        end_sample = int(end_ms * SAMPLE_RATE / 1000)
        fade_default = int(DEFAULT_FADE_MS * SAMPLE_RATE / 1000)
        mix = np.zeros(0, np.float32)
        for v in voices:
            start = int(round(v.start_ms * SAMPLE_RATE / 1000))
            cut = self._cut(v, stops)
            if cut is not None:
                cut_at, fade = cut[0] - start, cut[1]
            elif v.endless:
                # Loops end with the clip.
                cut_at, fade = end_sample - start, fade_default
            else:
                cut_at, fade = None, 0
            limit = self.max_samples if cut_at is None else min(self.max_samples, max(cut_at, 0) + fade)
            offset = 0.0
            for seek in seeks:
                if seek.target_id not in v.targets:
                    continue
                if seek.relative and seek.at_ms <= v.start_ms + FRAME_MS:
                    offset = seek.value
                else:
                    log.append("{:.0f} ms: seek of {} not applied".format(seek.at_ms, seek.target_id))
            try:
                samples = self._render_voice(v, limit, offset, log)
            except (KeyError, ValueError) as e:
                log.append("{:.0f} ms: media not played ({})".format(v.start_ms, e))
                continue
            if cut_at is not None and max(cut_at, 0) < len(samples):
                samples = samples[:max(cut_at, 0) + fade].copy()
                n = len(samples) - max(cut_at, 0)
                samples[max(cut_at, 0):] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
            if start + len(samples) > len(mix):
                mix = np.concatenate([mix, np.zeros(start + len(samples) - len(mix), np.float32)])
            mix[start:start + len(samples)] += samples

        packets = {}    # type: Dict[int, protocol_encoder.OutputAudio]
        if len(mix):
            # Soft saturation above 80 % of full scale.
            knee = 0.8 * 32767
            over = np.abs(mix) > knee
            if over.any():
                mix[over] = np.sign(mix[over]) * (knee + (32767 - knee) * np.tanh((np.abs(mix[over]) - knee) /
                                                                                  (32767 - knee)))
            pcm = np.clip(np.round(mix), -32768, 32767).astype(np.int16)
            pcm = np.concatenate([pcm, np.zeros((-len(pcm)) % FRAME_SAMPLES, np.int16)])
            frames = pcm.reshape(-1, FRAME_SAMPLES)
            encoded = _encode_frames(frames)
            for i, frame in enumerate(frames):
                if frame.any():
                    packets[int(round(i * FRAME_MS))] = protocol_encoder.OutputAudio(samples=bytes(encoded[i]))
        return AudioTrack(packets, log)

    @staticmethod
    def _cut(voice: Voice, stops: Sequence[StopCommand]) -> Optional[Tuple[int, int]]:
        """ (sample, fade samples) of the first Stop addressing a voice after its start. """
        for stop in sorted(stops, key=lambda s: s.at_ms):
            if stop.at_ms < voice.start_ms:
                continue
            if stop.scope in wh.SCOPE_ALL:
                hit = True
            elif stop.scope in wh.SCOPE_ALL_EXCEPT:
                hit = not voice.targets & set(stop.exceptions)
            else:
                hit = stop.target_id in voice.targets
            if hit:
                return int(stop.at_ms * SAMPLE_RATE / 1000), int(stop.fade_ms * SAMPLE_RATE / 1000)
        return None
