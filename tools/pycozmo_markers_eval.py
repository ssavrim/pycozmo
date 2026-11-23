#!/usr/bin/env python
"""

Measure the recognition of the markers on synthetic images made from the library of the prepared resources.

Each symbol is drawn with a random perspective, blur, noise and contrast, on a textured background, and
MarkerDetector must find its type and orientation. Images without marker, with frames that hold random patterns,
measure the false detections.

"""

import argparse
import collections
import time

import cv2
import numpy as np

from pycozmo import markers, util


def symbol_image(library, row, size=256):
    """ The marker of a row of the library: a black frame, a white gap and the symbol in the middle. """
    grid = int(round(len(library.centers_x) ** 0.5))
    symbol = library.probes[row].reshape(grid, grid).T          # probes are ordered x first, then y
    canvas = np.full((size, size), 255, np.uint8)
    lo, hi = int(0.209 * size), int(0.791 * size)
    canvas[:, :int(0.2 * size)] = 0
    canvas[:, int(0.8 * size):] = 0
    canvas[:int(0.2 * size), :] = 0
    canvas[int(0.8 * size):, :] = 0
    canvas[lo:hi, lo:hi] = cv2.resize(symbol, (hi - lo, hi - lo), interpolation=cv2.INTER_NEAREST)
    return canvas


def background(rng, shape):
    coarse = rng.randint(60, 220, (6, 8)).astype(np.uint8)
    base = cv2.resize(coarse, (shape[1], shape[0]), interpolation=cv2.INTER_CUBIC)
    texture = rng.normal(0, 6, shape)
    return np.clip(base + texture, 0, 255).astype(np.uint8)


def random_quad(rng, shape, side):
    """ The corners of a marker of about ``side`` pixels with a random turn, tilt and place. """
    angle = rng.uniform(-35, 35) * np.pi / 180
    tilt = rng.uniform(0, 0.25)
    pts = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], dtype=np.float64) * side / 2
    pts[:, 0] *= 1 - tilt * (pts[:, 1] / side)             # perspective: narrower at the top
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    pts = pts @ rot.T
    margin = side * 0.9
    center = [rng.uniform(margin, shape[1] - margin), rng.uniform(margin, shape[0] - margin)]
    return (pts + center).astype(np.float32)


def render(rng, marker, shape=(240, 320)):
    side = rng.uniform(40, 110)
    quad = random_quad(rng, shape, side)
    size = marker.shape[0]
    transform = cv2.getPerspectiveTransform(
        np.array([[0, 0], [size, 0], [size, size], [0, size]], dtype=np.float32), quad)
    image = background(rng, shape)
    warped = cv2.warpPerspective(marker, transform, (shape[1], shape[0]), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full(marker.shape, 255, np.uint8), transform, (shape[1], shape[0]))
    contrast = rng.uniform(0.55, 1.0)
    shade = rng.uniform(0, 40)
    marker_pixels = (warped.astype(np.float32) * contrast + shade).clip(0, 255)
    image[mask > 0] = marker_pixels[mask > 0].astype(np.uint8)
    sigma = rng.uniform(0.4, 1.2)
    image = cv2.GaussianBlur(image, (0, 0), sigma)
    image = np.clip(image + rng.normal(0, rng.uniform(2, 8), shape), 0, 255).astype(np.uint8)
    return image, quad


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", help="resource directory (default: the PyCozmo one)")
    parser.add_argument("--per-type", type=int, default=3, help="images per marker type")
    parser.add_argument("--negatives", type=int, default=60, help="images without marker")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    root = args.resources or str(util.get_cozmo_asset_dir())
    library = markers.load_marker_library(root)
    detector = markers.MarkerDetector(library)
    rng = np.random.RandomState(args.seed)

    # One row per label, in the orientation 0 or any orientation: the labels of the types that are not bad.
    row_of_label: dict[int, int] = {}
    for row, label in enumerate(library.labels):
        row_of_label.setdefault(int(label), row)
    by_type = collections.defaultdict(list)
    for label, row in row_of_label.items():
        marker_type = int(library.types[label])
        if label != 149 and marker_type != markers.MarkerType.INVALID:
            by_type[marker_type].append((label, row))

    results: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    started = time.monotonic()
    count = 0
    for marker_type, rows in sorted(by_type.items()):
        for _ in range(args.per_type):
            label, row = rows[rng.randint(len(rows))]
            image, _ = render(rng, symbol_image(library, row))
            found = detector.detect(image)
            count += 1
            outcome = "missed"
            if any(m.type == marker_type and m.orientation == library.orientations[label] for m in found):
                outcome = "right"
            elif any(m.type == marker_type for m in found):
                outcome = "wrong orientation"
            elif found:
                outcome = "wrong type"
            if found and outcome == "right" and len([m for m in found if m.type == marker_type]) > 1:
                outcome = "right (duplicate)"
            results[markers.MarkerType(marker_type).name][outcome] += 1
    positives_time = time.monotonic() - started

    false_detections = 0
    for _ in range(args.negatives):
        image = background(rng, (240, 320))
        # A frame with a random pattern inside, so that it looks like a marker but is none.
        pattern = (rng.rand(32, 32) > 0.5).astype(np.uint8) * 255
        canvas = np.full((256, 256), 255, np.uint8)
        canvas[:, :51] = 0
        canvas[:, 205:] = 0
        canvas[:51, :] = 0
        canvas[205:, :] = 0
        canvas[53:203, 53:203] = cv2.resize(pattern, (150, 150), interpolation=cv2.INTER_NEAREST)
        image, _ = render(rng, canvas)
        false_detections += len(detector.detect(image))

    total: collections.Counter = collections.Counter()
    for counter in results.values():
        total.update(counter)
    print("Positive images: {} ({:.2f} s per image)".format(count, positives_time / max(1, count)))
    for outcome, number in sorted(total.items()):
        print("  {:20s} {:4d}  {:5.1f} %".format(outcome, number, 100.0 * number / count))
    bad = {name: c for name, c in results.items() if c["right"] + c["right (duplicate)"] < sum(c.values())}
    if bad:
        print("Types with a failure:")
        for name, counter in sorted(bad.items()):
            print("  {:20s} {}".format(name, dict(counter)))
    print("Negative images: {}, false detections: {}".format(args.negatives, false_detections))


if __name__ == '__main__':
    main()
