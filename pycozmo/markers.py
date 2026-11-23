"""

Recognition of the visual markers of Cozmo: the symbols on the faces of the cubes, on the charger and the custom
object markers (docs/specs/markers.md).

A marker is a black square frame around a symbol. A frame is found in the image, the symbol is sampled on a grid of
probes through the homography of the frame, and the probes are compared with a library of symbols. The library comes
from the Cozmo application, it is prepared with the other resources (see ``pycozmo.resources``).

"""

import enum
import hashlib
import os
import struct
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from .logger import logger


__all__ = [
    "MarkerType",
    "MarkerLibrary",
    "Marker",
    "MarkerDetector",
    "MATCH_THRESHOLD",
    "LIBRARY_FILENAME",

    "load_marker_library",
    "extract_marker_library",
    "homography_from_corners",
    "estimate_marker_pose",
    "cube_marker",
    "marker_size_mm",
    "CUBE_SIZE_MM",
    "CUBE_MARKER_SIZE_MM",
    "CHARGER_MARKER_SIZE_MM",
    "sample_probes",
    "classify_probes",
]


#: Largest mean difference between the probes and a symbol of the library for the symbol to match.
MATCH_THRESHOLD = 50
#: Name of the library file in the directory of the resources.
LIBRARY_FILENAME = os.path.join("cozmo_resources", "config", "engine", "vision", "markers.npz")

#: Side of a cube, and of the square frame of its markers, in millimeters.
CUBE_SIZE_MM = 44.0
CUBE_MARKER_SIZE_MM = 25.0
#: Width and height of the frame of the marker of the charger, in millimeters.
CHARGER_MARKER_SIZE_MM = (27.0, 20.0)

#: The engine library of the reference application (3.4.0), in the APK, that holds the symbols.
ENGINE_LIBRARY = "lib/armeabi-v7a/libcozmoEngine.so"
ENGINE_LIBRARY_SHA256 = "02263c07f6bb60f4d7f351a3667dca84fd3e6c0fc6f838e18b5de7cf4e2989e1"

# Where the data are in the engine library, as virtual addresses.
_NUM_SYMBOLS = 598
_NUM_PROBES = 1024
_ADDR_SYMBOLS = 0xc9bca8
_ADDR_LABELS = 0xdc6ca8
_ADDR_TYPES = 0xdc7154
_ADDR_CORNER_ORDERS = 0xdc73ac
_ADDR_ORIENTATIONS = 0xdc7d0c
_ADDR_PARAMETERS = 0xdc7f64         # grid size, number of probes, fractional bits, number of probe points, offsets
_ADDR_CENTERS_X = 0xdc7fec
_ADDR_CENTERS_Y = 0xdc87ec

#: The labels of the library beyond this one do not name a marker: 149 is the "invalid" symbol.
_NUM_LABELS = 150
_INVALID_LABEL = 149
_SECOND_BEST_FACTOR = 1.25
#: Quads whose coarse distance is still above this many thresholds after the search are not polished.
_POLISH_FACTOR = 1.3
#: Number of symbols that the refinement of the corners compares with.
_CANDIDATES = 12


