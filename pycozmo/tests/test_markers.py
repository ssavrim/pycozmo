
import io
import struct
import unittest
import zipfile

import cv2
import numpy as np

from pycozmo import markers, resources
from pycozmo.markers import (
    Marker, MarkerDetector, MarkerLibrary, MarkerType, classify_probes, extract_marker_library,
    homography_from_corners, sample_probes)

GRID = 32
LOW = 0.21817017
STEP = (0.78182983 - LOW) / (GRID - 1)
CORNER_ORDERS = [[0, 1, 2, 3], [1, 3, 0, 2], [3, 2, 1, 0], [2, 0, 3, 1]]
TYPES = [MarkerType.ARROW, MarkerType.CHARGER, MarkerType.STAR5]


def symbols():
    """ Three random 32 x 32 symbols, binary so that they are easy to tell apart. """
    rng = np.random.RandomState(7)
    return [(rng.rand(GRID, GRID) > 0.5).astype(np.uint8) * 255 for _ in TYPES]


def make_library(symbols_=None):
    """ A library of three symbols, each in four orientations, with the layout of the real one. """
    symbols_ = symbols() if symbols_ is None else symbols_
    ix, iy = np.meshgrid(np.arange(GRID), np.arange(GRID), indexing="ij")
    centers_x = (LOW + ix * STEP).reshape(-1).astype(np.float32)
    centers_y = (LOW + iy * STEP).reshape(-1).astype(np.float32)
    rows, labels, types, orientations, orders = [], [], [], [], []
    for index, symbol in enumerate(symbols_):
        for turn in range(4):
            rows.append(np.rot90(symbol, -turn).T.reshape(-1))
            labels.append(index * 4 + turn)
            types.append(int(TYPES[index]))
            orientations.append(90.0 * turn)
            orders.append(CORNER_ORDERS[turn])
    return MarkerLibrary(
        probes=np.array(rows, dtype=np.uint8), labels=np.array(labels, dtype=np.int16),
        types=np.array(types, dtype=np.int32), orientations=np.array(orientations, dtype=np.float32),
        corner_orders=np.array(orders, dtype=np.int32), centers_x=centers_x, centers_y=centers_y,
        offsets_x=np.array([0, 0.006, 0, -0.006, 0], dtype=np.float32),
        offsets_y=np.array([0, 0, 0.006, 0, -0.006], dtype=np.float32))


def render_marker(symbol, size=320, turn=0):
    """ A marker: a black square, a white gap, and the symbol (turned by ``turn`` quarters) in the middle. """
    canvas = np.full((size, size), 255, np.uint8)
    canvas[:, :int(0.2 * size)] = 0
    canvas[:, int(0.8 * size):] = 0
    canvas[:int(0.2 * size), :] = 0
    canvas[int(0.8 * size):, :] = 0
    inner = np.rot90(symbol, -turn)
    lo, hi = int(0.209 * size), int(0.791 * size)
    for py in range(lo, hi):
        for px in range(lo, hi):
            gx = min(GRID - 1, max(0, round(((px + 0.5) / size - LOW) / STEP)))
            gy = min(GRID - 1, max(0, round(((py + 0.5) / size - LOW) / STEP)))
            canvas[py, px] = inner[gy, gx]
    return canvas


def place(marker_image, corners, shape=(240, 320), background=190):
    """ The marker warped onto an image; ``corners`` are the images of its top left, top right, bottom right and
    bottom left corners. """
    size = marker_image.shape[0]
    src = np.array([[0, 0], [size, 0], [size, size], [0, size]], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(src, np.array(corners, dtype=np.float32))
    image = np.full(shape, background, np.uint8)
    warped = cv2.warpPerspective(marker_image, transform, (shape[1], shape[0]), borderValue=background,
                                 flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full(marker_image.shape, 255, np.uint8), transform, (shape[1], shape[0]))
    image[mask > 0] = warped[mask > 0]
    return image


class TestLibrary(unittest.TestCase):

    def test_validation(self):
        library = make_library()
        with self.assertRaises(ValueError):
            MarkerLibrary(**{**library.__dict__, "centers_y": library.centers_y[:-1]})
        with self.assertRaises(ValueError):
            MarkerLibrary(**{**library.__dict__, "labels": library.labels[:-1]})
        with self.assertRaises(ValueError):
            MarkerLibrary(**{**library.__dict__, "types": library.types[:3]})
        with self.assertRaises(ValueError):
            MarkerLibrary(**{**library.__dict__, "offsets_y": library.offsets_y[:2]})

    def test_save_and_load(self):
        import os
        import tempfile
        library = make_library()
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, "markers.npz")
            library.save(filename)
            loaded = MarkerLibrary.load(filename)
        for name in ("probes", "labels", "types", "orientations", "corner_orders", "centers_x", "centers_y",
                     "offsets_x", "offsets_y"):
            np.testing.assert_array_equal(getattr(loaded, name), getattr(library, name))

    def test_marker_types(self):
        self.assertEqual(len(MarkerType), 39)
        self.assertEqual(MarkerType.LIGHTCUBEJ_LEFT.name, "LIGHTCUBEJ_LEFT")
        self.assertEqual(MarkerType.STAR5, 38)


