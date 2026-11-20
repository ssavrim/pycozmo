import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import pytest
from PIL import Image
from pycozmo.vision import FaceVision


pytest.importorskip("cv2")


class BrokenDetector:
    def detectMultiScale(self, *args, **kwargs):
        raise RuntimeError('failed')


class VisionTests(unittest.TestCase):
    def test_busy_queue_keeps_newest_frame(self):
        import queue
        vision = FaceVision.__new__(FaceVision)
        vision.queue = queue.Queue(maxsize=1)
        vision.closed = threading.Event()
        vision.submit(Image.new('RGB', (1, 1), 'red'))
        vision.submit(Image.new('RGB', (1, 1), 'blue'))
        self.assertEqual(vision.queue.get_nowait().getpixel((0, 0)), (0, 0, 255))

    def test_detected_face_reaches_callback(self):
        class Detector:
            def detectMultiScale(self, *args, **kwargs):
                return [(10, 10, 30, 30)]
        results = []
        ready = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            vision = FaceVision(lambda *args: (results.append(args), ready.set()), face_dir=tmp)
            try:
                vision.detector = Detector()
                vision.submit(Image.new('RGB', (100, 100)))
                self.assertTrue(ready.wait(3))
                self.assertEqual(results[0][:3], ([[10, 10, 30, 30]], 100, 100))
                self.assertIsNone(results[0][4])
            finally:
                vision.close()

    def test_callback_can_close_vision_and_submit_after_close_returns_false(self):
        ready = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            def on_detection(*_args):
                vision.close()
                ready.set()

            vision = FaceVision(on_detection, face_dir=tmp)
            try:
                vision.submit(Image.new('RGB', (100, 100)))
                self.assertTrue(ready.wait(3))
                self.assertFalse(vision.submit(Image.new('RGB', (100, 100))))
            finally:
                vision.close()

    def test_failed_frame_does_not_refresh_observation(self):
        events = []
        logged = threading.Event()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PYCOZMO_FACE_DIR': tmp}), \
                patch('pycozmo.vision.logger.exception', side_effect=lambda *_args: logged.set()):
            vision = FaceVision(lambda *args: events.append(args))
            try:
                vision.detector = BrokenDetector()
                vision.submit(Image.new('RGB', (100, 100)))
                self.assertTrue(logged.wait(3))
                self.assertEqual(events, [])
                self.assertIsNone(vision.latest_crop)
            finally:
                vision.close()


if __name__ == '__main__':
    unittest.main()
