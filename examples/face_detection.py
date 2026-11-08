#!/usr/bin/env python
"""Report faces from Cozmo's camera; install pycozmo with the vision extra."""
import time

import pycozmo
from pycozmo.vision import FaceVision


def on_detection(faces, width, height, timestamp, name, motion):
    if faces:
        print(f"Faces: {faces}, image: {width}x{height}, identity: {name or 'unknown'}")


with pycozmo.connect() as cli:
    vision = FaceVision(on_detection)
    handler = cli.add_handler(pycozmo.event.EvtNewRawCameraImage,
                              lambda _cli, image: vision.submit(image))
    try:
        cli.enable_camera(enable=True)
        print("Observing for 30 seconds...")
        time.sleep(30)
    finally:
        cli.enable_camera(enable=False)
        cli.del_handler(pycozmo.event.EvtNewRawCameraImage, handler)
        vision.close()
