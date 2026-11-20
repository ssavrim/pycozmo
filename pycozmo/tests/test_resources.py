"""
Resource preparation from synthetic application packages.

No resource of the Cozmo application is used: the archives are built here.
"""

import hashlib
import io
import json
import zipfile
from typing import Any

import pytest

from pycozmo import resources


FAKE_CERT = bytes.fromhex("3003020101")    # a DER SEQUENCE standing for a certificate


def der(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        return bytes([tag, n]) + content
    size = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + content


def pkcs7(cert: bytes) -> bytes:
    signed_data_oid = der(0x06, bytes.fromhex("2a864886f70d010702"))
    data_oid = der(0x06, bytes.fromhex("2a864886f70d010701"))
    signed_data = der(0x30, der(0x02, b"\x01") + der(0x31, b"") + der(0x30, data_oid) +
                      der(0xa0, cert) + der(0x31, b""))
    return der(0x30, signed_data_oid + der(0xa0, signed_data))


def zip_bytes(files: dict, symlinks: Any = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16
            z.writestr(info, "target")
    return buf.getvalue()


def obb_bytes(extra: Any = None, symlinks: Any = ()) -> bytes:
    audio = zip_bytes({
        "SoundbanksInfo.xml": "<SoundBanksInfo/>",
        "Init.bnk": b"BKHD",
        "1.wem": b"RIFF",
        "English(US)/Cozmo.bnk": b"BKHD",
        "English(US)/2.wem": b"RIFF",
    })
    files = {
        "assets/resources.txt": "DASConfig.json\n",
        "assets/cozmo_resources/config/cozmo_anim.fbs": "table Keyframes {}",
        "assets/cozmo_resources/assets/animations/anim_a.bin": b"\0" * 16,
        "assets/cozmo_resources/assets/animationGroups/g/ag_a.json": json.dumps({"Animations": []}),
        "assets/cozmo_resources/assets/animationGroupMaps/AnimationTriggerMap.json":
            json.dumps({"Pairs": [{"CladEvent": "A", "AnimName": "ag_a"}]}),
        "assets/cozmo_resources/assets/faceAnimations/f/f_00000.png": b"\x89PNG",
        "assets/cozmo_resources/sound/AudioAssets.zip": audio,
        "assets/cozmo_resources/tts/voice.dat": b"voice",
        "assets/cozmo_resources/config/engine/firmware_1/cozmo.safe": b"firmware",
        "assets/Scratch/index.html": "<html/>",
    }
    files.update(extra or {})
    return zip_bytes(files, symlinks)


def apk_bytes(cert: Any = FAKE_CERT) -> bytes:
    return zip_bytes({"AndroidManifest.xml": b"\x03\x00", "META-INF/CERT.RSA": pkcs7(cert), "classes.dex": b""})


def xapk(path, version_code="1204", obb=None, package="com.anki.cozmo", cert=FAKE_CERT):
    path.write_bytes(zip_bytes({
        "manifest.json": json.dumps({"package_name": package, "version_code": version_code,
                                     "version_name": "3.4.0"}),
        "com.anki.cozmo.apk": apk_bytes(cert),
        "Android/obb/com.anki.cozmo/main.{}.com.anki.cozmo.obb".format(version_code): obb or obb_bytes(),
    }))
    return path


@pytest.fixture(autouse=True)
def trust_fake_signer(monkeypatch):
    monkeypatch.setattr(resources, "ANKI_SIGNER_SHA256", hashlib.sha256(FAKE_CERT).hexdigest())


def test_signer_certificates():
    assert resources.signer_certificates(pkcs7(FAKE_CERT)) == [FAKE_CERT]
    with pytest.raises(ValueError):
        resources.signer_certificates(b"\x02\x01\x00")


def test_prepare_from_xapk(tmp_path):
    dest = tmp_path / "assets"
    stages = set()
    manifest = resources.prepare(xapk(tmp_path / "cozmo.xapk"), dest=dest,
                                 progress=lambda stage, fraction: stages.add(stage))
    assert (manifest.version_code, manifest.version_name) == (1204, "3.4.0")
    assert manifest.signer_sha256 == hashlib.sha256(FAKE_CERT).hexdigest()
    assert manifest.counts == {"animations": 1, "animation_groups": 1, "animation_triggers": 1,
                               "cube_animation_triggers": 0, "face_animation_frames": 1,
                               "sound_banks": 2, "wem_files": 2}
    # Synthetic packages differ from the reference ones.
    assert any("OBB differs" in w for w in manifest.warnings)
    assert any("Unexpected content" in w for w in manifest.warnings)
    assert "extract" in stages
    # Layout: sound banks in sound/AudioAssets, no zip, no TTS, no firmware, nothing outside cozmo_resources.
    assert (dest / "cozmo_resources/sound/AudioAssets/English(US)/2.wem").read_bytes() == b"RIFF"
    assert resources.audio_dir(dest) == dest / "cozmo_resources/sound/AudioAssets"
    assert not (dest / "cozmo_resources/sound/AudioAssets.zip").exists()
    assert not (dest / "cozmo_resources/tts").exists()
    assert not (dest / "cozmo_resources/config/engine").exists()
    assert sorted(p.name for p in dest.iterdir()) == ["cozmo_resources", "resources-manifest.json", "resources.txt"]
    assert resources.load_manifest(dest) == manifest
    assert resources.verify(dest) == []
    assert not list(tmp_path.glob(".assets.*"))


def test_prepare_from_apk_and_obb(tmp_path):
    apk = tmp_path / "cozmo.apk"
    apk.write_bytes(apk_bytes())
    obb = tmp_path / "main.1204.com.anki.cozmo.obb"
    obb.write_bytes(obb_bytes())
    with pytest.raises(resources.ResourceError, match="OBB"):
        resources.prepare(apk, dest=tmp_path / "a")
    manifest = resources.prepare(apk, obb=obb, dest=tmp_path / "a")
    assert manifest.version_code == 1204 and manifest.apk_sha256 == hashlib.sha256(apk.read_bytes()).hexdigest()


def test_prepare_from_obb_alone(tmp_path):
    obb = tmp_path / "main.1204.com.anki.cozmo.obb"
    obb.write_bytes(obb_bytes())
    manifest = resources.prepare(obb, dest=tmp_path / "a")
    assert manifest.signer_sha256 is None and manifest.apk_sha256 is None
    renamed = tmp_path / "cozmo.obb"
    renamed.write_bytes(obb_bytes())
    with pytest.raises(resources.ResourceError, match="version"):
        resources.prepare(renamed, dest=tmp_path / "b")
    manifest = resources.prepare(renamed, dest=tmp_path / "b", allow_other_version=True)
    assert manifest.version_code is None and manifest.warnings


def test_other_version_and_package(tmp_path):
    with pytest.raises(resources.ResourceError, match="1203"):
        resources.prepare(xapk(tmp_path / "old.xapk", version_code="1203"), dest=tmp_path / "a")
    manifest = resources.prepare(tmp_path / "old.xapk", dest=tmp_path / "a", allow_other_version=True)
    assert manifest.version_code == 1203 and any("1203" in w for w in manifest.warnings)
    with pytest.raises(resources.ResourceError, match="not the Cozmo application"):
        resources.prepare(xapk(tmp_path / "other.xapk", package="com.example"), dest=tmp_path / "b")


def test_other_signer(tmp_path):
    other = xapk(tmp_path / "resigned.xapk", cert=bytes.fromhex("3003020102"))
    with pytest.raises(resources.ResourceError, match="Anki"):
        resources.prepare(other, dest=tmp_path / "a")
    manifest = resources.prepare(other, dest=tmp_path / "a", allow_other_signer=True)
    assert any("Anki" in w for w in manifest.warnings)


@pytest.mark.parametrize("name", [
    "assets/cozmo_resources/../../evil.txt",
    "assets/cozmo_resources/a\\..\\..\\evil.txt",
])
def test_unsafe_paths_are_rejected(tmp_path, name):
    source = xapk(tmp_path / "bad.xapk", obb=obb_bytes({name: b"x"}))
    with pytest.raises(resources.ResourceError, match="Unsafe path"):
        resources.prepare(source, dest=tmp_path / "out" / "assets")
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "out" / "assets").exists()
    assert not list((tmp_path / "out").glob(".assets.*"))


def test_links_are_rejected(tmp_path):
    source = xapk(tmp_path / "bad.xapk", obb=obb_bytes(symlinks=["assets/cozmo_resources/link"]))
    with pytest.raises(resources.ResourceError, match="Links"):
        resources.prepare(source, dest=tmp_path / "a")


def test_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "MAX_EXTRACTED_BYTES", 100)
    with pytest.raises(resources.ResourceError, match="more than"):
        resources.prepare(xapk(tmp_path / "c.xapk"), dest=tmp_path / "a")


