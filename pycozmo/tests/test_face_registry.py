import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pytest


pytest.importorskip("cv2")


class FaceRegistryTest(unittest.TestCase):
    def test_enroll_identify_delete(self):
        import cv2
        import numpy as np
        from pycozmo.face_registry import FaceRegistry
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'PYCOZMO_FACE_DIR': directory + '/faces'}):
            registry = FaceRegistry(cv2)
            image = np.tile(np.arange(128, dtype=np.uint8), (128, 1))
            self.assertIsNone(registry.identify(image))
            registry.enroll('Test', image)
            self.assertEqual(registry.identify(image), 'Test')
            registry.clear()
            self.assertIsNone(registry.identify(image))
            self.assertFalse(Path(directory, 'faces').exists())

    def test_clear_preserves_unrelated_files(self):
        import cv2
        import numpy as np
        from pycozmo.face_registry import FaceRegistry
        with tempfile.TemporaryDirectory() as directory:
            registry = FaceRegistry(cv2, root=directory)
            registry.enroll('Test', np.zeros((128, 128), dtype=np.uint8))
            unrelated = Path(directory, 'notes.txt')
            unrelated.write_text('keep', encoding='utf-8')
            registry.clear()
            self.assertEqual(unrelated.read_text(encoding='utf-8'), 'keep')
            self.assertEqual(list(Path(directory).iterdir()), [unrelated])
            self.assertIsNone(registry.model)


if __name__ == '__main__':
    unittest.main()