class MarkerType(enum.IntEnum):
    """ The markers of Cozmo. """
    ARROW = 0
    BULLSEYE2 = 1
    CHARGER = 2
    INVALID = 3
    LIGHTCUBEI_BACK = 4
    LIGHTCUBEI_BOTTOM = 5
    LIGHTCUBEI_FRONT = 6
    LIGHTCUBEI_LEFT = 7
    LIGHTCUBEI_RIGHT = 8
    LIGHTCUBEI_TOP = 9
    LIGHTCUBEJ_BACK = 10
    LIGHTCUBEJ_BOTTOM = 11
    LIGHTCUBEJ_FRONT = 12
    LIGHTCUBEJ_LEFT = 13
    LIGHTCUBEJ_RIGHT = 14
    LIGHTCUBEJ_TOP = 15
    LIGHTCUBEK_BACK = 16
    LIGHTCUBEK_BOTTOM = 17
    LIGHTCUBEK_FRONT = 18
    LIGHTCUBEK_LEFT = 19
    LIGHTCUBEK_RIGHT = 20
    LIGHTCUBEK_TOP = 21
    SDK_2CIRCLES = 22
    SDK_2DIAMONDS = 23
    SDK_2HEXAGONS = 24
    SDK_2TRIANGLES = 25
    SDK_3CIRCLES = 26
    SDK_3DIAMONDS = 27
    SDK_3HEXAGONS = 28
    SDK_3TRIANGLES = 29
    SDK_4CIRCLES = 30
    SDK_4DIAMONDS = 31
    SDK_4HEXAGONS = 32
    SDK_4TRIANGLES = 33
    SDK_5CIRCLES = 34
    SDK_5DIAMONDS = 35
    SDK_5HEXAGONS = 36
    SDK_5TRIANGLES = 37
    STAR5 = 38


@dataclass(frozen=True)
class MarkerLibrary:
    """
    The symbols to recognize.

    ``probes`` has one row of ``len(centers_x)`` values per symbol; ``labels`` gives the label of each row (the four
    orientations of a symbol have four labels); ``types``, ``orientations`` (degrees) and ``corner_orders`` are
    indexed by label. The probes are at ``centers_x``, ``centers_y`` in the frame of a marker, each averaged over the
    points shifted by ``offsets_x``, ``offsets_y``.
    """
    probes: npt.NDArray[np.uint8]
    labels: npt.NDArray[np.int16]
    types: npt.NDArray[np.int32]
    orientations: npt.NDArray[np.float32]
    corner_orders: npt.NDArray[np.int32]
    centers_x: npt.NDArray[np.float32]
    centers_y: npt.NDArray[np.float32]
    offsets_x: npt.NDArray[np.float32]
    offsets_y: npt.NDArray[np.float32]

    def __post_init__(self) -> None:
        count = len(self.centers_x)
        if self.probes.ndim != 2 or self.probes.shape[1] != count or len(self.centers_y) != count:
            raise ValueError("The probes do not match their centers")
        if len(self.labels) != len(self.probes):
            raise ValueError("Each symbol needs a label")
        if len(self.offsets_x) != len(self.offsets_y) or len(self.offsets_x) == 0:
            raise ValueError("Invalid probe offsets")
        labels = int(np.max(self.labels)) + 1 if len(self.labels) else 0
        if not len(self.types) == len(self.orientations) == len(self.corner_orders) >= labels:
            raise ValueError("Each label needs a type, an orientation and a corner order")

    def save(self, filename: str) -> None:
        np.savez_compressed(
            filename, probes=self.probes, labels=self.labels, types=self.types, orientations=self.orientations,
            corner_orders=self.corner_orders, centers_x=self.centers_x, centers_y=self.centers_y,
            offsets_x=self.offsets_x, offsets_y=self.offsets_y)

    @classmethod
    def load(cls, filename: str) -> "MarkerLibrary":
        with np.load(filename) as data:
            return cls(
                probes=data["probes"].astype(np.uint8), labels=data["labels"].astype(np.int16),
                types=data["types"].astype(np.int32), orientations=data["orientations"].astype(np.float32),
                corner_orders=data["corner_orders"].astype(np.int32), centers_x=data["centers_x"].astype(np.float32),
                centers_y=data["centers_y"].astype(np.float32), offsets_x=data["offsets_x"].astype(np.float32),
                offsets_y=data["offsets_y"].astype(np.float32))


