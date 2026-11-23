#!/usr/bin/env python
"""Recognize the markers of the cubes and of the charger in the camera images (needs the prepared resources)."""
import time

import numpy as np

import pycozmo
from pycozmo import markers


library = markers.load_marker_library(str(pycozmo.util.get_cozmo_asset_dir()))
detector = markers.MarkerDetector(library)
last = [0.0]


def on_image(cli, image):
    if time.monotonic() - last[0] < 0.5:
        return
    last[0] = time.monotonic()
    found = detector.detect(np.asarray(image.convert("L")))
    print(", ".join("{} at ({:.0f}, {:.0f}), distance {}".format(m.name, *m.center, m.distance) for m in found)
          or "no marker")


with pycozmo.connect(enable_procedural_face=False) as cli:
    cli.set_head_angle(0.0)
    cli.add_handler(pycozmo.event.EvtNewRawCameraImage, on_image)
    cli.enable_camera(True)
    time.sleep(30)
