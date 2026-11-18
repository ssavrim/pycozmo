"""
Wwise hierarchy reading and animation sound rendering, on synthetic banks.

No resource of the Cozmo application is used: banks are written here with the
subset of the version 120 layout read by ``audiokinetic.hierarchy``.
"""

import collections
import random
import struct

import numpy as np
import pytest

from pycozmo import anim, anim_audio, anim_encoder as ae, protocol_encoder as pe
from pycozmo.audiokinetic import exception as ak_exception, hierarchy as wh


# ---------------------------------------------------------------------------
# Synthetic bank writer

def props(values=None, ranges=None):
    values, ranges = values or {}, ranges or {}

    def val(k, v):
        return struct.pack("<i" if k in wh._INT_PROPS else "<f", v)
    out = bytes([len(values)]) + bytes(values) + b"".join(val(k, v) for k, v in values.items())
    out += bytes([len(ranges)]) + bytes(ranges) + b"".join(val(k, a) + val(k, b) for k, (a, b) in ranges.items())
    return out


def node_base(parent=0, values=None, ranges=None):
    return (b"\0\0\0" + struct.pack("<II", 0, parent) + b"\0" + props(values, ranges) +
            b"\0" + b"\0" + b"\0" * 6 + struct.pack("<I", 0) + struct.pack("<H", 0))


def obj(obj_type, obj_id, body):
    return struct.pack("<BII", obj_type, len(body) + 4, obj_id) + body


def sound(obj_id, source_id, parent=0, values=None, ranges=None, plugin=0x00040001):
    params = struct.pack("<I", 0) if plugin & 0x0f in (2, 5) else b""     # source plugins carry parameters
    source = struct.pack("<IBIIB", plugin, 1, source_id, 0, 0) + params
    return obj(2, obj_id, source + node_base(parent, values, ranges))


def ranseq(obj_id, children, weights=None, loop=1, avoid=0, random_mode=0, mode=0, parent=0, values=None):
    weights = weights or [50000] * len(children)
    body = node_base(parent, values) + struct.pack("<HHHfffHBBBB", loop, 0, 0, 0, 0, 0, avoid, 0, random_mode, mode, 0)
    body += struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    body += struct.pack("<H", len(children)) + b"".join(struct.pack("<Ii", c, w) for c, w in zip(children, weights))
    return obj(5, obj_id, body)


def switch(obj_id, children, default, mapping):
    body = node_base() + struct.pack("<BIIB", 0, 1, default, 0)
    body += struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    body += struct.pack("<I", len(mapping))
    for switch_id, nodes in mapping.items():
        body += struct.pack("<II", switch_id, len(nodes)) + b"".join(struct.pack("<I", n) for n in nodes)
    body += struct.pack("<I", 0)
    return obj(6, obj_id, body)


def mixer(obj_id, children, values=None, ranges=None):
    body = node_base(0, values, ranges) + struct.pack("<I", len(children)) + b"".join(
        struct.pack("<I", c) for c in children)
    return obj(7, obj_id, body)


def action(obj_id, action_type, scope, target, values=None, extra=b""):
    body = struct.pack("<HIB", (action_type << 8) | scope, target, 0) + props(values)
    if action_type == wh.ACTION_PLAY:
        body += b"\x04" + struct.pack("<I", 0)
    elif action_type == wh.ACTION_STOP:
        body += b"\x04" + struct.pack("<I", 0)
    elif action_type == wh.ACTION_SEEK:
        body += extra + struct.pack("<I", 0)
    return obj(3, obj_id, body)


def event(obj_id, actions):
    return obj(4, obj_id, struct.pack("<I", len(actions)) + b"".join(struct.pack("<I", a) for a in actions))


def bank(objects, media=None, version=120):
    out = b"BKHD" + struct.pack("<I", 24) + struct.pack("<IIIIII", version, 1, 0, 0, 0, 0)
    media = media or {}
    if media:
        data, index, offset = b"", b"", 0
        for media_id, payload in media.items():
            index += struct.pack("<III", media_id, offset, len(payload))
            data += payload
            offset += len(payload)
        out += b"DIDX" + struct.pack("<I", len(index)) + index + b"DATA" + struct.pack("<I", len(data)) + data
    hirc = struct.pack("<I", len(objects)) + b"".join(objects)
    return out + b"HIRC" + struct.pack("<I", len(hirc)) + hirc