class TestProbes(unittest.TestCase):

    def test_sampling_through_the_homography(self):
        library = make_library()
        symbol = symbols()[0]
        image = render_marker(symbol, size=GRID * 10)
        probes = sample_probes(image, homography_from_corners([[0, 0], [319, 0], [319, 319], [0, 319]]), library)
        self.assertEqual(probes.shape, (GRID * GRID,))
        # The probes of the symbol are the symbol, give or take the edges that the offsets average.
        self.assertGreater(np.mean(probes == library.probes[0]), 0.9)

    def test_sampling_outside_the_image_is_clipped(self):
        library = make_library()
        probes = sample_probes(np.full((10, 10), 77, np.uint8),
                               homography_from_corners([[-50, -50], [200, -50], [200, 200], [-50, 200]]), library)
        self.assertTrue((probes == 77).all())


class TestClassify(unittest.TestCase):

    def setUp(self):
        self.library = make_library()

    def test_exact_symbols(self):
        for row in range(len(self.library.probes)):
            label, distance = classify_probes(self.library.probes[row], self.library)
            self.assertEqual(label, int(self.library.labels[row]))
            self.assertEqual(distance, 0)

    def test_contrast_is_stretched(self):
        faded = (self.library.probes[5].astype(np.int32) * 0.4 + 60).astype(np.uint8)
        label, distance = classify_probes(faded, self.library)
        self.assertEqual(label, 5)
        self.assertLess(distance, 5)

    def test_unknown_pattern(self):
        rng = np.random.RandomState(1)
        label, _ = classify_probes((rng.rand(GRID * GRID) * 255).astype(np.uint8), self.library)
        self.assertEqual(label, -1)

    def test_threshold(self):
        noisy = self.library.probes[2].copy()
        flip = np.random.RandomState(3).rand(noisy.size) < 0.2
        noisy[flip] = 255 - noisy[flip]
        label, distance = classify_probes(noisy, self.library, threshold=255)
        self.assertEqual(label, 2)
        self.assertGreater(distance, 10)
        self.assertEqual(classify_probes(noisy, self.library, threshold=distance)[0], -1)    # strictly below

    def test_ambiguous_symbols_are_rejected(self):
        # A second symbol, close to the first, with another label: only the probes that differ decide.
        library = make_library()
        probes = library.probes.copy()
        probes[4] = probes[0].copy()
        probes[4][:200] = 255 - probes[4][:200]
        library = MarkerLibrary(**{**library.__dict__, "probes": probes})
        mixed = probes[0].copy()
        mixed[:200] = (probes[0][:200].astype(np.int32) + probes[4][:200].astype(np.int32)) // 2
        label, _ = classify_probes(mixed, library)
        self.assertEqual(label, -1)
        self.assertEqual(classify_probes(probes[0], library)[0], 0)


