"""
Original animation selection and face animation tracks, on synthetic resources.
"""

import json
import math
import random

import numpy as np
from PIL import Image
import pytest

from pycozmo import anim, anim_encoder as ae, anim_library, protocol_encoder as pe


def member(name, weight=1.0, cooldown=0.0, mood="Default", head=None):
    m = anim.AnimationGroupMember(name, weight, cooldown, mood)
    if head is not None:
        m.use_head_angle, (m.head_angle_min, m.head_angle_max) = True, head
    return m


def pick(members, **kwargs):
    kwargs.setdefault("cooldowns", {})
    kwargs.setdefault("now", 0.0)
    chosen = anim_library.select_member(members, **kwargs)
    return chosen.name if chosen else None


def test_weighted_draw():
    rng = random.Random(3)
    members = [member("a", 1.0), member("b", 3.0)]
    counts = {"a": 0, "b": 0}
    for _ in range(4000):
        counts[pick(members, rng=rng)] += 1
    assert 0.70 < counts["b"] / 4000 < 0.80


def test_zero_weights_choose_the_last_eligible():
    assert pick([member("a", 0.0), member("b", 0.0), member("c", 0.0, head=(10, 20))]) == "b"


def test_cooldown_is_shared_by_clip_name_and_fallback_picks_the_soonest():
    cooldowns = {}
    group1 = [member("a", cooldown=10.0), member("b", cooldown=5.0)]
    group2 = [member("a", cooldown=10.0)]
    rng = random.Random(0)
    first = pick(group1, cooldowns=cooldowns, now=0.0, rng=rng)
    second = pick(group1, cooldowns=cooldowns, now=1.0, rng=rng)
    durations = {"a": 10.0, "b": 5.0}
    assert {first, second} == {"a", "b"}
    assert cooldowns == {first: 0.0 + durations[first], second: 1.0 + durations[second]}
    # "a" is on cooldown for group2 too; the fallback still plays it, without a new cooldown.
    before = dict(cooldowns)
    assert pick(group2, cooldowns=cooldowns, now=2.0) == "a" and cooldowns == before
    # In group1 both are on cooldown: the one whose cooldown ends first.
    soonest = min(before, key=before.get)
    assert pick(group1, cooldowns=cooldowns, now=2.0) == soonest
    # After the cooldowns, normal selection resumes.
    assert pick(group1, cooldowns=cooldowns, now=20.0, rng=rng) in ("a", "b")


def test_head_angle_ranges_in_radians_with_fallback_tolerance():
    members = [member("down", head=(-25, -5)), member("up", head=(10, 44))]
    assert pick(members, head_angle_rad=math.radians(20)) == "up"
    assert pick(members, head_angle_rad=math.radians(-10)) == "down"
    # Just outside a range: the 0.05 rad tolerance of the fallback applies.
    assert pick(members, head_angle_rad=math.radians(-5) + 0.04) == "down"
    # Far outside every range, or no head angle given: the first member.
    assert pick(members, head_angle_rad=math.radians(30) + 1.0) == "down"
    assert pick(members) == "down"
    # Members without a range are always eligible.
    assert pick([member("any"), member("up", head=(10, 44))], head_angle_rad=math.radians(-20)) == "any"


def test_mood_falls_back_to_default_and_strict_mode():
    members = [member("happy", mood="Happy"), member("plain")]
    assert pick(members, mood="Happy") == "happy"
    assert pick(members, mood="Sad") == "plain"
    assert pick([member("happy", mood="Happy")]) is None
    assert pick([member("a", cooldown=5.0)], cooldowns={"a": 10.0}, now=1.0, strict=True) is None


def test_animation_group_choose_member_is_compatible():
    group = anim.AnimationGroup.from_json({"Animations": [
        {"Name": "a", "Weight": 0.0, "CooldownTime_Sec": 0.0, "Mood": "Default"},
        {"Name": "b", "Weight": 0.0, "CooldownTime_Sec": 0.0, "Mood": "Default"},
    ]})
    assert group.member_probabilities == [0.5, 0.5]
    assert group.choose_member().name == "b"


@pytest.fixture
def resources(tmp_path):
    assets = tmp_path / "cozmo_resources" / "assets"
    clip = ae.AnimClip("anim_bored", [
        ae.AnimProceduralFace(trigger_time_ms=0, left_eye=[0] * 19, right_eye=[0] * 19),
        ae.AnimProceduralFace(trigger_time_ms=200, left_eye=[0] * 19, right_eye=[0] * 19),
        ae.AnimFaceAnimation(trigger_time_ms=100, anim_name="face_bored"),
        ae.AnimHeadAngle(trigger_time_ms=0, angle_deg=10),
    ])
    (assets / "animations").mkdir(parents=True)
    ae.AnimClips([clip]).to_fb_file(str(assets / "animations" / "anim_bored.bin"))
    faces = assets / "faceAnimations" / "face_bored"
    faces.mkdir(parents=True)
    for i in range(4):
        pixels = np.zeros((64, 128), np.uint8)
        if i != 2:      # frame 2 is empty
            pixels[10:20, 30 + i:60] = 255
        Image.fromarray(pixels).save(faces / "face_bored_{:05d}.png".format(i))
    (assets / "animationGroups" / "g").mkdir(parents=True)
    (assets / "animationGroups" / "g" / "ag_bored.json").write_text(json.dumps({"Animations": [
        {"Name": "anim_bored", "Weight": 1.0, "CooldownTime_Sec": 30.0, "Mood": "Default"}]}))
    (assets / "animationGroupMaps").mkdir()
    (assets / "animationGroupMaps" / "AnimationTriggerMap.json").write_text(json.dumps({"Pairs": [
        {"CladEvent": "Bored", "AnimName": "ag_bored"}]}))
    return tmp_path


def test_face_animation_frames_replace_procedural_faces(resources):
    lib = anim_library.AnimationLibrary(resources)
    frames = lib.preprocess("anim_bored").keyframes
    images = {t: [p for p in ps if isinstance(p, pe.DisplayImage)] for t, ps in frames.items()}
    # Procedural face at 0 kept; face animation frames at 100, 133, (167 empty), 200;
    # the procedural face at 200 falls within the face animation and is dropped.
    assert sorted(t for t, ps in images.items() if ps) == [0, 100, 133, 200]
    assert all(len(ps) == 1 for ps in images.values() if ps)
    assert any(isinstance(p, pe.AnimHead) for p in frames[0])
    # The face animation frame is not the procedural face.
    assert images[100][0].image != images[0][0].image


def test_missing_face_animation_is_skipped(resources, tmp_path):
    clip = ae.AnimClip("x", [ae.AnimFaceAnimation(trigger_time_ms=0, anim_name="missing")])
    assert anim.PreprocessedClip.from_anim_clip(clip, face_anim_dir=tmp_path).keyframes == {}


def test_library_triggers_and_cooldowns(resources):
    now = [0.0]
    lib = anim_library.AnimationLibrary(resources, clock=lambda: now[0])
    assert lib.clip_names() == ["anim_bored"] and lib.group_names() == ["ag_bored"]
    assert lib.triggers == {"Bored": "ag_bored"} and lib.cube_triggers == {}
    assert lib.choose("Bored") == "anim_bored"
    assert lib.cooldowns() == {"anim_bored": 30.0}
    # On cooldown, the only member is still returned by the fallback.
    assert lib.choose("ag_bored") == "anim_bored"
    assert lib.choose("Unknown") is None
    lib.reset_cooldowns()
    assert lib.cooldowns() == {}