# Synthetic project:
#   mixer 900 (volume -2 dB) > sounds 101..103 in random container 200 (shuffle, avoid 1)
#   event 1: play 200; event 2: play looping sound 104; event 3: stop 104 with 100 ms fade;
#   event 4: play switch 300 (default 2 -> sound 102); event 5: seek 104 to 50 %; event 6: plugin sound 105
OBJECTS = [
    mixer(900, [200], values={wh.PROP_VOLUME: -2.0}),
    sound(101, 1001, parent=200, values={wh.PROP_VOLUME: -4.0, wh.PROP_PITCH: 1200.0}),
    sound(102, 1002, parent=200, ranges={wh.PROP_PITCH: (-100.0, 100.0)}),
    sound(103, 1003, parent=200),
    ranseq(200, [101, 102, 103], avoid=1, random_mode=1, parent=900),
    sound(104, 1004, values={wh.PROP_LOOP: 0}),
    sound(105, 0, plugin=0x00650002),
    switch(300, [101, 102], 2, {1: [101], 2: [102]}),
    action(11, wh.ACTION_PLAY, 3, 200, values={wh.PROP_DELAY_TIME: 100}),
    action(12, wh.ACTION_PLAY, 3, 104),
    action(13, wh.ACTION_STOP, 3, 104, values={wh.PROP_TRANSITION_TIME: 100}),
    action(14, wh.ACTION_PLAY, 3, 300),
    action(15, wh.ACTION_SEEK, 3, 104, extra=struct.pack("<Bfff", 1, 0.5, 0.0, 0.0) + b"\0"),
    action(16, wh.ACTION_PLAY, 3, 105),
    event(1, [11]), event(2, [12]), event(3, [13]), event(4, [14]), event(5, [15]), event(6, [16]),
]


@pytest.fixture
def audio_dir(tmp_path):
    (tmp_path / "Cozmo.bnk").write_bytes(bank(OBJECTS, media={1003: b"EMBEDDED"}))
    for media_id in (1001, 1002, 1004):
        (tmp_path / "{}.wem".format(media_id)).write_bytes(str(media_id).encode())
    return tmp_path


@pytest.fixture
def hierarchy(audio_dir):
    return wh.Hierarchy.load([audio_dir / "Cozmo.bnk"])


