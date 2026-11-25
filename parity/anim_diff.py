#!/usr/bin/env python3
"""
Differential check of the animation FlatBuffers codec (AnimClips.to_fb_stream() and from_fb_stream()): run on master
and on this branch with the same seed, then compare the outputs (see protocol_diff.py).

Random clips are built from JSON-like data with every keyframe type, encoded (the bytes are printed), decoded again
(the clips are printed as JSON), and truncated or corrupted data is decoded (result or exception type).
"""

import argparse
import io
import json
import random
import sys


def random_clip(rng, i):
    def t():
        return rng.choice([0, rng.randint(0, 10000), 2 ** 32 - 1])
    kfs = {k: [] for k in ("LiftHeightKeyFrame", "ProceduralFaceKeyFrame", "HeadAngleKeyFrame", "RobotAudioKeyFrame",
                           "BackpackLightsKeyFrame", "FaceAnimationKeyFrame", "EventKeyFrame", "BodyMotionKeyFrame",
                           "RecordHeadingKeyFrame", "TurnToRecordedHeadingKeyFrame")}
    for _ in range(rng.randint(0, 25)):
        k = rng.choice(list(kfs))
        if k == "LiftHeightKeyFrame":
            d = {"triggerTime_ms": t(), "durationTime_ms": t(), "height_mm": rng.randint(0, 255),
                 "heightVariability_mm": rng.choice([0, rng.randint(0, 255)])}
        elif k == "ProceduralFaceKeyFrame":
            d = {"triggerTime_ms": t(), "faceAngle": rng.choice([0.0, rng.uniform(-30, 30)]),
                 "faceCenterX": rng.uniform(-20, 20), "faceCenterY": rng.choice([0, 1.5]),
                 "faceScaleX": rng.choice([1.0, rng.uniform(0, 2)]), "faceScaleY": rng.choice([1, 0.25]),
                 "leftEye": [rng.choice([0.0, rng.uniform(-1, 1)]) for _ in range(19)],
                 "rightEye": [rng.choice([0, rng.uniform(-1, 1)]) for _ in range(19)]}
        elif k == "HeadAngleKeyFrame":
            d = {"triggerTime_ms": t(), "durationTime_ms": t(), "angle_deg": rng.randint(-128, 127),
                 "angleVariability_deg": rng.choice([0, 3])}
        elif k == "RobotAudioKeyFrame":
            d = {"triggerTime_ms": t(), "audioEventId": [rng.randint(-2 ** 63, 2 ** 63 - 1)
                                                          for _ in range(rng.randint(0, 4))],
                 "volume": rng.choice([1.0, rng.random()]),
                 "probability": [rng.random() for _ in range(rng.randint(0, 3))],
                 "hasAlts": rng.choice([True, False])}
        elif k == "BackpackLightsKeyFrame":
            d = {"triggerTime_ms": t(), "durationTime_ms": t()}
            for led in ("Left", "Front", "Middle", "Back", "Right"):
                d[led] = [rng.randint(0, 255) for _ in range(4)]
        elif k == "FaceAnimationKeyFrame":
            d = {"triggerTime_ms": t(), "animName": rng.choice(["", "face_happy", "é€x" * rng.randint(1, 3)])}
        elif k == "EventKeyFrame":
            d = {"triggerTime_ms": t(), "event_id": rng.choice(["TAPPED_BLOCK", "x" * rng.randint(0, 9)])}
        elif k == "BodyMotionKeyFrame":
            d = {"triggerTime_ms": t(), "durationTime_ms": t(),
                 "radius_mm": rng.choice(["STRAIGHT", "TURN_IN_PLACE", "120", rng.uniform(-500, 500)]),
                 "speed": rng.randint(-32768, 32767)}
        elif k == "RecordHeadingKeyFrame":
            d = {"triggerTime_ms": t()}
        else:
            d = {"triggerTime_ms": t(), "durationTime_ms": t(), "offset_deg": rng.choice([0, -90]),
                 "speed_degPerSec": rng.randint(-500, 500), "accel_degPerSec2": rng.choice([1000, 50]),
                 "decel_degPerSec2": rng.choice([1000, 70]), "tolerance_deg": rng.choice([2, 5]),
                 "numHalfRevs": rng.choice([0, 3]), "useShortestDir": rng.choice([True, False])}
        kfs[k].append(d)
    return {"Name": "anim_{}_{}".format(i, rng.randint(0, 99)), "keyframes": kfs}


def describe(fn):
    try:
        return "ok " + str(fn())
    except Exception as e:      # noqa: BLE001 - the exception is the result being compared.
        return "exc {}".format(type(e).__name__)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=200)
    args = ap.parse_args()
    sys.path.insert(0, args.root)
    from pycozmo.anim_encoder import AnimClips

    rng = random.Random(args.seed)
    out = sys.stdout
    for i in range(args.count):
        data = {"clips": [random_clip(rng, j) for j in range(rng.randint(0, 4))]}
        clips = AnimClips.from_dict(data)
        f = io.BytesIO()
        clips.to_fb_stream(f)
        buf = f.getvalue()
        out.write("enc {} {}\n".format(i, buf.hex()))
        back = AnimClips.from_fb_stream(io.BytesIO(buf))
        out.write("dec {} {}\n".format(i, json.dumps(back.to_dict(), sort_keys=True)))
        for _ in range(3):
            bad = bytearray(buf)
            if bad and rng.random() < 0.5:
                bad = bad[:rng.randrange(len(bad))]
            elif bad:
                bad[rng.randrange(len(bad))] = rng.randrange(256)
            out.write("bad {}\n".format(describe(
                lambda: len(AnimClips.from_fb_stream(io.BytesIO(bytes(bad))).clips))))


if __name__ == "__main__":
    main()