def test_unusable_inputs(tmp_path):
    truncated = tmp_path / "truncated.xapk"
    truncated.write_bytes(xapk(tmp_path / "ok.xapk").read_bytes()[:2000])
    with pytest.raises(resources.ResourceError):
        resources.prepare(truncated, dest=tmp_path / "a")
    other = tmp_path / "other.zip"
    other.write_bytes(zip_bytes({"readme.txt": "hello"}))
    with pytest.raises(resources.ResourceError, match="neither"):
        resources.prepare(other, dest=tmp_path / "a")
    incomplete = tmp_path / "main.1204.com.anki.cozmo.obb"
    incomplete.write_bytes(zip_bytes({"assets/resources.txt": "x"}))
    with pytest.raises(resources.ResourceError, match="missing"):
        resources.prepare(incomplete, dest=tmp_path / "a")
    assert not (tmp_path / "a").exists()


def test_existing_resources_need_force(tmp_path):
    dest = tmp_path / "assets"
    source = xapk(tmp_path / "c.xapk")
    resources.prepare(source, dest=dest)
    (dest / "cozmo_resources" / "stale.txt").write_text("old")
    with pytest.raises(resources.ResourceError, match="already prepared"):
        resources.prepare(source, dest=dest)
    resources.prepare(source, dest=dest, force=True, include_firmware=True)
    assert not (dest / "cozmo_resources" / "stale.txt").exists()
    assert (dest / "cozmo_resources/config/engine/firmware_1/cozmo.safe").read_bytes() == b"firmware"
    other = tmp_path / "busy"
    other.mkdir()
    (other / "notes.txt").write_text("mine")
    with pytest.raises(resources.ResourceError, match="not empty"):
        resources.prepare(source, dest=other)


def test_verify_detects_changes(tmp_path):
    dest = tmp_path / "assets"
    assert resources.verify(dest) == ["No resources in {}.".format(dest)]
    resources.prepare(xapk(tmp_path / "c.xapk"), dest=dest)
    (dest / "cozmo_resources/config/cozmo_anim.fbs").write_text("changed")
    assert resources.verify(dest) == ["File contents differ from the manifest."]
    (dest / "cozmo_resources/assets/animations/anim_a.bin").unlink()
    assert resources.verify(dest) == ["animations: 0 (manifest: 1)"]
    (dest / resources.MANIFEST_NAME).unlink()
    assert "older tool" in resources.verify(dest)[0]


def test_audio_dir_accepts_former_layout(tmp_path):
    (tmp_path / "cozmo_resources" / "sound").mkdir(parents=True)
    assert resources.audio_dir(tmp_path) == tmp_path / "cozmo_resources" / "sound"