@dataclass(frozen=True)
class Marker:
    """
    A marker seen in an image.

    ``corners`` are the four corners in the image, as (x, y), in the order top left, bottom left, top right, bottom
    right of the marker itself (the order of the corners of a rotated marker follows its orientation).
    ``orientation`` is the rotation of the marker in degrees and ``distance`` the mean difference with the symbol
    of the library (smaller is better).
    """
    type: MarkerType
    corners: npt.NDArray[np.float32]
    orientation: float
    distance: int

    @property
    def name(self) -> str:
        return self.type.name

    @property
    def center(self) -> tuple[float, float]:
        x, y = self.corners.mean(axis=0)
        return float(x), float(y)


def cube_marker(marker_type: MarkerType) -> tuple[int, str] | None:
    """
    The cube and the face of a marker: the number of the object (1, 2 or 3 for LIGHTCUBE1, 2 or 3) and the name of the
    face (BACK, BOTTOM, FRONT, LEFT, RIGHT or TOP); None for the other markers.
    """
    name = marker_type.name
    if not name.startswith("LIGHTCUBE"):
        return None
    letter, face = name[len("LIGHTCUBE"):].split("_")
    return "IJK".index(letter) + 1, face


def marker_size_mm(marker_type: MarkerType) -> tuple[float, float] | None:
    """ Width and height of the frame of a marker, in millimeters; None when it depends on the object. """
    if cube_marker(marker_type) is not None:
        return CUBE_MARKER_SIZE_MM, CUBE_MARKER_SIZE_MM
    if marker_type == MarkerType.CHARGER:
        return CHARGER_MARKER_SIZE_MM
    return None


def load_marker_library(resource_dir: str) -> MarkerLibrary:
    """ Load the library of the prepared resources. Raises FileNotFoundError when it was not prepared. """
    start_time = time.perf_counter()
    library = MarkerLibrary.load(os.path.join(resource_dir, LIBRARY_FILENAME))
    logger.debug("Loaded {} marker symbols in {:.02f} s.".format(len(library.probes), time.perf_counter() - start_time))
    return library


def _virtual_reader(engine: bytes) -> Any:
    """ A function reading bytes at a virtual address of an ELF32 little-endian file. """
    if len(engine) < 0x34 or engine[:4] != b"\x7fELF" or engine[4] != 1 or engine[5] != 1:
        raise ValueError("Not a 32-bit little-endian ELF file")
    phoff, = struct.unpack_from("<I", engine, 0x1c)
    phentsize, phnum = struct.unpack_from("<HH", engine, 0x2a)
    segments = []
    for i in range(phnum):
        kind, offset, vaddr, _, filesz, _, _, _ = struct.unpack_from("<8I", engine, phoff + i * phentsize)
        if kind == 1:
            segments.append((vaddr, offset, filesz))

    def read(address: int, size: int) -> bytes:
        for vaddr, offset, filesz in segments:
            if vaddr <= address and address + size <= vaddr + filesz:
                start = offset + address - vaddr
                if start + size <= len(engine):
                    return engine[start:start + size]
        raise ValueError("Address 0x{:x} is not in the file".format(address))

    return read