def fake_decoder(data: bytes):
    """ Media 1001..1004: a constant level of (id - 1000) * 1000 for 0.5 s at 22,320 Hz; embedded: 3000. """
    level = 3000.0 if data == b"EMBEDDED" else (int(data) - 1000) * 1000.0
    return np.full(anim_audio.SAMPLE_RATE // 2, level, np.float32), anim_audio.SAMPLE_RATE


# ---------------------------------------------------------------------------
# Hierarchy

def test_hierarchy_reads_every_object(hierarchy):
    assert not hierarchy.unreadable
    s = hierarchy.get(101)
    assert isinstance(s, wh.Sound) and (s.source_id, s.parent_id) == (1001, 200)
    assert s.props == {wh.PROP_VOLUME: -4.0, wh.PROP_PITCH: 1200.0}
    assert hierarchy.get(102).ranges == {wh.PROP_PITCH: (-100.0, 100.0)}
    c = hierarchy.get(200)
    assert (c.loop_count, c.avoid_repeat_count, c.random_mode, c.mode) == (1, 1, 1, 0)
    assert c.children == (101, 102, 103) and c.playlist == ((101, 50000), (102, 50000), (103, 50000))
    assert hierarchy.get(300).switches == {1: (101,), 2: (102,)} and hierarchy.get(300).default_switch == 2
    assert hierarchy.get(900).children == (200,)
    stop = hierarchy.get(13)
    assert (stop.action_type, stop.scope, stop.target_id, stop.props) == (wh.ACTION_STOP, 3, 104,
                                                                          {wh.PROP_TRANSITION_TIME: 100})
    seek = hierarchy.get(15)
    assert seek.seek_relative and seek.seek_value == 0.5
    assert hierarchy.get(1).action_ids == (11,)
    assert [n.id for n in hierarchy.parents(s)] == [101, 200, 900]
    assert hierarchy.media[1003].size == len(b"EMBEDDED")


def test_hierarchy_rejects_other_versions_and_isolates_bad_objects(tmp_path):
    path = tmp_path / "old.bnk"
    path.write_bytes(bank([], version=88))
    with pytest.raises(ak_exception.AudioKineticFormatError, match="version 88"):
        wh.Hierarchy.load([path])
    broken = obj(5, 77, node_base() + b"\x01\x02")      # container cut short
    path.write_bytes(bank([broken, sound(78, 5)]))
    h = wh.Hierarchy.load([path])
    assert 77 in h.unreadable and isinstance(h.get(78), wh.Sound)


# ---------------------------------------------------------------------------
# Keyframe event choice

def kf(ids, probabilities=(), volume=1.0, t=0):
    return ae.AnimRobotAudio(trigger_time_ms=t, audio_event_ids=list(ids), volume=volume,
                             probabilities=list(probabilities), has_alts=True)


def test_choose_audio_event_follows_probabilities():
    rng = random.Random(4)
    played = sum(anim_audio.choose_audio_event(kf([7], [0.3]), rng) == 7 for _ in range(10000))
    assert 2800 <= played <= 3200
    picks = collections.Counter(anim_audio.choose_audio_event(kf([7, 8], [0.5, 0.5]), rng) for _ in range(2000))
    assert set(picks) == {7, 8}
    picks = collections.Counter(anim_audio.choose_audio_event(kf([7, 8]), rng) for _ in range(2000))
    assert set(picks) == {7, 8} and 800 < picks[7] < 1200
    assert anim_audio.choose_audio_event(kf([2 ** 32 + 5]), rng) == 5
    with pytest.raises(anim_audio.AudioEventError):
        anim_audio.choose_audio_event(kf([7, 8], [0.5]), rng)
    with pytest.raises(anim_audio.AudioEventError):
        anim_audio.choose_audio_event(kf([7, 8], [0.7, 0.6]), rng)


# ---------------------------------------------------------------------------
# Resolution

def sources(resolver, event_id):
    return [seg.source_id for v in resolver.resolve(event_id).voices for seg in v.segments]


def test_shuffle_with_avoid_repeat(hierarchy):
    resolver = anim_audio.EventResolver(hierarchy, rng=random.Random(5))
    picks = [sources(resolver, 1)[0] for _ in range(300)]
    for i in range(0, 300, 3):
        assert sorted(picks[i:i + 3]) == [1001, 1002, 1003]     # each once per cycle
    assert all(a != b for a, b in zip(picks, picks[1:]))        # never twice in a row


def test_sequence_and_weights(tmp_path):
    path = tmp_path / "b.bnk"
    path.write_bytes(bank([sound(1, 11), sound(2, 12), sound(3, 13),
                           ranseq(10, [1, 2, 3], mode=1), ranseq(20, [1, 2], weights=[1, 3]),
                           action(31, wh.ACTION_PLAY, 3, 10), action(32, wh.ACTION_PLAY, 3, 20),
                           event(41, [31]), event(42, [32])]))
    resolver = anim_audio.EventResolver(wh.Hierarchy.load([path]), rng=random.Random(6))
    assert [sources(resolver, 41)[0] for _ in range(5)] == [11, 12, 13, 11, 12]
    counts = collections.Counter(sources(resolver, 42)[0] for _ in range(4000))
    assert 0.70 < counts[12] / 4000 < 0.80
    resolver.reset()
    assert sources(resolver, 41) == [11]


def test_properties_delays_and_targets(hierarchy):
    resolver = anim_audio.EventResolver(hierarchy, rng=random.Random(7))
    seen = {}
    for _ in range(3):
        result = resolver.resolve(1)
        voice, = result.voices
        seg, = list(voice.segments)
        seen[seg.source_id] = seg
        assert voice.start_ms == 100 and {101, 102, 103} & voice.targets and {200, 900} <= voice.targets
    assert seen[1001].volume_db == -6.0 and seen[1001].pitch_cents == 1200.0     # sound -4 dB + mixer -2 dB
    assert -100.0 <= seen[1002].pitch_cents <= 100.0
    looping, = resolver.resolve(2).voices
    assert looping.endless and list(looping.segments)[0].loops == 0
    stop, = resolver.resolve(3).stops
    assert (stop.target_id, stop.fade_ms) == (104, 100)
    seek, = resolver.resolve(5).seeks
    assert seek.relative and seek.value == 0.5
    assert sources(resolver, 4) == [1002]       # default switch
    assert "event 99 not found" in resolver.resolve(99).notes


# ---------------------------------------------------------------------------
# Rendering

def renderer(audio_dir, rng=8):
    r = anim_audio.AnimAudioRenderer.from_audio_dir(audio_dir, decoder=fake_decoder, rng=random.Random(rng))
    return r


def frame_level(pkt):
    b = np.frombuffer(pkt.samples, np.uint8).astype(np.int32)
    seg, man = (b >> 4) & 7, b & 15
    return int(np.where(seg == 0, man << 4, (man | 16) << (seg + 3)).max())


def test_media_store(audio_dir, hierarchy):
    store = anim_audio.MediaStore(audio_dir, hierarchy)
    assert store.read(1001) == b"1001" and store.read(1003) == b"EMBEDDED"
    assert 1001 in store and 9999 not in store
    with pytest.raises(KeyError):
        store.read(9999)


def test_render_frames_on_the_animation_grid(audio_dir):
    r = renderer(audio_dir)
    clip = ae.AnimClip("c", [kf([4], t=200), ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=1000)])
    track = r.render(clip)
    times = sorted(track.packets)
    # Sound 102 (0.5 s) from 200 ms: frames 6 to 20 of the 30 per second grid.
    assert times[0] == round(6 * 1000 / 30) and times[-1] == round(20 * 1000 / 30)
    assert all(isinstance(p, pe.OutputAudio) and len(p.samples) == 744 for p in track.packets.values())


