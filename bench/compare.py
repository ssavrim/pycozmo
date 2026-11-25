#!/usr/bin/env python3
"""
Benchmark of identical workloads on two PyCozmo trees (offline, no robot).

Run one tree:
    python bench/compare.py run --root /path/to/tree [--repeat 7] > result.json

Compare two trees (each with its own Python interpreter, e.g. two virtual environments):
    python bench/compare.py compare \\
        --baseline /venvs/master/bin/python:/path/to/master \\
        --candidate /venvs/rust/bin/python:/path/to/branch

Each workload is timed `repeat` times; the median time per operation is reported, with the ratio
baseline / candidate (above 1 means that the candidate is faster).
"""

import argparse
import io
import json
import os
import random
import statistics
import subprocess
import sys
import time


def workloads():
    """ Returns (name, number of operations per run, setup function returning the run function). """
    import pycozmo
    from pycozmo import protocol_encoder as pe, protocol_declaration as decl
    from pycozmo.frame import Frame
    from pycozmo.image_encoder import ImageEncoder, ImageDecoder
    from pycozmo.anim_encoder import AnimClips
    from pycozmo.procedural_face import ProceduralFace
    from pycozmo.emotions import DecayGraph, EmotionType, Node
    from pycozmo.window import ReceiveWindow, SendWindow
    from PIL import Image, ImageDraw

    rng = random.Random(42)

    def sample_packets():
        return [
            pe.RobotState(timestamp=12345, pose_x=1.5, pose_y=-2.0, pose_angle_rad=0.3, head_angle_rad=0.1,
                          battery_voltage=4.1, status=0x1234, cliff_data_raw=(100, 200, 300, 400)),
            pe.AnimHead(duration_ms=100, angle_deg=-10),
            pe.AnimLift(duration_ms=200, height_mm=50),
            pe.DriveWheels(lwheel_speed_mmps=50.0, rwheel_speed_mmps=-50.0),
            pe.SetHeadAngle(angle_rad=0.4, max_speed_rad_per_sec=10.0, accel_rad_per_sec2=10.0),
            pe.OutputAudio(samples=bytes(rng.randrange(256) for _ in range(744))),
            pe.DisplayImage(image=bytes(rng.randrange(256) for _ in range(300))),
            pe.LightStateCenter(states=[pe.LightState(on_color=0x7fff, off_color=0x001f)] * 3),
            pe.AnimationState(timestamp=1, num_anim_bytes_played=2, num_audio_frames_played=3),
            pe.ImageChunk(frame_timestamp=1, image_id=2, chunk_id=3, data=bytes(rng.randrange(256) for _ in range(600))),
        ]

    def protocol_encode():
        pkts = sample_packets()

        def run():
            for p in pkts:
                p.to_bytes()
        return run

    def protocol_decode():
        pkts = [(type(p), p.to_bytes()) for p in sample_packets()]

        def run():
            for cls, data in pkts:
                cls.from_bytes(data)
        return run

    def protocol_construct():
        def run():
            pe.RobotState(timestamp=12345, pose_x=1.5, pose_y=-2.0, pose_angle_rad=0.3, head_angle_rad=0.1,
                          battery_voltage=4.1, status=0x1234, cliff_data_raw=(100, 200, 300, 400))
            pe.DriveWheels(50.0, -50.0, 0.0, 0.0)
            pe.SetHeadAngle(angle_rad=0.4)
            pe.AnimHead(duration_ms=100, angle_deg=-10)
            pe.AnimBody(speed=10)
        return run

    def frame_pack():
        pkts = sample_packets()[1:6]
        for i, p in enumerate(pkts):
            p.seq = i

        def run():
            Frame(decl.FrameType.ENGINE, 100, 104, 7, pkts).to_bytes()
        return run

    def frame_unpack():
        robot_pkts = [sample_packets()[0], sample_packets()[8], sample_packets()[9]]
        raw = Frame(decl.FrameType.ROBOT, 10, 0xffff, 20, robot_pkts).to_bytes()

        def run():
            Frame.from_bytes(raw)
        return run

    def images():
        res = []
        for i in range(20):
            im = Image.new("1", (128, 32), color=0)
            draw = ImageDraw.Draw(im)
            draw.text((rng.randint(0, 60), rng.randint(0, 16)), "Cozmo {}".format(i), fill=1)
            draw.ellipse(((rng.randint(0, 100), 2), (rng.randint(100, 127), 30)), outline=1)
            res.append(im)
        return res

    def image_encode():
        ims = images()

        def run():
            for im in ims:
                ImageEncoder(im).encode()
        return run

    def image_decode():
        bufs = [bytes(ImageEncoder(im).encode()) for im in images()]

        def run():
            for b in bufs:
                ImageDecoder(b).decode()
        return run

    def clips():
        from pycozmo import anim_encoder as ae
        res = []
        for c in range(20):
            kfs = []
            for t in range(0, 3000, 33):
                kfs.append(ae.AnimProceduralFace(trigger_time_ms=t, angle=rng.uniform(-5, 5),
                                                 left_eye=[rng.random() for _ in range(19)],
                                                 right_eye=[rng.random() for _ in range(19)]))
                if t % 99 == 0:
                    kfs.append(ae.AnimHeadAngle(trigger_time_ms=t, duration_ms=99, angle_deg=rng.randint(-20, 40)))
                    kfs.append(ae.AnimLiftHeight(trigger_time_ms=t, duration_ms=99, height_mm=rng.randint(0, 90)))
                if t % 330 == 0:
                    kfs.append(ae.AnimRobotAudio(trigger_time_ms=t, audio_event_ids=[rng.randint(0, 2 ** 31)],
                                                 probabilities=[1.0]))
                    kfs.append(ae.AnimEvent(trigger_time_ms=t, event_id="TAPPED_BLOCK"))
            res.append(ae.AnimClip("anim_bench_{:02d}".format(c), kfs))
        return AnimClips(res)

    def anim_encode():
        c = clips()

        def run():
            c.to_fb_stream(io.BytesIO())
        return run

    def anim_decode():
        f = io.BytesIO()
        clips().to_fb_stream(f)
        data = f.getvalue()

        def run():
            AnimClips.from_fb_stream(io.BytesIO(data))
        return run

    def face_render():
        faces = []
        for _ in range(10):
            f = ProceduralFace()
            f.center_x = rng.uniform(-10, 10)
            f.angle = rng.uniform(-10, 10)
            for e in f.eyes:
                e.scale_x = rng.uniform(0.8, 1.2)
                e.angle = rng.uniform(-15, 15)
                e.lids[0].y = rng.uniform(0, 0.4)
                e.lids[1].bend = rng.uniform(0, 0.5)
            faces.append(f)

        def run():
            for f in faces:
                f.render()
        return run

    def mood_decay():
        now = [0.0]
        graph = DecayGraph([Node(0, 1), Node(10, 1), Node(60, 0.6), Node(100, 0)])
        emotions = [EmotionType("e{}".format(i), graph, graph, clock=lambda: now[0]) for i in range(9)]
        for e in emotions:
            e.add(0.8)

        def run():
            now[0] += 0.033
            for e in emotions:
                e.update()
        return run

    def decay_graph_evaluate():
        graph = DecayGraph([Node(0, 1), Node(10, 1), Node(60, 0.6), Node(100, 0)])

        def run():
            for x in (-1.0, 5.0, 40.0, 60.0, 200.0):
                graph.evaluate(x)
        return run

    def windows():
        def run():
            r = ReceiveWindow(16, size=62, max_seq=0xfffe)
            s = SendWindow(16, size=62, max_seq=0xfffe)
            for i in range(60):
                r.put(i, i)
                s.put(i)
            while r.get() is not None:
                pass
            s.acknowledge(59)
        return run

    del pycozmo
    return [
        ("protocol: construct 5 messages", 5, protocol_construct),
        ("protocol: encode 10 messages", 10, protocol_encode),
        ("protocol: decode 10 messages", 10, protocol_decode),
        ("frame: pack 5 packets", 1, frame_pack),
        ("frame: unpack robot frame (3 packets)", 1, frame_unpack),
        ("window: 60 packets through send/receive windows", 1, windows),
        ("image: encode 20 display images", 20, image_encode),
        ("image: decode 20 display images", 20, image_decode),
        ("anim: encode 20 clips (FlatBuffers)", 1, anim_encode),
        ("anim: decode 20 clips (FlatBuffers)", 1, anim_decode),
        ("procedural face: render 10 faces", 10, face_render),
        ("mood: decay of 9 emotions", 1, mood_decay),
        ("decay graph: 5 evaluations", 5, decay_graph_evaluate),
    ]


