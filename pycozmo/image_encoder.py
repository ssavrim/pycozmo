"""

Cozmo image run-length encoding and decoding.

ImageDecoder and ImageEncoder are implemented in Rust (crates/cozmo-core/src/image.rs, crates/cozmo-py/src/image.rs).
The text helpers below stay in Python.

"""

import sys
from io import StringIO

from PIL import Image

from ._cozmo import ImageDecoder, ImageEncoder


__all__ = [
    "render",
    "image_to_str",
    "str_to_image",

    "ImageDecoder",
    "ImageEncoder",
]


def render(image: bytes) -> None:
    for y in range(32):
        sys.stdout.write("{:3d}".format(y))
        for x in range(128):
            if ((x+1) % 10) == 1:
                sys.stdout.write(" ")
            sys.stdout.write(chr(ord("0") + image[y*128 + x])) if image[y*128 + x] else sys.stdout.write(".")
        sys.stdout.write("\n")


def image_to_str(image):
    s = StringIO()
    for y in range(32):
        for x in range(128):
            if image[y * 128 + x]:
                s.write(chr(ord("0") + image[y * 128 + x]))
            else:
                s.write(".")
        s.write("\n")
    return s.getvalue()


def str_to_image(sim: str) -> Image.Image:
    s = StringIO(sim)
    im = Image.new("1", (128, 32), color=0)
    px = im.load()
    if px is None:
        raise ValueError("Failed to access image pixels.")
    for y in range(32):
        while True:
            line = s.readline().strip()
            if line:
                break
        x = 0
        for c in line:
            px[x, y] = 1 if c and c != "." else 0
            x += 1
    return im
