"""
Animation stream messages and their encoding from animation keyframes.

Specification: docs/specs/animation-stream.md. Head and lift durations are 16-bit,
AnimBody carries a curvature radius, and animation events travel to the robot (0x95)
and back (0xd5).
"""

import random
import struct

import pytest

import pycozmo
from pycozmo import anim, anim_encoder as ae, event, protocol_encoder as pe


def packets(clip, **kwargs):
    return anim.PreprocessedClip.from_anim_clip(ae.AnimClip("test", clip), **kwargs).keyframes


def test_head_and_lift_carry_16_bit_durations():
    head = pe.AnimHead(duration_ms=4500, angle_deg=-20)
    assert head.id == 0x93 and struct.unpack("<Hb", head.to_bytes()) == (4500, -20)
    lift = pe.AnimLift(duration_ms=8900, height_mm=92)
    assert lift.id == 0x94 and struct.unpack("<HB", lift.to_bytes()) == (8900, 92)
    # Durations below 256 ms keep the byte layout used before (low byte, then 0).
    assert pe.AnimHead(duration_ms=200, angle_deg=5).to_bytes() == bytes([200, 0, 5])


def test_anim_body_layout_and_default():
    assert pe.AnimBody().id == 0x99
    assert struct.unpack("<hh", pe.AnimBody(speed=-50, curvature_radius_mm=-300).to_bytes()) == (-50, -300)
    assert pe.AnimBody().curvature_radius_mm == anim.BODY_STRAIGHT_RADIUS_MM


def test_animation_event_messages():
    assert pe.AnimEvent(event_id=pe.AnimEventType.TAPPED_BLOCK).to_bytes() == b"\x02"
    assert pe.AnimEvent().id == 0x95
    received = pe.AnimationEvent.from_bytes(struct.pack("<IBB", 123456, 7, 0))
    assert received.id == 0xd5 and len(received) == 6
    assert (received.timestamp, received.anim_id, received.event_id) == (
        123456, 7, pe.AnimEventType.DEVICE_AUDIO_TRIGGER)


def test_long_head_and_lift_keyframes_are_not_truncated():
    frames = packets([ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=4500, angle_deg=30),
                      ae.AnimLiftHeight(trigger_time_ms=0, duration_ms=8900, height_mm=50)])
    head, lift = frames[0]
    assert (head.duration_ms, head.angle_deg) == (4500, 30)
    assert (lift.duration_ms, lift.height_mm) == (8900, 50)


def test_variability_is_drawn_in_range_and_clamped():
    rng = random.Random(1)
    angles = {packets([ae.AnimHeadAngle(trigger_time_ms=0, angle_deg=10, variability_deg=5)], rng=rng)[0][0].angle_deg
              for _ in range(200)}
    assert angles == set(range(5, 16))
    heights = {packets([ae.AnimLiftHeight(trigger_time_ms=0, height_mm=2, variability_mm=10)], rng=rng)[0][0].height_mm
               for _ in range(200)}
    assert min(heights) == 0 and max(heights) == 12


@pytest.mark.parametrize("radius, speed, expected", [
    ("STRAIGHT", 100, (100, 0x7fff)),
    ("STRAIGHT", 500, (220, 0x7fff)),
    ("TURN_IN_PLACE", -250, (-250, 0)),
    ("TURN_IN_PLACE", 400, (300, 0)),
    ("POINT_TURN", 90, (90, 0)),
    (5000.0, 100, (100, 5000)),
    (-27450.0, -40, (-40, -27450)),
    (40000.0, 50, (50, 0x7fff)),
    (-40000.0, 50, (50, -0x8000)),
    ("120", 30, (30, 120)),
])
def test_body_motion_command(radius, speed, expected):
    assert anim.body_motion_command(radius, speed) == expected


def test_body_motion_is_sent_as_anim_body_and_stopped():
    frames = packets([ae.AnimBodyMotion(trigger_time_ms=100, duration_ms=500, radius_mm=5000.0, speed=100)])
    start, = frames[100]
    stop, = frames[600]
    assert isinstance(start, pe.AnimBody) and (start.speed, start.curvature_radius_mm) == (100, 5000)
    assert isinstance(stop, pe.AnimBody) and (stop.speed, stop.curvature_radius_mm) == (0, 0x7fff)
    # No wheel speeds are computed on the host any more.
    assert not any(isinstance(p, (pe.DriveWheels, pe.TurnInPlaceAtSpeed)) for ps in frames.values() for p in ps)


def test_event_keyframes_are_streamed():
    frames = packets([ae.AnimEvent(trigger_time_ms=330, event_id="TAPPED_BLOCK"),
                      ae.AnimEvent(trigger_time_ms=400, event_id="NOT_AN_EVENT")])
    pkt, = frames[330]
    assert isinstance(pkt, pe.AnimEvent) and pkt.event_id == pe.AnimEventType.TAPPED_BLOCK
    assert 400 not in frames


def test_client_dispatches_events_of_the_current_animation():
    client = pycozmo.Client()
    received = []
    client.add_handler(event.EvtAnimationEvent, lambda cli, event_id, timestamp: received.append((event_id, timestamp)))
    # Before any animation is started by the client, events are passed through.
    client._on_animation_event(None, pe.AnimationEvent(timestamp=10, anim_id=3, event_id=2))
    client._current_anim_id = 4
    client._on_animation_event(None, pe.AnimationEvent(timestamp=20, anim_id=3, event_id=0))
    client._on_animation_event(None, pe.AnimationEvent(timestamp=30, anim_id=4, event_id=1))
    assert received == [(pe.AnimEventType.TAPPED_BLOCK, 10), (pe.AnimEventType.ENERGY_DRAINCUBE_END, 30)]