def extract_marker_library(engine: bytes, check_hash: bool = True) -> MarkerLibrary:
    """
    Read the library of symbols from the engine library of the Cozmo application (``ENGINE_LIBRARY``).

    Only the reference version is supported (``check_hash``): the data are read at fixed addresses.
    Raises ValueError for another file or when the data look wrong.
    """
    if check_hash and hashlib.sha256(engine).hexdigest() != ENGINE_LIBRARY_SHA256:
        raise ValueError("Not the engine library of the reference application")
    read = _virtual_reader(engine)
    grid, num_probes, fractional_bits, num_points = struct.unpack("<4i", read(_ADDR_PARAMETERS, 16))
    if (grid * grid, num_probes, fractional_bits) != (_NUM_PROBES, _NUM_PROBES, 15) or not 0 < num_points <= 5:
        raise ValueError("Unexpected marker parameters")
    scale = np.float32(1 << fractional_bits)
    offsets_x = np.frombuffer(read(_ADDR_PARAMETERS + 16, 2 * num_points), dtype="<i2").astype(np.float32) / scale
    offsets_y = np.frombuffer(read(_ADDR_PARAMETERS + 26, 2 * num_points), dtype="<i2").astype(np.float32) / scale
    centers_x = np.frombuffer(read(_ADDR_CENTERS_X, 2 * num_probes), dtype="<i2").astype(np.float32) / scale
    centers_y = np.frombuffer(read(_ADDR_CENTERS_Y, 2 * num_probes), dtype="<i2").astype(np.float32) / scale
    probes = np.frombuffer(read(_ADDR_SYMBOLS, _NUM_SYMBOLS * num_probes), dtype=np.uint8).reshape(_NUM_SYMBOLS, -1)
    labels = np.frombuffer(read(_ADDR_LABELS, 2 * _NUM_SYMBOLS), dtype="<u2").astype(np.int16)
    types = np.frombuffer(read(_ADDR_TYPES, 4 * _NUM_LABELS), dtype="<i4")
    corner_orders = np.frombuffer(read(_ADDR_CORNER_ORDERS, 16 * _NUM_LABELS), dtype="<i4").reshape(_NUM_LABELS, 4)
    orientations = np.frombuffer(read(_ADDR_ORIENTATIONS, 4 * _NUM_LABELS), dtype="<f4").copy()
    orientations[np.abs(orientations) < 1e-6] = 0.0     # the invalid label holds an integer instead of an angle
    if np.max(labels) >= _NUM_LABELS or np.max(types) > MarkerType.STAR5 or np.max(corner_orders) > 3:
        raise ValueError("Unexpected marker tables")
    return MarkerLibrary(
        probes=probes.copy(), labels=labels, types=types.astype(np.int32), orientations=orientations,
        corner_orders=corner_orders.astype(np.int32), centers_x=centers_x, centers_y=centers_y,
        offsets_x=offsets_x, offsets_y=offsets_y)


