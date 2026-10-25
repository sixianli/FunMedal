import hashlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import pngio  # noqa: E402

GEN_ASSETS = ROOT / "tools" / "gen_assets.py"
DEFAULT_PACK = ROOT / "assets" / "default"
PRIVATE = ROOT / "private-assets"
PRIVATE_PACK = PRIVATE / "pack"
PREBUILT = ROOT / "firmware" / "prebuilt" / "funmedal.bin"
POSES = ["01-lying", "02-lying-sleepy", "03-tap-up", "04-tap-down", "05-tilt",
         "06-mumble", "07-call", "08-worry", "09-wave", "10-cheer"]
BOXES = [(64, 74), (84, 108)]


def generate(pack, out):
    return subprocess.run([sys.executable, str(GEN_ASSETS), "--pack", str(pack), "--out", str(out)],
                          capture_output=True, text=True)


def parse(blob):
    assert blob[:4] == b"FMA1"
    images, sizes = blob[4], blob[5]
    sprites = []
    for i in range(images * sizes):
        w, h, offset = struct.unpack_from("<HHI", blob, 8 + i * 8)
        end = offset + w * h * 2 + (w * h + 1) // 2
        assert end <= len(blob)
        sprites.append((w, h, blob[offset:end]))
    return images, sizes, sprites


@pytest.fixture(scope="module")
def default_blob(tmp_path_factory):
    out = tmp_path_factory.mktemp("default-assets")
    done = generate(DEFAULT_PACK, out)
    assert done.returncode == 0, done.stdout + done.stderr
    return (out / "character.bin").read_bytes()


def copy_pack(source, target):
    shutil.copytree(source, target)
    return target


@pytest.mark.parametrize("case", ["[SC-15] the default pack in the repository builds ten poses in a 64 by 74 and "
                                  "an 84 by 108 box, each fitting its box and touching it on at least one side"])
def test_default_pack_builds(case, default_blob):
    images, sizes, sprites = parse(default_blob)
    assert (images, sizes) == (len(POSES), len(BOXES))
    for size, (bw, bh) in enumerate(BOXES):
        fitted = [sprites[i * sizes + size][:2] for i in range(images)]
        assert all(0 < w <= bw and 0 < h <= bh for w, h in fitted), fitted
        assert max(w for w, _ in fitted) == bw or max(h for _, h in fitted) == bh, fitted


@pytest.mark.parametrize("case", ["[SC-15] building from another pack directory gives different bitmaps, so the "
                                  "pack really comes from the directory passed in"])
def test_other_pack_builds_other_bitmaps(case, default_blob, tmp_path):
    pack = copy_pack(DEFAULT_PACK, tmp_path / "pack")
    for name in POSES:
        path = pack / "character" / f"{name}.png"
        w, h, rgba = pngio.read_rgba(path)
        inverted = bytes(255 - v if i % 4 != 3 else v for i, v in enumerate(rgba))
        pngio.write_rgba(path, w, h, inverted)
    done = generate(pack, tmp_path / "out")
    assert done.returncode == 0, done.stdout + done.stderr
    other = (tmp_path / "out" / "character.bin").read_bytes()
    assert [s[:2] for s in parse(other)[2]] == [s[:2] for s in parse(default_blob)[2]]
    assert all(a[2] != b[2] for a, b in zip(parse(other)[2], parse(default_blob)[2]))


@pytest.mark.parametrize("case", ["[SC-15] a pose without transparency, fully opaque, or missing stops the build "
                                  "with a message naming the file instead of drawing a white box"])
@pytest.mark.parametrize("damage", ["no-alpha", "opaque", "missing"])
def test_bad_pose_stops_the_build(case, damage, tmp_path):
    pack = copy_pack(DEFAULT_PACK, tmp_path / "pack")
    path = pack / "character" / "05-tilt.png"
    w, h, rgba = pngio.read_rgba(path)
    if damage == "no-alpha":
        pngio.write_rgb(path, w, h, bytes(v for i, v in enumerate(rgba) if i % 4 != 3))
    elif damage == "opaque":
        pngio.write_rgba(path, w, h, bytes(255 if i % 4 == 3 else v for i, v in enumerate(rgba)))
    else:
        path.unlink()
    done = generate(pack, tmp_path / "out")
    assert done.returncode != 0
    assert "05-tilt.png" in done.stderr, done.stderr
    assert not (tmp_path / "out" / "character.bin").exists()


def private_files():
    if not PRIVATE.is_dir():
        pytest.skip("this Mac has no private-assets directory")
    return [p for p in PRIVATE.rglob("*") if p.is_file() and p.name != ".DS_Store"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("case", ["[SC-15] no file tracked in git has the same bytes as any file under "
                                  "private-assets"])
def test_repository_holds_no_private_file(case):
    private = {sha(p): p for p in private_files()}
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    tracked = [ROOT / name for name in listed.decode().split("\0") if name]
    same = [(str(p.relative_to(ROOT)), str(private[sha(p)].relative_to(ROOT)))
            for p in tracked if p.is_file() and sha(p) in private]
    assert same == []


@pytest.mark.parametrize("case", ["[SC-15] the prebuilt firmware carries the default pack's bitmaps and none of "
                                  "the private pack's"])
def test_prebuilt_uses_the_default_pack(case, default_blob, tmp_path):
    firmware = PREBUILT.read_bytes()
    assert all(s[2] in firmware for s in parse(default_blob)[2])
    if not PRIVATE_PACK.is_dir():
        pytest.skip("this Mac has no private-assets/pack directory")
    done = generate(PRIVATE_PACK, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    private = parse((tmp_path / "character.bin").read_bytes())[2]
    assert [s[:2] for s in private if s[2] in firmware] == []