def test_loop_is_cut_by_stop_with_fade(audio_dir):
    r = renderer(audio_dir)
    track = r.render(ae.AnimClip("c", [kf([2], t=0), kf([3], t=1000),
                                       ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=3000)]))
    times = sorted(track.packets)
    assert times[0] == 0 and 1000 <= times[-1] <= 1100      # 100 ms fade after the stop
    assert frame_level(track.packets[times[-1]]) < frame_level(track.packets[500])


def test_endless_loop_ends_with_the_clip_and_seek_offsets(audio_dir):
    r = renderer(audio_dir)
    track = r.render(ae.AnimClip("c", [kf([2], t=0), kf([5], t=0),
                                       ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=2000)]))
    assert max(track.packets) <= 2030
    assert not any("seek" in line for line in track.log)


def test_volume_probability_plugins_and_invalid_keyframes(audio_dir):
    r = renderer(audio_dir)
    loud = r.render(ae.AnimClip("c", [kf([2], t=0, volume=1.0), ae.AnimHeadAngle(duration_ms=500)]))
    quiet = r.render(ae.AnimClip("c", [kf([2], t=0, volume=0.25), ae.AnimHeadAngle(duration_ms=500)]))
    assert frame_level(quiet.packets[100]) < frame_level(loud.packets[100])
    silent = r.render(ae.AnimClip("c", [kf([2], [0.0]), kf([2, 3], [0.9, 0.9], t=10), kf([6], t=20)]))
    assert silent.packets == {}
    assert any("silence" in line for line in silent.log)
    assert any("keyframe ignored" in line for line in silent.log)
    assert any("source plugin 0x650002" in line for line in silent.log)


def test_preprocessed_clip_includes_sound(audio_dir):
    r = renderer(audio_dir)
    clip = ae.AnimClip("c", [kf([4], t=0), ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=300, angle_deg=5)])
    frames = anim.PreprocessedClip.from_anim_clip(clip, audio=r).keyframes
    assert any(isinstance(p, pe.OutputAudio) for ps in frames.values() for p in ps)
    assert sum(isinstance(p, pe.OutputAudio) for p in frames[0]) == 1
    without = anim.PreprocessedClip.from_anim_clip(clip, audio=r, allowed_types=(ae.AnimHeadAngle,)).keyframes
    assert not any(isinstance(p, pe.OutputAudio) for ps in without.values() for p in ps)
