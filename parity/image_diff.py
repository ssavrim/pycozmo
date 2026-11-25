#!/usr/bin/env python3
"""
Differential check of the display image codec (pycozmo.image_encoder): run on master and on this branch with the same
seed, then compare the outputs (see protocol_diff.py).

Cases: encoding of random images (noise of several densities, shapes, text) and decoding of the result; decoding of
random byte sequences (decoder state or exception).
"""

import argparse
import hashlib
import random
import sys


def describe(fn):
    try:
        return "ok " + str(fn())
    except Exception as e:      # noqa: BLE001 - the exception is the result being compared.
        return "exc {}: {}".format(type(e).__name__, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=500)
    args = ap.parse_args()
    sys.path.insert(0, args.root)
    from PIL import Image, ImageDraw
    from pycozmo.image_encoder import ImageDecoder, ImageEncoder

    rng = random.Random(args.seed)
    out = sys.stdout
    for i in range(args.count):
        im = Image.new("1", (128, 32), color=0)
        kind = i % 4
        if kind == 0:
            density = rng.choice([0.01, 0.1, 0.5, 0.9])
            for y in range(32):
                for x in range(128):
                    if rng.random() < density:
                        im.putpixel((x, y), 1)
        else:
            draw = ImageDraw.Draw(im)
            for _ in range(rng.randint(1, 6)):
                x0, y0 = rng.randint(-10, 130), rng.randint(-5, 35)
                x1, y1 = x0 + rng.randint(0, 60), y0 + rng.randint(0, 30)
                if kind == 1:
                    draw.rectangle(((x0, y0), (x1, y1)), fill=1)
                elif kind == 2:
                    draw.ellipse(((x0, y0), (x1, y1)), fill=rng.randint(0, 1), outline=1)
                else:
                    draw.text((x0, y0), "Cozmo {}".format(rng.randint(0, 999)), fill=1)
        enc = describe(lambda: bytes(ImageEncoder(im).encode()).hex())
        out.write("enc {} {}\n".format(i, enc))
        if enc.startswith("ok "):
            def dec():
                d = ImageDecoder(bytes.fromhex(enc[3:]))
                d.decode()
                same = all((d.image[y * 128 + x] != 0) == (im.getpixel((x, y)) != 0)
                           for y in range(32) for x in range(128))
                return "{} {} {}".format(d.x, d.y, same)
            out.write("  dec {}\n".format(describe(dec)))
    for i in range(args.count * 4):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 80)))

        def dec_random():
            d = ImageDecoder(data)
            try:
                d.decode()
            finally:
                out.write("  state {} {} {} {} {}\n".format(d.x, d.y, d.last_draw, d.repeat_column_shift,
                                                           hashlib.sha1(bytes(d.image)).hexdigest()))
            return "done"
        out.write("rand {} {}\n".format(data.hex(), describe(dec_random)))


if __name__ == "__main__":
    main()
