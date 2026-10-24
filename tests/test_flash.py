import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from bridge_harness import (ROOT, Bridge, CardWatcher, _try_info, artifacts, bridge_cli, info_fields,  # noqa: F401
                            require_bridge_app, require_resident_locked_out, run_dir, stop_leftover_bridges, wait_until,
                            write_summary)

ARTIFACTS_PREFIX = "ob15"
ARTIFACTS_SUBDIR = "flash"
FLASH = ROOT / "flash.sh"
PREBUILT = ROOT / "firmware" / "prebuilt"
PREBUILT_FILES = ["bootloader.bin", "partition-table.bin", "funmedal.bin"]
FLASH_BYTES = 8 * 1024 * 1024
NEW_BACKUP = "firmware-backup/factory-full8MB.bin"
WRITE_OPTIONS = ["--flash_mode", "dio", "--flash_freq", "80m", "--flash_size", "8MB"]
WRITE_FILES = {"0x0": "bootloader.bin", "0x8000": "partition-table.bin", "0x10000": "funmedal.bin"}
FAKE_ESPTOOL = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_ESPTOOL_LOG"]
with open(log, "a", encoding="utf-8") as out:
    out.write(json.dumps({{"args": args, "cwd": os.getcwd()}}) + "\\n")
calls = sum(1 for _ in open(log, encoding="utf-8"))
mode = os.environ.get("FAKE_ESPTOOL", "")
if "read_flash" in args:
    if mode == "read-fail":
        sys.exit(2)
    image = bytearray(b"\\xa5" * {FLASH_BYTES})
    if mode == "reads-differ" and calls > 1:
        image[1234] = 0
    with open(args[-1], "wb") as out:
        out.write(image[: {FLASH_BYTES} // 2] if mode == "read-short" else image)
if "write_flash" in args and mode == "write-fail":
    sys.exit(2)
"""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def layout(tmp_path):
    repo = tmp_path / "repo"
    (repo / "firmware").mkdir(parents=True)
    shutil.copy(FLASH, repo / "flash.sh")
    shutil.copytree(PREBUILT, repo / "firmware" / "prebuilt")
    return repo


def run_flash(tmp_path, repo, mode="", with_esptool=True, name="esptool"):
    tools = tmp_path / "bin"
    tools.mkdir(exist_ok=True)
    calls = tmp_path / "esptool-calls.jsonl"
    if with_esptool:
        fake = tools / name
        fake.write_text(FAKE_ESPTOOL, encoding="utf-8")
        fake.chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin", "LC_CTYPE": "C.UTF-8",
           "FAKE_ESPTOOL_LOG": str(calls), "FAKE_ESPTOOL": mode}
    done = subprocess.run(["sh", str(repo / "flash.sh"), "/dev/cu.fake"], env=env, capture_output=True, text=True,
                          timeout=60)
    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
    backups = sorted(str(p.relative_to(repo)) for p in (repo / "firmware-backup").glob("*")) \
        if (repo / "firmware-backup").exists() else []
    return {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "backups": backups,
            "commands": [next((a for a in c["args"] if a in ("read_flash", "write_flash")), None) for c in recorded],
            "calls": recorded}


def write_args(result):
    call = next(c for c in result["calls"] if "write_flash" in c["args"])
    return call["args"][call["args"].index("write_flash") + 1:], call["cwd"]


def split_write(args):
    options, rest = args[:len(WRITE_OPTIONS)], args[len(WRITE_OPTIONS):]
    return options, dict(zip(rest[0::2], rest[1::2])), len(rest) % 2


def keep_backup(repo, name="kept.bin", good=True):
    folder = repo / "firmware-backup"
    folder.mkdir()
    image = folder / name
    image.write_bytes(b"\x5a" * FLASH_BYTES)
    digest = sha256(image) if good else "0" * 64
    (folder / f"{name}.sha256").write_text(f"{digest}  {image}\n", encoding="utf-8")
    return image


@pytest.mark.parametrize("case", ["[OB-15] with no backup yet, flash.sh reads the whole flash twice, keeps it with its "
                                  "SHA-256 once both reads match, and then writes the three prebuilt files"])
def test_backs_up_then_writes(case, run_dir, tmp_path):
    repo = layout(tmp_path)
    result = run_flash(tmp_path, repo)
    write_summary(run_dir, {k: v for k, v in result.items() if k != "calls"})
    assert result["exit"] == 0, result
    assert result["commands"] == ["read_flash", "read_flash", "write_flash"], result
    assert result["backups"] == [NEW_BACKUP, f"{NEW_BACKUP}.sha256"], result
    backup = repo / NEW_BACKUP
    assert backup.stat().st_size == FLASH_BYTES, result
    recorded = (repo / f"{NEW_BACKUP}.sha256").read_text(encoding="utf-8").split()
    assert recorded == [sha256(backup), NEW_BACKUP], recorded
    args, cwd = write_args(result)
    assert split_write(args) == (WRITE_OPTIONS, WRITE_FILES, 0) and os.path.realpath(cwd) == os.path.realpath(repo / "firmware" / "prebuilt"), result
    reads = [c["args"] for c in result["calls"] if "read_flash" in c["args"]]
    assert all(a[:4] == ["--chip", "esp32c3", "-p", "/dev/cu.fake"] and a[4:7] == ["read_flash", "0", "0x800000"]
               for a in reads), reads


@pytest.mark.parametrize("case", ["[OB-15] with a backup already kept, flash.sh checks its SHA-256 and writes without "
                                  "reading the card again"])
def test_kept_backup_is_checked(case, run_dir, tmp_path):
    repo = layout(tmp_path)
    keep_backup(repo, "trae_card_full8MB.bin")
    result = run_flash(tmp_path, repo, name="esptool.py")
    write_summary(run_dir, {k: v for k, v in result.items() if k != "calls"})
    assert result["exit"] == 0, result
    assert result["commands"] == ["write_flash"], result
    assert result["backups"] == ["firmware-backup/trae_card_full8MB.bin", "firmware-backup/trae_card_full8MB.bin.sha256"]


@pytest.mark.parametrize("case, way", [
    ("[OB-15] flash.sh stops before touching the card when esptool is not installed", "no-esptool"),
    ("[OB-15] flash.sh stops without writing when reading the flash fails", "read-fail"),
    ("[OB-15] flash.sh stops without writing and keeps no backup when the two reads differ", "reads-differ"),
    ("[OB-15] flash.sh stops without writing and keeps no backup when a read comes back short", "read-short"),
    ("[OB-15] flash.sh stops without writing when a kept backup no longer matches its SHA-256", "backup-damaged"),
    ("[OB-15] flash.sh stops without writing when a kept backup has no SHA-256 file", "backup-unchecked"),
    ("[OB-15] flash.sh stops before touching the card when a prebuilt file is damaged", "prebuilt-damaged"),
    ("[OB-15] flash.sh reports a failed write and exits non-zero", "write-fail"),
])
def test_stops_on_trouble(case, way, run_dir, tmp_path):
    repo = layout(tmp_path)
    mode = way if way in ("read-fail", "reads-differ", "read-short", "write-fail") else ""
    if way == "backup-damaged":
        keep_backup(repo, good=False)
    if way == "backup-unchecked":
        keep_backup(repo).with_name("kept.bin.sha256").unlink()
    if way == "prebuilt-damaged":
        with (repo / "firmware" / "prebuilt" / "funmedal.bin").open("ab") as image:
            image.write(b"\0")
    result = run_flash(tmp_path, repo, mode=mode, with_esptool=way != "no-esptool")
    write_summary(run_dir, {k: v for k, v in result.items() if k != "calls"})
    expected = {"no-esptool": [], "read-fail": ["read_flash"], "reads-differ": ["read_flash", "read_flash"],
                "read-short": ["read_flash", "read_flash"], "backup-damaged": [], "backup-unchecked": [],
                "prebuilt-damaged": [], "write-fail": ["read_flash", "read_flash", "write_flash"]}[way]
    assert result["exit"] != 0 and result["stderr"].strip(), result
    assert result["commands"] == expected, result
    if way in ("read-fail", "reads-differ", "read-short"):
        assert result["backups"] == [], result
    if way == "no-esptool":
        assert "brew install esptool" in result["stderr"] and not (repo / "firmware-backup").exists(), result


@pytest.mark.parametrize("case", ["[OB-15] the prebuilt files match their checksums and come from the firmware "
                                  "source as it is now"])
def test_prebuilt_is_current(case, run_dir):
    sums = dict(reversed(line.split()) for line in (PREBUILT / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
    source = (PREBUILT / "SOURCE").read_text(encoding="utf-8").strip()
    args = (PREBUILT / "flash_args").read_text(encoding="utf-8").split()
    changed = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", source, "--", "firmware",
                              ":(exclude)firmware/prebuilt"], capture_output=True, text=True)
    untracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard", "--", "firmware",
                                ":(exclude)firmware/prebuilt"], capture_output=True, text=True)
    summary = {"source": source, "listed": sorted(sums), "flash_args": args, "diff_exit": changed.returncode,
               "changed": changed.stdout.split(), "untracked": untracked.stdout.split(),
               "matching": {name: sums.get(name) == sha256(PREBUILT / name) for name in PREBUILT_FILES}}
    write_summary(run_dir, summary)
    assert sorted(sums) == sorted(PREBUILT_FILES) and all(summary["matching"].values()), summary
    assert split_write(args) == (WRITE_OPTIONS, WRITE_FILES, 0), summary
    assert summary["diff_exit"] == 0 and summary["changed"] == [] and summary["untracked"] == [], summary


@pytest.mark.parametrize("case", ["[OB-15] flash.sh with esptool alone writes the prebuilt firmware to the card, which "
                                  "boots it, keeps its pairing and lets the bridge connect"])
def test_real_card(case, run_dir):
    ports = glob.glob("/dev/cu.usbmodem*")
    if not ports:
        pytest.skip("card USB serial port not found")
    esptool_dirs = glob.glob(os.path.expanduser("~/esp/.espressif/python_env/*/bin"))
    if not esptool_dirs:
        pytest.skip("no esptool from an ESP-IDF install to stand in for one installed with brew or pip")
    require_bridge_app()
    resident, _ = bridge_cli("status")
    if resident.returncode == 0:
        pytest.fail("a bridge is running on the default socket and holds the card; quit it first")
    summary = {}
    require_resident_locked_out()
    stop_leftover_bridges()
    env = {"HOME": os.environ["HOME"], "PATH": f"{esptool_dirs[0]}:/usr/bin:/bin", "LC_CTYPE": "C.UTF-8"}
    done = subprocess.run(["sh", str(FLASH), ports[0]], env=env, capture_output=True, text=True, timeout=300)
    (run_dir / "flash-output.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    summary["exit"] = done.returncode
    summary["verified"] = done.stdout.count("Hash of data verified")
    watcher = CardWatcher()
    watcher.start()
    try:
        booted = info_fields(wait_until(lambda: _try_info(watcher), 20.0, "card answering after the flash"))
        bridge = Bridge(run_dir)
        try:
            summary["status"] = bridge.start()
            after = info_fields(watcher.request("info"))
        finally:
            bridge.quit()
    finally:
        watcher.close()
    summary.update({"fw": booted["fw"], "sec": after["sec"], "link": after["link"]})
    write_summary(run_dir, summary)
    assert summary["exit"] == 0 and summary["verified"] == len(PREBUILT_FILES), summary
    assert summary["fw"].startswith("funmedal-"), summary
    assert summary["status"]["card"] == "connected" and summary["status"]["connects"] == 1, summary
    assert summary["sec"] == "encrypted,authenticated,bonded" and summary["link"] == "match", summary