def run_tree(root, repeat, min_time):
    sys.path.insert(0, root)
    results = []
    for name, ops, setup in workloads():
        fn = setup()
        fn()
        # Number of calls per sample so that a sample lasts about min_time.
        n = 1
        while True:
            t0 = time.perf_counter()
            for _ in range(n):
                fn()
            dt = time.perf_counter() - t0
            if dt >= min_time:
                break
            n *= 2
        samples = []
        for _ in range(repeat):
            t0 = time.perf_counter()
            for _ in range(n):
                fn()
            samples.append((time.perf_counter() - t0) / (n * ops))
        results.append({"name": name, "median_s": statistics.median(samples), "min_s": min(samples),
                        "samples": len(samples), "calls": n})
    return results


def fmt(s):
    for unit, k in (("s", 1), ("ms", 1e3), ("us", 1e6), ("ns", 1e9)):
        if s * k >= 1:
            return "{:.3g} {}".format(s * k, unit)
    return "{:.3g} ns".format(s * 1e9)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", required=True)
    r.add_argument("--repeat", type=int, default=7)
    r.add_argument("--min-time", type=float, default=0.1)
    c = sub.add_parser("compare")
    c.add_argument("--baseline", required=True, help="PYTHON:ROOT")
    c.add_argument("--candidate", required=True, help="PYTHON:ROOT")
    c.add_argument("--repeat", type=int, default=7)
    c.add_argument("--min-time", type=float, default=0.1)
    c.add_argument("--json", help="also write the results to this file")
    args = ap.parse_args()

    if args.cmd == "run":
        json.dump(run_tree(os.path.abspath(args.root), args.repeat, args.min_time), sys.stdout)
        return

    def run(spec):
        python, root = spec.split(":", 1)
        out = subprocess.run([python, os.path.abspath(__file__), "run", "--root", root, "--repeat",
                              str(args.repeat), "--min-time", str(args.min_time)],
                             check=True, capture_output=True, text=True, cwd=root)
        return json.loads(out.stdout)

    base = run(args.baseline)
    cand = run(args.candidate)
    rows = []
    print("| Workload | Baseline (median per op) | Candidate (median per op) | Speed-up |")
    print("|---|---:|---:|---:|")
    for b, c in zip(base, cand):
        ratio = b["median_s"] / c["median_s"]
        rows.append({"name": b["name"], "baseline_s": b["median_s"], "candidate_s": c["median_s"], "ratio": ratio})
        print("| {} | {} | {} | {:.1f}x |".format(b["name"], fmt(b["median_s"]), fmt(c["median_s"]), ratio))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
