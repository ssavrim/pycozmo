"""

Preparation of Cozmo resources from the user's copy of the Cozmo Android application.

PyCozmo does not ship any resource of the Cozmo application. ``prepare()`` takes
an application package supplied by the user and extracts the resources PyCozmo
uses (animations, configuration, sound banks) into the resource directory:

    <root>/                         default: ~/.pycozmo/assets (PYCOZMO_DIR)
        resources-manifest.json     what was prepared, from what, and checksums
        resources.txt
        cozmo_resources/
            assets/                 animations, animation groups, face animations...
            config/                 engine configuration, cozmo_anim.fbs...
            sound/AudioAssets/      sound banks, SoundbanksInfo.xml, .wem files

Accepted inputs: an XAPK (APK and OBB in one archive), an APK with its OBB, or the
OBB alone. The reference is version 3.4.0 (version code 1204).
Specification: ``docs/specs/resources.md``.

The signer check compares the APK signing certificate with Anki's. It detects a
different application, not a tampered one: signatures are not verified.

"""

import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import zipfile
from typing import Callable, Dict, Iterator, List, Optional, Tuple, Union

from . import exception
from . import util


__all__ = [
    "MANIFEST_FORMAT",
    "MANIFEST_NAME",
    "REFERENCE_VERSION_CODE",
    "REFERENCE_VERSION_NAME",

    "ResourceError",
    "ResourceManifest",

    "resource_root",
    "audio_dir",
    "load_manifest",
    "prepare",
    "verify",
]


MANIFEST_FORMAT = "pycozmo.resources.v1"
MANIFEST_NAME = "resources-manifest.json"
REFERENCE_VERSION_CODE = 1204
REFERENCE_VERSION_NAME = "3.4.0"
PACKAGE_NAME = "com.anki.cozmo"

#: SHA-256 of Anki's APK signing certificate (CN=anki.com).
ANKI_SIGNER_SHA256 = "2f2e06275f965972119e72ffd4ac0a11f02a54cb28f21981a87c9499f535535a"
#: SHA-256 of the reference 3.4.0 packages.
REFERENCE_APK_SHA256 = "6f9f0a889a4bb1d0bbc89c9fa509adc809d6946f86228fec56b97353585f8544"
REFERENCE_OBB_SHA256 = "0f86f75fa139bf492558f37a29073b7992f11834256ff636861f5ed184c6caef"
#: Content counts of the reference 3.4.0 resources (see ``_count()``).
REFERENCE_COUNTS = {
    "animations": 289,
    "animation_groups": 507,
    "animation_triggers": 573,
    "cube_animation_triggers": 40,
    "face_animation_frames": 552,
    "sound_banks": 6,
    "wem_files": 2214,
}

#: Upper bound of extracted data, against archive bombs.
MAX_EXTRACTED_BYTES = 1 << 30

_OBB_PREFIX = "assets/"
_SELECTED = ("assets/resources.txt", "assets/cozmo_resources/")
# Not used by PyCozmo: proprietary TTS voices and robot firmware images.
_EXCLUDED = ("assets/cozmo_resources/tts/", "assets/cozmo_resources/config/engine/firmware")
_AUDIO_ZIP = "cozmo_resources/sound/AudioAssets.zip"
_AUDIO_DIR = "cozmo_resources/sound/AudioAssets"
_REQUIRED = (
    "resources.txt",
    "cozmo_resources/config/cozmo_anim.fbs",
    "cozmo_resources/assets/animations",
    _AUDIO_DIR + "/SoundbanksInfo.xml",
)
_OBB_NAME_RE = re.compile(r"main\.(\d+)\.com\.anki\.cozmo\.obb$")

ProgressCallback = Callable[[str, float], None]
PathLike = Union[str, os.PathLike]


class ResourceError(exception.PyCozmoException):
    """ The supplied application package cannot be used, or prepared resources are invalid. """


@dataclasses.dataclass(frozen=True)
class ResourceManifest:
    """ Description of prepared resources, stored in ``resources-manifest.json``. """
    version_name: Optional[str]
    version_code: Optional[int]
    apk_sha256: Optional[str]
    obb_sha256: str
    signer_sha256: Optional[str]
    prepared_at: str
    tool_version: str
    counts: Dict[str, int]
    tree_sha256: str
    include_firmware: bool = False
    warnings: Tuple[str, ...] = ()
    format: str = MANIFEST_FORMAT

    def to_json(self) -> str:
        data = dataclasses.asdict(self)
        data["warnings"] = list(self.warnings)
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "ResourceManifest":
        data = json.loads(text)
        if data.get("format") != MANIFEST_FORMAT:
            raise ResourceError("Unsupported resource manifest format: {}".format(data.get("format")))
        fields = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in data.items() if k in fields}
        data["warnings"] = tuple(data.get("warnings", ()))
        return cls(**data)