class TestDetector(unittest.TestCase):

    def setUp(self):
        self.library = make_library()
        self.detector = MarkerDetector(self.library)
        self.symbols = symbols()

    def detect(self, symbol_index, turn=0, corners=((100, 50), (220, 50), (220, 170), (100, 170))):
        image = place(render_marker(self.symbols[symbol_index], turn=turn), corners)
        return self.detector.detect(image), image

    def test_upright_marker(self):
        found, _ = self.detect(1)
        self.assertEqual([m.type for m in found], [MarkerType.CHARGER])
        marker = found[0]
        self.assertEqual(marker.orientation, 0.0)
        self.assertEqual(marker.name, "CHARGER")
        self.assertLess(marker.distance, 20)
        cx, cy = marker.center
        self.assertAlmostEqual(cx, 160, delta=3)
        self.assertAlmostEqual(cy, 110, delta=3)
        # The corners are the ones of the marker: top left, bottom left, top right, bottom right.
        np.testing.assert_allclose(marker.corners, [[100, 50], [100, 170], [220, 50], [220, 170]], atol=3)

    def test_each_symbol(self):
        for index, expected in enumerate(TYPES):
            found, _ = self.detect(index)
            self.assertEqual([m.type for m in found], [expected])

    def test_turned_marker(self):
        for turn in range(1, 4):
            found, _ = self.detect(0, turn=turn)
            self.assertEqual([m.type for m in found], [MarkerType.ARROW], turn)
            self.assertEqual(found[0].orientation, 90.0 * turn)

    def test_perspective(self):
        found, _ = self.detect(2, corners=((90, 60), (200, 40), (230, 170), (110, 150)))
        self.assertEqual([m.type for m in found], [MarkerType.STAR5])

    def test_no_marker(self):
        image = np.full((240, 320), 120, np.uint8)
        cv2.rectangle(image, (50, 50), (150, 150), 0, 4)       # a plain square frame
        self.assertEqual(self.detector.detect(image), [])
        self.assertEqual(self.detector.detect(np.zeros((240, 320), np.uint8)), [])

    def test_color_and_pil_input(self):
        from PIL import Image
        _, image = self.detect(0)
        for converted in (np.dstack([image] * 3), Image.fromarray(image)):
            self.assertEqual([m.type for m in self.detector.detect(converted)], [MarkerType.ARROW])

    def test_two_markers(self):
        left = place(render_marker(self.symbols[0]), ((20, 40), (120, 40), (120, 140), (20, 140)))
        right = place(render_marker(self.symbols[1]), ((190, 60), (290, 60), (290, 160), (190, 160)))
        image = np.minimum(left, right)
        found = self.detector.detect(image)
        self.assertEqual(sorted(m.type for m in found), sorted([MarkerType.ARROW, MarkerType.CHARGER]))

    def test_marker_is_a_value(self):
        marker = Marker(MarkerType.ARROW, np.zeros((4, 2), np.float32), 0.0, 3)
        self.assertEqual(marker.center, (0.0, 0.0))


def fake_engine(check_values=None):
    """ A minimal ELF file with the data of the library at their addresses. """
    library = make_library()
    size = 0x1000000
    data = bytearray(size)
    header = bytearray(0x34)
    header[:4] = b"\x7fELF"
    header[4], header[5] = 1, 1
    struct.pack_into("<I", header, 0x1c, 0x34)
    struct.pack_into("<HH", header, 0x2a, 32, 1)
    program = struct.pack("<8I", 1, 0, 0, 0, size, size, 5, 0x1000)
    data[:0x34] = header
    data[0x34:0x34 + 32] = program

    def put(address, content):
        data[address:address + len(content)] = content

    n = len(library.centers_x)
    put(markers._ADDR_PARAMETERS, struct.pack("<4i", 32, n, 15, 5))
    put(markers._ADDR_PARAMETERS + 16, np.round(library.offsets_x * 32768).astype("<i2").tobytes())
    put(markers._ADDR_PARAMETERS + 26, np.round(library.offsets_y * 32768).astype("<i2").tobytes())
    put(markers._ADDR_CENTERS_X, np.round(library.centers_x * 32768).astype("<i2").tobytes())
    put(markers._ADDR_CENTERS_Y, np.round(library.centers_y * 32768).astype("<i2").tobytes())
    return data, library, put


