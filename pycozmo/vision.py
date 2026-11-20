"""In-memory face detection with a bounded one-frame queue."""
import queue
import threading
import time

from . import logger


__all__ = ["FaceVision"]


class FaceVision:
    """Process camera images on a worker and deliver observations to a callback.

    The callback runs on the worker thread. It receives face rectangles, image
    width and height, a monotonic timestamp, the identified name (or None), and
    a motion score. Install the ``vision`` extra to use this class.
    """

    def __init__(self, on_detection, face_dir=None):
        import cv2
        self.cv2 = cv2
        self.detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        if self.detector.empty():
            raise RuntimeError('Face detection model not found.')
        if not hasattr(cv2, 'face'):
            raise RuntimeError('OpenCV face recognition module unavailable; install pycozmo[vision].')
        from .face_registry import FaceRegistry
        self.registry = FaceRegistry(cv2, root=face_dir)
        self.latest_lock = threading.Lock()
        self.latest_crop = None
        self.latest_at = 0
        self.on_detection = on_detection
        self.queue = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.previous_gray = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def submit(self, image):
        """Queue an image; return False if closed or a concurrent submit won."""
        if self.closed.is_set():
            return False
        try:
            self.queue.put_nowait(image.copy())
        except queue.Full:
            # Keep the newest frame when processing cannot keep up.
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(image.copy())
            except queue.Full:
                return False
        return True

    def run(self):
        import numpy as np
        while not self.closed.is_set():
            try:
                im = self.queue.get(timeout=.3)
            except queue.Empty:
                continue
            try:
                gray = self.cv2.cvtColor(np.asarray(im), self.cv2.COLOR_RGB2GRAY)
                small = self.cv2.resize(gray, (80, 60))
                small = self.cv2.GaussianBlur(small, (5, 5), 0)
                motion = 0.0
                if self.previous_gray is not None:
                    diff = self.cv2.absdiff(small, self.previous_gray)
                    motion = float((diff > 25).mean())
                self.previous_gray = small
                faces = self.detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(24, 24))
                result = [[int(n) for n in face] for face in faces]
                now = time.monotonic()
                crop = None
                identity = None
                if result:
                    x, y, w, h = max(result, key=lambda face: face[2] * face[3])
                    crop = self.cv2.resize(gray[y:y+h, x:x+w], (128, 128))
                    identity = self.registry.identify(crop)
                with self.latest_lock:
                    self.latest_crop, self.latest_at = crop, now
                self.on_detection(result, im.width, im.height, now, identity, motion)
            except Exception:
                # Do not refresh observation timestamps after a failed frame.
                # The supervisor will stop once the last valid frame is stale.
                with self.latest_lock:
                    self.latest_crop = None
                logger.exception('Face vision failed to process an image or callback.')

    def enroll(self, name):
        """Save the most recently observed face under the given name."""
        with self.latest_lock:
            if self.latest_crop is None or time.monotonic() - self.latest_at > 2:
                raise ValueError('No recent face in camera.')
            crop = self.latest_crop.copy()
        self.registry.enroll(name, crop)

    def close(self):
        """Stop processing. The method is safe to call from the callback."""
        self.closed.set()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
