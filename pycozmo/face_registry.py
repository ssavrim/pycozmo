"""Explicit local enrollment using OpenCV LBPH; no data leaves this machine."""
import os
from pathlib import Path
import threading
import uuid


__all__ = ["FaceRegistry"]


class FaceRegistry:
    """Manage explicitly enrolled face samples stored on the local machine."""
    def __init__(self, cv2, root=None):
        self.cv2 = cv2
        self.root = Path(root or os.environ.get('PYCOZMO_FACE_DIR', Path.home() / '.local/share/pycozmo/faces'))
        self.lock = threading.Lock()
        self.labels = {}
        self.model = None
        self._train()

    def _train(self):
        import numpy as np
        faces, labels, names = [], [], {}
        if self.root.is_dir():
            for num, folder in enumerate(sorted(self.root.iterdir())):
                if not folder.is_dir():
                    continue
                try:
                    name = (folder / 'name.txt').read_text(encoding='utf-8').strip()
                    sample = self.cv2.imread(str(folder / 'face.png'), self.cv2.IMREAD_GRAYSCALE)
                    if not name or sample is None:
                        continue
                    faces.append(sample)
                    labels.append(num)
                    names[num] = name
                except OSError:
                    continue
        model = None
        if faces:
            model = self.cv2.face.LBPHFaceRecognizer_create()
            model.train(faces, np.asarray(labels, dtype=np.int32))
        with self.lock:
            self.labels, self.model = names, model

    def enroll(self, name, crop):
        """Persist one grayscale face crop and retrain the recognition model."""
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ValueError('Name must contain 1 to 40 characters.')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = self.root / uuid.uuid4().hex
        folder.mkdir(mode=0o700)
        try:
            (folder / 'name.txt').write_text(name.strip(), encoding='utf-8')
            os.chmod(folder / 'name.txt', 0o600)
            if not self.cv2.imwrite(str(folder / 'face.png'), crop):
                raise OSError('Unable to save face image.')
            os.chmod(folder / 'face.png', 0o600)
            self._train()
        except Exception:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def identify(self, crop):
        """Return the matched name, or None when no sufficiently close match exists."""
        with self.lock:
            if self.model is None:
                return None
            label, distance = self.model.predict(crop)
            return self.labels.get(label) if distance < 60 else None

    def clear(self):
        """Delete managed samples while preserving unrelated files in the directory."""
        import shutil
        if self.root.is_dir():
            for folder in self.root.iterdir():
                try:
                    if uuid.UUID(folder.name, version=4).hex != folder.name:
                        continue
                except ValueError:
                    continue
                if folder.is_symlink() or not folder.is_dir():
                    continue
                if {p.name for p in folder.iterdir()} <= {'name.txt', 'face.png'}:
                    shutil.rmtree(folder)
            try:
                self.root.rmdir()
            except OSError:
                pass  # Preserve unrelated files and nonempty directories.
        self._train()