class TestExtraction(unittest.TestCase):

    def build(self):
        data, library, put = fake_engine()
        count = len(library.probes)
        # The real library has 598 symbols; the fake one is padded to that.
        probes = np.zeros((markers._NUM_SYMBOLS, markers._NUM_PROBES), np.uint8)
        probes[:count] = library.probes
        labels = np.zeros(markers._NUM_SYMBOLS, "<u2")
        labels[:count] = library.labels
        types = np.zeros(markers._NUM_LABELS, "<i4")
        types[:count] = library.types
        orders = np.zeros((markers._NUM_LABELS, 4), "<i4")
        orders[:count] = library.corner_orders
        orientations = np.zeros(markers._NUM_LABELS, "<f4")
        orientations[:count] = library.orientations
        orientations[149] = np.frombuffer(struct.pack("<i", 32), "<f4")[0]       # an integer, not an angle
        put(markers._ADDR_SYMBOLS, probes.tobytes())
        put(markers._ADDR_LABELS, labels.tobytes())
        put(markers._ADDR_TYPES, types.tobytes())
        put(markers._ADDR_CORNER_ORDERS, orders.tobytes())
        put(markers._ADDR_ORIENTATIONS, orientations.tobytes())
        return bytes(data), library

    def test_extract(self):
        engine, library = self.build()
        extracted = extract_marker_library(engine, check_hash=False)
        count = len(library.probes)
        np.testing.assert_array_equal(extracted.probes[:count], library.probes)
        np.testing.assert_array_equal(extracted.labels[:count], library.labels)
        np.testing.assert_array_equal(extracted.types[:count], library.types)
        np.testing.assert_array_equal(extracted.corner_orders[:count], library.corner_orders)
        np.testing.assert_allclose(extracted.centers_x, library.centers_x, atol=1e-4)
        np.testing.assert_allclose(extracted.offsets_x, library.offsets_x, atol=1e-4)
        self.assertEqual(extracted.orientations[149], 0.0)
        self.assertEqual(extracted.orientations[1], 90.0)

    def test_extract_checks_the_file(self):
        engine, _ = self.build()
        with self.assertRaises(ValueError):
            extract_marker_library(engine)                                  # not the reference file
        with self.assertRaises(ValueError):
            extract_marker_library(b"not an elf file" * 10, check_hash=False)
        broken = bytearray(engine)
        struct.pack_into("<i", broken, markers._ADDR_PARAMETERS + 4, 512)   # another number of probes
        with self.assertRaises(ValueError):
            extract_marker_library(bytes(broken), check_hash=False)
        with self.assertRaises(ValueError):
            extract_marker_library(engine[:0x2000], check_hash=False)       # data beyond the end of the file


class TestPrepare(unittest.TestCase):

    class Source:
        def __init__(self, apk):
            self.apk = apk

    def apk(self, members):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            for name, content in members.items():
                z.writestr(name, content)
        return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))

    def prepare(self, source):
        import pathlib
        import tempfile
        warnings: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            resources._prepare_marker_library(source, pathlib.Path(tmp), warnings)
            written = (pathlib.Path(tmp) / markers.LIBRARY_FILENAME).exists()
        return warnings, written

    def test_without_apk(self):
        warnings, written = self.prepare(self.Source(None))
        self.assertFalse(written)
        self.assertIn("needs the APK", warnings[0])

    def test_without_engine_library(self):
        warnings, written = self.prepare(self.Source(self.apk({"other": b""})))
        self.assertFalse(written)
        self.assertIn("is not in the APK", warnings[0])

    def test_other_engine_library(self):
        warnings, written = self.prepare(self.Source(self.apk({markers.ENGINE_LIBRARY: b"\x7fELF" + bytes(100)})))
        self.assertFalse(written)
        self.assertIn("not the engine library of the reference application", warnings[0].lower())


class TestPose(unittest.TestCase):

    def project(self, rotation, translation, size, matrix):
        half = size / 2.0
        points = np.array([[-half, -half, 0], [-half, half, 0], [half, -half, 0], [half, half, 0]], np.float64)
        projected, _ = cv2.projectPoints(points, np.array(rotation, np.float64), np.array(translation, np.float64),
                                         matrix, None)
        return projected.reshape(4, 2).astype(np.float32)

    def test_pose(self):
        matrix = np.array([[290.0, 0, 160], [0, 290.0, 120], [0, 0, 1]])
        rotation = [0.0, 0.3, 0.1]
        translation = [20.0, -10.0, 250.0]
        marker = Marker(MarkerType.ARROW, self.project(rotation, translation, 25.0, matrix), 0.0, 0)
        found = markers.estimate_marker_pose(marker, 25.0, matrix)
        assert found is not None
        np.testing.assert_allclose(found[0], rotation, atol=0.02)
        np.testing.assert_allclose(found[1], translation, rtol=0.01, atol=1.0)

    def test_distance_follows_the_size(self):
        matrix = np.array([[290.0, 0, 160], [0, 290.0, 120], [0, 0, 1]])
        marker = Marker(MarkerType.ARROW, self.project([0.0, 0.0, 0.0], [0.0, 0.0, 200.0], 25.0, matrix), 0.0, 0)
        near = markers.estimate_marker_pose(marker, 25.0, matrix)
        far = markers.estimate_marker_pose(marker, 50.0, matrix)
        assert near is not None and far is not None
        self.assertAlmostEqual(near[1][2], 200.0, delta=2.0)
        self.assertAlmostEqual(far[1][2], 400.0, delta=4.0)