def homography_from_corners(corners: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """
    Homography from the frame of a marker, a unit square, to the image.

    ``corners`` are the images of (0, 0), (1, 0), (1, 1) and (0, 1): the top left, top right, bottom right and bottom
    left corners.
    """
    src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    dst = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    return np.asarray(cv2.getPerspectiveTransform(src, dst), dtype=np.float64)


def sample_probes(image: npt.NDArray[np.uint8], homography: npt.NDArray[np.float64],
                  library: MarkerLibrary) -> npt.NDArray[np.uint8]:
    """ The value of each probe: the mean of the pixels at its points, through the homography. """
    xs = library.centers_x[:, None] + library.offsets_x[None, :]
    ys = library.centers_y[:, None] + library.offsets_y[None, :]
    h = homography.astype(np.float32)
    w = h[2, 0] * xs + h[2, 1] * ys + h[2, 2]
    u = (h[0, 0] * xs + h[0, 1] * ys + h[0, 2]) / w
    v = (h[1, 0] * xs + h[1, 1] * ys + h[1, 2]) / w
    # Round half away from zero, as the original application.
    cols = np.where(u > 0, np.floor(u + 0.5), np.ceil(u - 0.5)).astype(np.int64)
    rows = np.where(v > 0, np.floor(v + 0.5), np.ceil(v - 0.5)).astype(np.int64)
    height, width = image.shape[:2]
    cols = np.clip(cols, 0, width - 1)
    rows = np.clip(rows, 0, height - 1)
    samples = image[rows, cols].astype(np.int64)
    means: npt.NDArray[np.uint8] = (samples.sum(axis=1) // samples.shape[1]).astype(np.uint8)
    return means


def classify_probes(probes: npt.NDArray[np.uint8], library: MarkerLibrary,
                    threshold: int = MATCH_THRESHOLD) -> tuple[int, int]:
    """
    Label of the symbol that the probes show, and its distance; the label is -1 when no symbol matches.

    The probes are stretched to 0-255 and compared with each symbol by mean absolute difference. The closest symbol
    below the threshold is kept, unless the second closest has another label and its probes that differ from the
    first by more than the threshold are as far from the first as 1.25 thresholds.
    """
    p = cv2.normalize(probes.reshape(-1, 1), None, 255, 0, cv2.NORM_MINMAX).reshape(-1)
    diffs = np.abs(library.probes.astype(np.int32) - p.astype(np.int32)[None, :])
    distances = (diffs.sum(axis=1) / library.probes.shape[1]).astype(np.int64)      # truncated
    best_distance = threshold
    best = -1
    second = -1
    second_distance = np.iinfo(np.int32).max
    for index, distance in enumerate(distances):
        if distance < best_distance:
            second, second_distance = best, best_distance
            best, best_distance = index, int(distance)
        elif distance < second_distance:
            second, second_distance = index, int(distance)
    if best == -1:
        return -1, threshold
    label = int(library.labels[best])
    if second != -1 and int(library.labels[second]) != label:
        differs = np.abs(library.probes[best].astype(np.int32) - library.probes[second].astype(np.int32)) > threshold
        count = int(differs.sum())
        if count:
            gap = np.abs(p.astype(np.int32)[differs] - library.probes[best].astype(np.int32)[differs])
            spread = int(gap.sum()) // count
            if threshold * _SECOND_BEST_FACTOR <= spread:
                return -1, best_distance
    return label, best_distance


class MarkerDetector:
    """ Finds the markers of an image. """

    def __init__(self, library: MarkerLibrary, threshold: int = MATCH_THRESHOLD, min_area: float = 100.0,
                 max_area_fraction: float = 0.5, edge_margin: float = 2.0, refine_iterations: int = 5,
                 refine_step: float = 1.5, refine_limit: int = 110) -> None:
        self.library = library
        self.threshold = threshold
        self.min_area = min_area
        self.max_area_fraction = max_area_fraction
        self.edge_margin = edge_margin
        # Refinement of the corners: number of rounds, first step in pixels, and the distance above which the quad
        # is not worth refining.
        self.refine_iterations = refine_iterations
        self.refine_step = refine_step
        self.refine_limit = refine_limit
        # The refinement compares a quarter of the probes, each at its center only.
        side = int(round(len(library.centers_x) ** 0.5))
        index = np.arange(len(library.centers_x))
        if side * side == len(index):
            coarse = np.flatnonzero((index // side % 2 == 0) & (index % side % 2 == 0))
        else:
            coarse = index
        self._probes16 = library.probes.astype(np.int16)
        self._coarse = coarse
        self._coarse_x = library.centers_x[coarse]
        self._coarse_y = library.centers_y[coarse]
        self._coarse_probes = library.probes[:, coarse].astype(np.int16)

    def find_quads(self, image: npt.NDArray[np.uint8]) -> list[npt.NDArray[np.float32]]:
        """
        Dark square frames, each as four corners (clockwise, from the one nearest to the top left).

        The image is binarized in several ways, because the frame of a small or faded marker only comes out with
        some of them; the squares that come out of two of them at the same place count once.
        """
        height, width = image.shape[:2]
        blur = cv2.GaussianBlur(image, (3, 3), 0)
        big = max(15, (min(height, width) // 8) | 1)
        binaries = [
            cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, c)
            for block, c in ((big, 10), (big, 4), (15, 6), (31, 8))]
        _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        binaries.append(otsu)
        quads: list[npt.NDArray[np.float32]] = []
        for binary in binaries:
            contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                quad = self._quad(contour, image.shape)
                if quad is None:
                    continue
                if not any(np.abs(quad - other).max() < 3.0 for other in quads):
                    quads.append(quad)
        return quads

    def _quad(self, contour: Any, shape: tuple[int, ...]) -> npt.NDArray[np.float32] | None:
        """ The four corners of a contour that looks like the frame of a marker, if it does. """
        height, width = shape[:2]
        area = cv2.contourArea(contour)
        if area < self.min_area or area > self.max_area_fraction * width * height:
            return None
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            return None
        corners = polygon.reshape(4, 2).astype(np.float32)
        # A marker cut by the edge of the image cannot be told from a part of something else.
        edge = self.edge_margin
        if (corners.min() < edge or corners[:, 0].max() > width - 1 - edge or corners[:, 1].max() > height - 1 - edge):
            return None
        # Reject thin or very distorted shapes.
        sides = np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)
        if sides.min() < 0.35 * sides.max() or cv2.contourArea(corners) < 0.6 * area:
            return None
        # Clockwise on the screen (y down), starting at the corner nearest to the top left.
        if cv2.contourArea(corners, oriented=True) < 0:
            corners = corners[::-1]
        start = int(np.argmin(corners.sum(axis=1)))
        return np.asarray(np.roll(corners, -start, axis=0), dtype=np.float32)

    def _probes(self, image: npt.NDArray[np.uint8], corners: npt.NDArray[np.float32],
                full: bool) -> npt.NDArray[np.int16]:
        """ The probes of the corners, stretched to 0-255: all of them, or a quarter of them at their centers. """
        if full:
            probes = sample_probes(image, homography_from_corners(corners), self.library).astype(np.int16)
        else:
            h = homography_from_corners(corners).astype(np.float32)
            w = h[2, 0] * self._coarse_x + h[2, 1] * self._coarse_y + h[2, 2]
            cols = np.rint((h[0, 0] * self._coarse_x + h[0, 1] * self._coarse_y + h[0, 2]) / w).astype(np.intp)
            rows = np.rint((h[1, 0] * self._coarse_x + h[1, 1] * self._coarse_y + h[1, 2]) / w).astype(np.intp)
            height, width = image.shape[:2]
            probes = image[np.clip(rows, 0, height - 1), np.clip(cols, 0, width - 1)].astype(np.int16)
        low, high = int(np.min(probes)), int(np.max(probes))
        return ((probes - low) * 255 // max(1, high - low)).astype(np.int16)

    def _distances(self, image: npt.NDArray[np.uint8], corners: npt.NDArray[np.float32], full: bool,
                   rows: npt.NDArray[np.intp] | None = None) -> npt.NDArray[np.int64]:
        """ Distance to the symbols (or to the ``rows`` of the library) of the probes of the corners. """
        library = self._probes16 if full else self._coarse_probes
        if rows is not None:
            library = library[rows]
        probes = self._probes(image, corners, full)
        return np.asarray(np.abs(library - probes[None, :]).sum(axis=1) // library.shape[1], dtype=np.int64)

    def _distance(self, image: npt.NDArray[np.uint8], corners: npt.NDArray[np.float32]) -> int:
        """ Distance of the closest symbol on the coarse probes. """
        return int(np.min(self._distances(image, corners, False)))

    def _search(self, image: npt.NDArray[np.uint8], corners: npt.NDArray[np.float32], full: bool,
                rows: npt.NDArray[np.intp], step: float, rounds: int) -> npt.NDArray[np.float32]:
        best = int(np.min(self._distances(image, corners, full, rows)))
        for _ in range(rounds):
            improved = False
            for i in range(4):
                for axis in (0, 1):
                    for sign in (-1.0, 1.0):
                        candidate = corners.copy()
                        candidate[i, axis] += sign * step
                        value = int(np.min(self._distances(image, candidate, full, rows)))
                        if value < best:
                            best, corners, improved = value, candidate, True
            if not improved:
                step /= 2
        return corners

    def refine_corners(self, image: npt.NDArray[np.uint8],
                       corners: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """
        Move the corners, a fraction of a pixel at a time, to the place where the symbols fit best.

        The corners found in the image are a few pixels off. The search compares only the closest symbols, on a
        quarter of the probes, then goes on with all the probes for a last round of small steps.
        """
        distances = self._distances(image, corners, False)
        if int(np.min(distances)) > self.refine_limit:
            return corners
        rows = np.argsort(distances)[:_CANDIDATES].astype(np.intp)
        corners = self._search(image, corners, False, rows, self.refine_step, self.refine_iterations)
        if int(np.min(self._distances(image, corners, False, rows))) > self.threshold * _POLISH_FACTOR:
            return corners          # far from any symbol: not worth a closer look
        return self._search(image, corners, True, rows, 0.5, 2)

    def classify_quad(self, image: npt.NDArray[np.uint8], corners: npt.NDArray[np.float32]) -> Marker | None:
        """ The marker framed by four corners (top left, top right, bottom right, bottom left), if any. """
        corners = self.refine_corners(image, corners)
        homography = homography_from_corners(corners)
        probes = sample_probes(image, homography, self.library)
        label, distance = classify_probes(probes, self.library, self.threshold)
        if label < 0 or label == _INVALID_LABEL or label >= len(self.library.types):
            return None
        marker_type = int(self.library.types[label])
        if marker_type == MarkerType.INVALID:
            return None
        # Corners in the order top left, bottom left, top right, bottom right, then reordered to the marker.
        z_order = corners[[0, 3, 1, 2]]
        ordered = z_order[self.library.corner_orders[label]]
        return Marker(MarkerType(marker_type), ordered.astype(np.float32), float(self.library.orientations[label]),
                      distance)

    def detect(self, image: Any) -> list[Marker]:
        """
        Find the markers of a grayscale image (or a PIL image, or a color array).

        The frame of a marker and the edge of its symbol both look like a square: when several markers share a
        place, the one that matches best is kept.
        """
        gray = _to_gray(image)
        found = []
        for quad in self.find_quads(gray):
            marker = self.classify_quad(gray, quad)
            if marker is not None:
                found.append(marker)
        found.sort(key=lambda m: m.distance)
        markers: list[Marker] = []
        for marker in found:
            size = float(np.linalg.norm(marker.corners[0] - marker.corners[3]))
            cx, cy = marker.center
            if all(np.hypot(cx - other.center[0], cy - other.center[1]) > 0.5 * size for other in markers):
                markers.append(marker)
        return markers


def estimate_marker_pose(
        marker: Marker, size_mm: float | tuple[float, float] | None, camera_matrix: npt.ArrayLike,
        distortion: npt.ArrayLike | None = None) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]] | None:
    """
    Pose of a marker with respect to the camera: a rotation vector and a translation in millimeters.

    ``size_mm`` is the side of the square frame of the marker, or its width and height; it is not known from the image.
    None takes the size of the marker of a cube or of the charger (``marker_size_mm``). The frame of the marker has
    x to the right, y down and z away from the symbol. Returns None when the size is unknown or the pose cannot be
    found.
    """
    if size_mm is None:
        size = marker_size_mm(marker.type)
        if size is None:
            return None
        width, height = size
    elif isinstance(size_mm, tuple):
        width, height = size_mm
    else:
        width = height = float(size_mm)
    half_w, half_h = width / 2.0, height / 2.0
    # The corners of a marker are in the order top left, bottom left, top right, bottom right.
    points = np.array([[-half_w, -half_h, 0], [-half_w, half_h, 0], [half_w, -half_h, 0], [half_w, half_h, 0]],
                      dtype=np.float64)
    ok, rotation, translation = cv2.solvePnP(
        points, marker.corners.astype(np.float64), np.asarray(camera_matrix, dtype=np.float64),
        None if distortion is None else np.asarray(distortion, dtype=np.float64), flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    return np.asarray(rotation, dtype=np.float64).reshape(3), np.asarray(translation, dtype=np.float64).reshape(3)


def _to_gray(image: Any) -> npt.NDArray[np.uint8]:
    array = np.asarray(image)
    if array.ndim == 3:
        array = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    return np.ascontiguousarray(array, dtype=np.uint8)
