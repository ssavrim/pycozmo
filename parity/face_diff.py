#!/usr/bin/env python3
"""
Differential check of the procedural face renderer (ProceduralFace.render()): run on master and on this branch with
the same seed, then compare the outputs (see protocol_diff.py). Prints a hash of the pixels of each rendered face.

Faces: the default face, random parameters in the ranges of the animations (eye radii, lids, scales, angles), extreme
values (tiny and large scales, angles that are multiples of 90 degrees), and the faces of ProceduralFaceGenerator.
"""

import argparse
import hashlib
import random
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=3000)
    args = ap.parse_args()
    sys.path.insert(0, args.root)
    from pycozmo.procedural_face import ProceduralFace

    rng = random.Random(args.seed)
    out = sys.stdout

    def show(i, params):
        try:
            im = ProceduralFace(list(params)).render()
            res = hashlib.sha1(im.tobytes()).hexdigest() + " " + str(im.tobytes().count(0) if False else sum(bin(b).count("1") for b in im.tobytes()))
        except Exception as e:      # noqa: BLE001 - the exception is the result being compared.
            res = "exc {}".format(type(e).__name__)
        out.write("{} {}\n".format(i, res))

    default = ProceduralFace().params
    show("default", default)
    for i in range(args.count):
        p = list(default)
        mode = i % 4
        for j in range(43):
            if mode == 0 and rng.random() < 0.3 and j not in (2, 3):
                p[j] = rng.uniform(-1, 1) * rng.choice([1, 2, 10])
            elif mode == 1:
                if j in (0, 1, 5, 6, 24, 25):          # centers
                    p[j] = rng.uniform(-60, 60)
                elif j in (2, 3, 7, 8, 26, 27):        # scales
                    p[j] = rng.choice([1.0, rng.uniform(0, 2), 0.01, 15.0])
                elif j in (4, 9, 28):                  # angles
                    p[j] = rng.choice([0.0, 90.0, 180.0, 270.0, -90.0, rng.uniform(-45, 45), 360.0])
                elif 10 <= j < 18 or 29 <= j < 37:     # radii
                    p[j] = rng.uniform(0, 1)
                else:                                  # lids: y, angle, bend
                    k = (j - 18) % 3 if j < 24 else (j - 37) % 3
                    p[j] = [rng.uniform(-0.2, 1.0), rng.uniform(-30, 30), rng.uniform(-1, 1)][k]
            elif mode == 2 and rng.random() < 0.5:
                p[j] = rng.choice([0, 1, -1, 0.5, 1e-9, 3.0, 45.0, 0.25])
        if mode == 3:
            # Mild changes of every parameter around the default face (most faces are not blank).
            p = [v + rng.uniform(-0.3, 0.3) * (1 if j % 19 not in (5, 6) else 20) for j, v in enumerate(default)]
            p[2] = abs(p[2]) + 0.3
            p[3] = abs(p[3]) + 0.3
        show(i, p)


if __name__ == "__main__":
    main()