def resource_root() -> pathlib.Path:
    """ Default resource directory: ``$PYCOZMO_DIR/assets``. """
    return util.get_cozmo_asset_dir()


def audio_dir(root: Optional[PathLike] = None) -> pathlib.Path:
    """
    Directory with sound banks and .wem files.

    Resources prepared by earlier versions of PyCozmo keep them directly in
    ``cozmo_resources/sound``; that layout is still accepted.
    """
    root = pathlib.Path(root) if root is not None else resource_root()
    path = root / _AUDIO_DIR
    if path.is_dir():
        return path
    return root / "cozmo_resources" / "sound"


def load_manifest(root: Optional[PathLike] = None) -> Optional[ResourceManifest]:
    """ Read the manifest of prepared resources, or None if there is none. """
    root = pathlib.Path(root) if root is not None else resource_root()
    try:
        text = (root / MANIFEST_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return ResourceManifest.from_json(text)


# ---------------------------------------------------------------------------
# Inputs

@dataclasses.dataclass
class _Source:
    obb: zipfile.ZipFile
    obb_sha256: str
    obb_name: str
    apk: Optional[zipfile.ZipFile] = None
    apk_sha256: Optional[str] = None
    version_code: Optional[int] = None
    version_name: Optional[str] = None
    closers: List = dataclasses.field(default_factory=list)

    def close(self) -> None:
        for c in reversed(self.closers):
            c.close()


def _sha256_stream(f, progress: Optional[ProgressCallback] = None, stage: str = "", size: int = 0) -> str:
    h = hashlib.sha256()
    done = 0
    while True:
        chunk = f.read(1 << 20)
        if not chunk:
            break
        h.update(chunk)
        done += len(chunk)
        if progress and size:
            progress(stage, min(done / size, 1.0))
    return h.hexdigest()


def _sha256_file(path: pathlib.Path, progress: Optional[ProgressCallback] = None, stage: str = "") -> str:
    with open(path, "rb") as f:
        return _sha256_stream(f, progress, stage, path.stat().st_size)


def _sha256_member(z: zipfile.ZipFile, name: str, progress: Optional[ProgressCallback] = None,
                   stage: str = "") -> str:
    with z.open(name) as f:
        return _sha256_stream(f, progress, stage, z.getinfo(name).file_size)


def _kind(z: zipfile.ZipFile) -> str:
    names = set(z.namelist())
    if "manifest.json" in names and any(n.endswith(".obb") for n in names) and any(n.endswith(".apk") for n in names):
        return "xapk"
    if "AndroidManifest.xml" in names:
        return "apk"
    if "assets/resources.txt" in names or any(n.startswith("assets/cozmo_resources/") for n in names):
        return "obb"
    return "unknown"


def _open_zip(path: pathlib.Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ResourceError("{} is not a readable archive: {}".format(path, e)) from e


def _version_from_obb_name(name: str) -> Optional[int]:
    m = _OBB_NAME_RE.search(name)
    return int(m.group(1)) if m else None


def _open_sources(source: pathlib.Path, obb: Optional[pathlib.Path],
                  progress: Optional[ProgressCallback]) -> _Source:
    z = _open_zip(source)
    kind = _kind(z)
    if kind == "xapk":
        if obb is not None:
            z.close()
            raise ResourceError("An XAPK already contains the OBB; do not pass --obb.")
        try:
            manifest = json.loads(z.read("manifest.json"))
        except (ValueError, KeyError) as e:
            z.close()
            raise ResourceError("Invalid XAPK manifest.json.") from e
        if manifest.get("package_name") != PACKAGE_NAME:
            z.close()
            raise ResourceError("{} is not the Cozmo application ({}).".format(
                source, manifest.get("package_name")))
        apk_name = next(n for n in z.namelist() if n.endswith(".apk"))
        obb_name = next(n for n in z.namelist() if n.endswith(".obb"))
        apk_sha = _sha256_member(z, apk_name, progress, "checksum APK")
        obb_sha = _sha256_member(z, obb_name, progress, "checksum OBB")
        apk_f = z.open(apk_name)
        obb_f = z.open(obb_name)
        try:
            version_code = int(manifest.get("version_code"))
        except (TypeError, ValueError):
            version_code = _version_from_obb_name(obb_name)
        src = _Source(obb=zipfile.ZipFile(obb_f), obb_sha256=obb_sha, obb_name=obb_name,
                      apk=zipfile.ZipFile(apk_f), apk_sha256=apk_sha, version_code=version_code,
                      version_name=manifest.get("version_name"))
        src.closers = [z, apk_f, obb_f, src.apk, src.obb]
        return src
    if kind == "apk":
        if obb is None:
            z.close()
            raise ResourceError("{} is an APK: the OBB file (main.1204.com.anki.cozmo.obb) is also "
                                "required, pass it with --obb.".format(source))
        apk_sha = _sha256_file(source, progress, "checksum APK")
        o = _open_zip(obb)
        if _kind(o) != "obb":
            z.close()
            o.close()
            raise ResourceError("{} is not a Cozmo OBB file.".format(obb))
        src = _Source(obb=o, obb_sha256=_sha256_file(obb, progress, "checksum OBB"), obb_name=obb.name,
                      apk=z, apk_sha256=apk_sha, version_code=_version_from_obb_name(obb.name))
        src.closers = [z, o]
        return src
    if kind == "obb":
        if obb is not None:
            z.close()
            raise ResourceError("{} is already an OBB file; do not pass --obb.".format(source))
        src = _Source(obb=z, obb_sha256=_sha256_file(source, progress, "checksum OBB"), obb_name=source.name,
                      version_code=_version_from_obb_name(source.name))
        src.closers = [z]
        return src
    z.close()
    raise ResourceError("{} is neither an XAPK, an APK nor an OBB of the Cozmo application.".format(source))


# ---------------------------------------------------------------------------
# APK signing certificate (minimal DER reader, no external dependency)

def _der_read(data: bytes, pos: int) -> Tuple[int, int, int, int]:
    """ Return (tag, content start, content end, element end) of the DER element at pos. """
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7f
        if not 0 < n <= 4:
            raise ValueError("Unsupported DER length.")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    end = pos + length
    if end > len(data):
        raise ValueError("Truncated DER element.")
    return tag, pos, end, end


def _der_children(data: bytes, start: int, end: int) -> Iterator[Tuple[int, int, int, int]]:
    pos = start
    while pos < end:
        tag, cstart, cend, nxt = _der_read(data, pos)
        yield tag, pos, cstart, cend
        pos = nxt


def signer_certificates(pkcs7: bytes) -> List[bytes]:
    """ DER certificates of a PKCS#7 SignedData blob (META-INF/*.RSA of an APK). """
    tag, start, end, _ = _der_read(pkcs7, 0)
    if tag != 0x30:
        raise ValueError("Not a PKCS#7 ContentInfo.")
    children = list(_der_children(pkcs7, start, end))
    if len(children) < 2 or children[1][0] != 0xa0:
        raise ValueError("No SignedData content.")
    _, _, cstart, cend = children[1]
    tag, sstart, send, _ = _der_read(pkcs7, cstart)
    if tag != 0x30:
        raise ValueError("Invalid SignedData.")
    for tag, elem_start, cstart, cend in _der_children(pkcs7, sstart, send):
        if tag == 0xa0:     # certificates [0] IMPLICIT SET OF Certificate
            return [pkcs7[s:e] for s, e in
                    ((es, ce) for _, es, _, ce in _der_children(pkcs7, cstart, cend))]
    return []


def _apk_signer_sha256(apk: zipfile.ZipFile) -> Optional[str]:
    for name in apk.namelist():
        if name.startswith("META-INF/") and name.upper().endswith((".RSA", ".DSA", ".EC")):
            try:
                certs = signer_certificates(apk.read(name))
            except (ValueError, IndexError):
                return None
            if certs:
                return hashlib.sha256(certs[0]).hexdigest()
    return None


# ---------------------------------------------------------------------------
# Extraction

def _safe_relpath(name: str) -> str:
    """ Validate an archive member name and return it as a relative POSIX path. """
    if not name or name.startswith(("/", "\\")) or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise ResourceError("Unsafe path in archive: {!r}".format(name))
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ResourceError("Unsafe path in archive: {!r}".format(name))
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _selected_members(obb: zipfile.ZipFile, include_firmware: bool) -> List[zipfile.ZipInfo]:
    excluded = tuple(e for e in _EXCLUDED if not (include_firmware and "firmware" in e))
    return [i for i in obb.infolist()
            if i.filename.startswith(_SELECTED) and not i.filename.startswith(excluded)]


def _extract(z: zipfile.ZipFile, members: List[zipfile.ZipInfo], dest: pathlib.Path, strip: str,
             budget: List[int], progress: Optional[ProgressCallback], stage: str) -> None:
    total = sum(i.file_size for i in members) or 1
    done = 0
    for info in members:
        rel = _safe_relpath(info.filename)
        if _is_symlink(info):
            raise ResourceError("Links are not allowed in archives: {!r}".format(info.filename))
        if strip:
            if not rel.startswith(strip):
                continue
            rel = rel[len(strip):]
        if not rel:
            continue
        target = dest / rel
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        budget[0] -= info.file_size
        if budget[0] < 0:
            raise ResourceError("Archive expands to more than {} bytes.".format(MAX_EXTRACTED_BYTES))
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with z.open(info) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > info.file_size:
                    raise ResourceError("Archive member larger than declared: {!r}".format(info.filename))
                out.write(chunk)
        done += info.file_size
        if progress:
            progress(stage, done / total)


def _count(root: pathlib.Path) -> Dict[str, int]:
    res = root / "cozmo_resources"
    assets = res / "assets"
    adir = audio_dir(root)

    def pairs(path: pathlib.Path) -> int:
        try:
            return len(json.loads(path.read_text(encoding="utf-8")).get("Pairs", []))
        except (OSError, ValueError):
            return 0

    return {
        "animations": len(list((assets / "animations").glob("*.bin"))),
        "animation_groups": len(list((assets / "animationGroups").rglob("*.json"))),
        "animation_triggers": pairs(assets / "animationGroupMaps" / "AnimationTriggerMap.json"),
        "cube_animation_triggers": pairs(assets / "cubeAnimationGroupMaps" / "CubeAnimationTriggerMap.json"),
        "face_animation_frames": len(list((assets / "faceAnimations").rglob("*.png"))),
        "sound_banks": len(list(adir.rglob("*.bnk"))),
        "wem_files": len(list(adir.rglob("*.wem"))),
    }


def _tree_sha256(root: pathlib.Path) -> str:
    """ Checksum of the extracted files (paths and contents), excluding the manifest. """
    h = hashlib.sha256()
    base = root / "cozmo_resources"
    files = sorted(p for p in base.rglob("*") if p.is_file())
    files.insert(0, root / "resources.txt")
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0" + _sha256_file(p).encode("ascii") + b"\n")
    return h.hexdigest()


def _tool_version() -> str:
    from . import __version__
    return __version__


def prepare(source: PathLike, *, obb: Optional[PathLike] = None, dest: Optional[PathLike] = None,
            force: bool = False, include_firmware: bool = False, allow_other_version: bool = False,
            allow_other_signer: bool = False, progress: Optional[ProgressCallback] = None) -> ResourceManifest:
    """
    Extract the resources of a Cozmo application package supplied by the user.

    :param source: XAPK, APK (with ``obb``), or OBB file.
    :param obb: OBB file, when ``source`` is an APK.
    :param dest: resource directory, ``resource_root()`` by default.
    :param force: replace resources that are already prepared.
    :param include_firmware: also extract the robot firmware images (read-only use).
    :param allow_other_version: accept a version other than 3.4.0 (1204).
    :param allow_other_signer: accept an APK not signed with Anki's certificate.
    :param progress: called with (stage, fraction between 0 and 1).
    :raises ResourceError: if the input is unsuitable or extraction fails.
    """
    source = pathlib.Path(source)
    obb_path = pathlib.Path(obb) if obb is not None else None
    dest = pathlib.Path(dest) if dest is not None else resource_root()
    if not force:
        if (dest / "resources.txt").exists():
            raise ResourceError("Resources already prepared in {}; use force to replace them.".format(dest))
        if dest.is_dir() and any(dest.iterdir()):
            raise ResourceError("{} is not empty; use force to replace its content.".format(dest))

    warnings = []   # type: List[str]
    src = _open_sources(source, obb_path, progress)
    tmp = dest.parent / ".{}.tmp-{}".format(dest.name, os.getpid())
    try:
        # Version.
        if src.version_code is None:
            msg = "Cannot determine the application version from {}.".format(src.obb_name)
            if not allow_other_version:
                raise ResourceError(msg + " Name the OBB main.1204.com.anki.cozmo.obb or allow other versions.")
            warnings.append(msg)
        elif src.version_code != REFERENCE_VERSION_CODE:
            msg = "Version code {} differs from the reference {} ({}).".format(
                src.version_code, REFERENCE_VERSION_CODE, REFERENCE_VERSION_NAME)
            if not allow_other_version:
                raise ResourceError(msg)
            warnings.append(msg)

        # Signer.
        signer = None
        if src.apk is not None:
            signer = _apk_signer_sha256(src.apk)
            if signer != ANKI_SIGNER_SHA256:
                msg = "The APK is not signed with Anki's certificate ({}).".format(signer or "none found")
                if not allow_other_signer:
                    raise ResourceError(msg)
                warnings.append(msg)
            if src.apk_sha256 != REFERENCE_APK_SHA256:
                warnings.append("The APK differs from the reference 3.4.0 package.")
        if src.obb_sha256 != REFERENCE_OBB_SHA256:
            warnings.append("The OBB differs from the reference 3.4.0 package.")

        # Extraction to a temporary directory.
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        budget = [MAX_EXTRACTED_BYTES]
        members = _selected_members(src.obb, include_firmware)
        _extract(src.obb, members, tmp, _OBB_PREFIX, budget, progress, "extract")
        audio_zip = tmp / _AUDIO_ZIP
        if audio_zip.is_file():
            with _open_zip(audio_zip) as az:
                _extract(az, az.infolist(), tmp / _AUDIO_DIR, "", budget, progress, "extract sound")
            audio_zip.unlink()

        missing = [r for r in _REQUIRED if not (tmp / r).exists()]
        if missing:
            raise ResourceError("Incomplete resources, missing: {}".format(", ".join(missing)))

        counts = _count(tmp)
        if src.version_code == REFERENCE_VERSION_CODE:
            diffs = ["{} {} (expected {})".format(k, counts.get(k), v)
                     for k, v in REFERENCE_COUNTS.items() if counts.get(k) != v]
            if diffs:
                warnings.append("Unexpected content: " + ", ".join(diffs))

        if progress:
            progress("checksum resources", 0.0)
        manifest = ResourceManifest(
            version_name=src.version_name or (REFERENCE_VERSION_NAME
                                              if src.version_code == REFERENCE_VERSION_CODE else None),
            version_code=src.version_code,
            apk_sha256=src.apk_sha256,
            obb_sha256=src.obb_sha256,
            signer_sha256=signer,
            prepared_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            tool_version=_tool_version(),
            counts=counts,
            tree_sha256=_tree_sha256(tmp),
            include_firmware=include_firmware,
            warnings=tuple(warnings),
        )
        (tmp / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")

        # Replace the destination.
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = None
        if dest.exists():
            old = dest.parent / ".{}.old-{}".format(dest.name, os.getpid())
            dest.rename(old)
        tmp.rename(dest)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return manifest
    except zipfile.BadZipFile as e:
        raise ResourceError("Corrupted archive: {}".format(e)) from e
    finally:
        src.close()
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def verify(root: Optional[PathLike] = None) -> List[str]:
    """
    Check prepared resources against their manifest.

    :return: a list of problems, empty if the resources match the manifest.
    """
    root = pathlib.Path(root) if root is not None else resource_root()
    try:
        manifest = load_manifest(root)
    except (ResourceError, ValueError, TypeError) as e:
        return ["Invalid manifest: {}".format(e)]
    if manifest is None:
        if (root / "resources.txt").exists():
            return ["No manifest: resources were prepared by an older tool; prepare them again."]
        return ["No resources in {}.".format(root)]
    problems = ["Missing: {}".format(r) for r in _REQUIRED if not (root / r).exists()]
    counts = _count(root)
    problems += ["{}: {} (manifest: {})".format(k, counts.get(k), v)
                 for k, v in manifest.counts.items() if counts.get(k) != v]
    if not problems and _tree_sha256(root) != manifest.tree_sha256:
        problems.append("File contents differ from the manifest.")
    return problems
