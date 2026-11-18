# Resources

Specification of the preparation of Cozmo resources from the user's copy of the Cozmo
Android application. Implemented by `pycozmo.resources` and `tools/pycozmo_resources.py`.

PyCozmo does not include any resource of the application. The user supplies the
application package; PyCozmo extracts the data it reads (animations, configuration,
sound banks) for local use.

## Inputs

Detected by content:

| Input | Recognized by |
| --- | --- |
| XAPK | ZIP with `manifest.json`, an `.apk` and an `.obb` member |
| APK | ZIP with `AndroidManifest.xml`; requires the OBB as a second file |
| OBB | ZIP with `assets/resources.txt` or `assets/cozmo_resources/` |

Reference version: **3.4.0, version code 1204**, package `com.anki.cozmo`.

| Item | Reference value |
| --- | --- |
| APK SHA-256 | `6f9f0a889a4bb1d0bbc89c9fa509adc809d6946f86228fec56b97353585f8544` |
| OBB `main.1204.com.anki.cozmo.obb` SHA-256 | `0f86f75fa139bf492558f37a29073b7992f11834256ff636861f5ed184c6caef` |
| APK signing certificate SHA-256 | `2f2e06275f965972119e72ffd4ac0a11f02a54cb28f21981a87c9499f535535a` |

## Checks

1. **Version**: from `manifest.json` (XAPK) or the OBB name `main.<code>.com.anki.cozmo.obb`.
   Another or unknown version is refused unless explicitly allowed (warning in the manifest).
2. **Package**: an XAPK of another package is refused.
3. **Signer**: SHA-256 of the first certificate of `META-INF/*.RSA|DSA|EC` (PKCS#7) compared with
   the reference; mismatch refused unless allowed. Signatures are not verified: this check
   identifies the publisher, it does not detect tampering. An OBB alone has no signer.
4. **Checksums**: APK and OBB compared with the reference; a difference is a warning.
5. **Safe extraction**: members with an absolute path, a backslash, a drive letter or a `..`
   component, and links, are refused; at most 1 GiB is extracted; a member larger than
   declared is refused.
6. **Completeness**: `resources.txt`, `cozmo_resources/config/cozmo_anim.fbs`,
   `cozmo_resources/assets/animations` and `cozmo_resources/sound/AudioAssets/SoundbanksInfo.xml`
   must exist.
7. **Content counts** (reference version only; differences are warnings): 289 animations,
   507 animation groups, 573 animation triggers, 40 cube animation triggers, 552 face animation
   frames, 6 sound banks, 2,214 `.wem` files.

## Extraction

From the OBB, only `assets/resources.txt` and `assets/cozmo_resources/**` are extracted, without
the leading `assets/`, except `cozmo_resources/tts/` (voices) and
`cozmo_resources/config/engine/firmware*` (robot firmware images; extracted on request).
`cozmo_resources/sound/AudioAssets.zip` is extracted into `cozmo_resources/sound/AudioAssets/`
and removed.

Extraction goes to a temporary directory next to the destination, then replaces it. Existing
resources, or a non-empty destination, are replaced only on request (`force`).

## Layout

```
<root>/                         default: $PYCOZMO_DIR/assets (~/.pycozmo/assets)
    resources-manifest.json
    resources.txt
    cozmo_resources/
        assets/                 animations, animationGroups, animationGroupMaps,
                                cubeAnimationGroupMaps, faceAnimations, ...
        config/
        sound/AudioAssets/      SoundbanksInfo.xml, *.bnk, *.wem, English(US)/
```

Readers also accept sound banks directly in `cozmo_resources/sound/` (former layout).

## Manifest

`resources-manifest.json`, format `pycozmo.resources.v1`: version name and code, APK and OBB
SHA-256, signer SHA-256, preparation time (UTC, ISO 8601), PyCozmo version, content counts,
`tree_sha256`, firmware option, warnings.

`tree_sha256` is the SHA-256 of one line per extracted file (`resources.txt`, then the files of
`cozmo_resources/` in sorted order): `<relative path>\0<SHA-256 of the file>\n`.

`verify` reports: missing manifest or required files, counts different from the manifest, and,
if those match, a different `tree_sha256`.

## Command line

`pycozmo_resources.py [--dest DIR] status | prepare SOURCE [--obb FILE] [--force]
[--include-firmware] [--allow-other-version] [--allow-other-signer] | verify | remove`.
`download` only explains how to use `prepare`.
