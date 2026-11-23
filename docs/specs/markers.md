# Visual markers

How the markers of Cozmo are recognized in an image: the symbols on the faces of the cubes, on the charger and the
custom object markers. Implemented in `pycozmo/markers.py`.

Confidence: **Specified** (precise rule, tested), **Assumed** (reasonable choice, may differ from the original),
**Not modeled**.

## The marker

A marker is a black square frame around a white area that holds a symbol. The symbol is described by 1024 *probes*:
the centers of a grid of 32 x 32 cells, in the frame of the marker (a unit square, x to the right and y down, from the
top left corner of the frame). The centers are between 0.218 and 0.782 on both axes, ordered with x changing slowest.
Each probe is the mean of 5 pixels: its center and the four points at 0.00626 (205 / 32768) on both sides along each axis.

There are 39 marker types (`MarkerType`): `ARROW`, `BULLSEYE2`, `CHARGER`, `INVALID`, `STAR5`, the six faces
(`BACK`, `BOTTOM`, `FRONT`, `LEFT`, `RIGHT`, `TOP`) of the cubes `LIGHTCUBEI`, `LIGHTCUBEJ` and `LIGHTCUBEK`, and the
16 markers of the custom objects (`SDK_2CIRCLES` ... `SDK_5TRIANGLES`).

## The library

The library holds 598 symbols, each with 1024 probe values: a marker in each of its orientations has its own label (150
labels). Per label: the type, the orientation (0, 90, 180 or 270 degrees) and the order of the corners to give to the
marker. Label 149 is the invalid symbol.

The library is **not** in the resource files of the application: it is compiled into its engine library
(`lib/armeabi-v7a/libcozmoEngine.so` of the APK). `pycozmo_resources.py prepare` reads it from the APK of the user
(only the 3.4.0 library, checked by its checksum) and writes `cozmo_resources/config/engine/vision/markers.npz`
in the resources of the user. It is not part of PyCozmo and must not be redistributed. Without the APK (an OBB alone) or
with another version, the preparation goes on with a warning and the markers are not available.

## Recognition of a symbol

Given a homography from the frame of the marker to the image (Specified, tested):

1. **Probes.** The value of each probe is the mean of its 5 pixels, rounded to the nearest pixel (half away from zero),
   whole division by 5.
2. **Contrast.** The 1024 values are stretched linearly so that the smallest becomes 0 and the largest 255.
3. **Closest symbol.** The distance to a symbol is the mean absolute difference over the probes, truncated to an integer.
   The symbols are read in order; one is the best when its distance is strictly below the best so far, which starts at the
   threshold of **50**; the second best is tracked the same way.
4. **Ambiguity.** When there is a second best with another label, the probes where the two symbols differ by more than the
   threshold are looked at: the mean difference between the probes and the best symbol over them must be below 1.25
   thresholds, or there is no match. Without such probes, the best symbol is kept.
5. **Result.** The label gives the type, the orientation and the order of the corners. The invalid label (149) and
   no match give no marker.

The corners are numbered top left, bottom left, top right, bottom right (a Z); the corners of the result are reordered so
that the first is the top left of the marker itself.

## Finding the frames

- **Original.** A scale-space binarization, connected components and quadrilaterals with refinement of the corners.
  **Not modeled**: the frames are found with OpenCV (**Assumed**).
- **Frames.** Adaptive threshold of the blurred image, contours, polygons with 4 convex corners, an area from 100 pixels
  to half of the image, sides not shorter than 35 % of the longest, and the polygon close to its contour. The corners are
  ordered clockwise from the one nearest to the top left, and mapped to the corners of the unit square.
- **Refinement** (Assumed). The corners are off by a few pixels, which spoils the comparison. Each corner is moved a step
  at a time (1.5 pixels, halved when nothing improves, 5 rounds) where the distance of the closest symbol decreases. It is
  only tried when the first distance is below 110.
- **Duplicates.** The frame of the marker and the border of the symbol both look like a square: of the markers that
  are closer than half a side from a better one, only the best is kept.

## Pose

`estimate_marker_pose(marker, size_mm, camera_matrix)` gives the rotation and the translation of a marker with respect to the
camera from its four corners (`cv2.solvePnP`). The size of the frame is not known from the image: the caller gives it.
The sizes of the cubes and of the charger are not modeled.

## Open points

| Point | Status |
|---|---|
| Probe grid, offsets, 5-point mean, stretching, distance, threshold 50, ambiguity rule | Specified |
| Types, orientations and corner orders of the labels | Specified |
| Reading the library from the engine library of the APK (3.4.0) | Specified |
| Detection of the frames and refinement of the corners | Assumed (OpenCV, not the original algorithm) |
| Letters of the cubes (I, J, K) and their object numbers | Not modeled |
| Size of the markers of the cubes and of the charger, pose of the objects | Not modeled |
| Speed | About 0.8 s for an image of 320 x 240 on the test machine: not real time |
