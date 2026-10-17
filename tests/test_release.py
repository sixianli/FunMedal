import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

from bridge_harness import ROOT, artifacts, bridge, card, run_dir, write_summary  # noqa: F401

ARTIFACTS_PREFIX = "fm16"
ARTIFACTS_SUBDIR = "release"
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_EMAIL = re.compile(rb"^(noreply@anthropic\.com|[A-Za-z0-9._%+-]+@users\.noreply\.github\.com)$")
USER_NAME = re.escape(Path.home().name.encode())
FINDINGS = {
    "user_name": re.compile(rb"(?<![A-Za-z0-9])" + USER_NAME + rb"(?![A-Za-z0-9])"),
    "chip_unique_id": re.compile(rb"Optional unique 128-bit ID\s*=\s*[0-9a-f]{2}(?: [0-9a-f]{2}){15}"),
    "hardware_address": re.compile(rb"(?<![0-9A-Fa-f:])[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}(?![0-9A-Fa-f:])"),
    "card_identity": re.compile(rb"trae_card_[0-9A-Fa-f]{12}"),
    "secret": re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
BACKUP_DIR = "firmware-backup/"
LOCAL_CARD_IDS = sorted({m.group(1).lower().encode() for f in (ROOT / BACKUP_DIR).glob("*")
                         if (m := re.search(r"trae_card_([0-9A-Fa-f]{12})", f.name))})
if LOCAL_CARD_IDS:
    FINDINGS["local_card_id"] = re.compile(b"|".join(map(re.escape, LOCAL_CARD_IDS)), re.IGNORECASE)
FLASH_IMAGE_BYTES = 4 * 1024 * 1024
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
SPEECH_ACCESS = re.compile(r"^[ \t]*(open .*--args speech-access)$", re.MULTILINE)
COMMAND_WAIT_S = 15


def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout


def history_files():
    seen = {}
    for commit in git("rev-list", "--all").decode().split():
        for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            _, kind, sha = meta.split()
            if kind == b"blob":
                seen.setdefault(sha.decode(), (path.decode(), commit[:7]))
    batch = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, capture_output=True, check=True,
                           input="".join(f"{sha}\n" for sha in seen).encode()).stdout
    position = 0
    for sha in seen:
        header_end = batch.index(b"\n", position)
        size = int(batch[position:header_end].split()[2])
        content = batch[header_end + 1:header_end + 1 + size]
        position = header_end + 1 + size + 1
        yield seen[sha][0], f"commit {seen[sha][1]}", content


def pending_files():
    names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    for name in filter(None, names):
        path = ROOT / name.decode()
        if path.is_file():
            yield name.decode(), "working tree", path.read_bytes()


def scan(files):
    found = defaultdict(set)
    for path, where, content in files:
        if path.startswith(BACKUP_DIR):
            found["firmware_backup"].add(f"{where}: {path}")
        if path.endswith(".bin") and len(content) >= FLASH_IMAGE_BYTES:
            found["flash_image"].add(f"{where}: {path}")
        for kind, pattern in FINDINGS.items():
            if pattern.search(content):
                found[kind].add(f"{where}: {path}")
        if any(not ALLOWED_EMAIL.match(m.group(0)) for m in EMAIL.finditer(content)):
            found["email"].add(f"{where}: {path}")
    return found


def commit_emails():
    lines = git("log", "--all", "--format=%H%x00%ae%x00%ce%x00%B%x01").split(b"\x01")
    found = set()
    for record in filter(lambda r: r.strip(), lines):
        commit, author, committer, message = record.strip().split(b"\0", 3)
        addresses = [author, committer] + [m.group(0) for m in EMAIL.finditer(message)]
        if any(not ALLOWED_EMAIL.match(a) for a in addresses):
            found.add(f"commit {commit[:7].decode()}")
    return found


@pytest.mark.parametrize("case", ["[FM-16] nothing private is in the files or the history that would be published"])
def test_nothing_private(case, run_dir):
    found = scan(list(history_files()) + list(pending_files()))
    emails = commit_emails()
    if emails:
        found["commit_email"] = emails
    report = {kind: {"count": len(where), "where": sorted(where)} for kind, where in sorted(found.items())}
    write_summary(run_dir, {"note": "only kinds and paths are recorded, never the matched text", "found": report})
    assert not report, {kind: entry["count"] for kind, entry in report.items()}


@pytest.mark.parametrize("case", ["[FM-16] every relative link in the README and the notes points at a file in the repo"])
def test_links_resolve(case, run_dir):
    documents = ["README.md", "MISSION.md"] + sorted(str(p.relative_to(ROOT)) for p in (ROOT / "notes").glob("*.md"))
    broken = []
    for document in documents:
        text = (ROOT / document).read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(text):
            if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                continue
            path = (ROOT / document).parent / target.split("#", 1)[0]
            if not path.exists():
                broken.append(f"{document}: {target}")
    write_summary(run_dir, {"documents": documents, "broken": broken})
    assert broken == [], broken


def documented_commands(pattern):
    documents = ["README.md"] + sorted(str(p.relative_to(ROOT)) for p in (ROOT / "notes").glob("*.md"))
    return [(document, line) for document in documents
            for line in pattern.findall((ROOT / document).read_text(encoding="utf-8"))]


@pytest.mark.parametrize("case", ["[FM-16] the documented speech-access command runs while the bridge is serving"])
def test_speech_access_while_serving(case, bridge, run_dir):
    bridge.start()
    commands = documented_commands(SPEECH_ACCESS)
    runs = []
    for index, (document, line) in enumerate(commands):
        report = run_dir / f"speech-access-{index}.json"
        process = subprocess.Popen(shlex.split(line) + ["--log", str(report)], cwd=ROOT)
        try:
            code = process.wait(timeout=COMMAND_WAIT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = None
        runs.append({"document": document, "command": line, "exit": code,
                     "report": json.loads(report.read_text(encoding="utf-8")) if report.exists() else None})
    serving = bridge.status()
    write_summary(run_dir, {"runs": runs, "bridge_after": serving})
    assert len(commands) >= 2, commands
    assert all(run["exit"] == 0 and run["report"] is not None for run in runs), runs
    assert serving is not None and serving["card"] == "connected", serving


@pytest.mark.parametrize("case", ["[FM-16] a fresh clone builds the bridge and the firmware by the README steps"])
def test_fresh_clone_builds(case, run_dir):
    if git("status", "--porcelain", "--", ".", ":!artifacts", ":!.agents").strip():
        pytest.fail("commit first: a fresh clone only has committed files")
    idf = Path(os.environ.get("FM_IDF_EXPORT", Path.home() / "esp" / "esp-idf-v5.5.5" / "export.sh"))
    if not idf.exists():
        pytest.fail(f"ESP-IDF not found at {idf}")
    workdir = Path(tempfile.mkdtemp(prefix="fm-clone-"))
    clone = workdir / "FunMedal"
    record = {"commit": git("rev-parse", "HEAD").decode().strip()}
    try:
        subprocess.run(["git", "clone", "--quiet", str(ROOT), str(clone)], check=True)
        steps = {
            "bridge": ["sh", "mac/bridge/build.sh"],
            "firmware": ["bash", "-c", f"export IDF_TOOLS_PATH=$HOME/esp/.espressif && . {idf} >/dev/null && "
                                       "cd firmware && idf.py build"],
        }
        for name, argv in steps.items():
            result = subprocess.run(argv, cwd=clone, capture_output=True, timeout=900)
            (run_dir / f"{name}.log").write_bytes(result.stdout + result.stderr)
            record[name] = result.returncode
        bridge = clone / "mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge"
        image = clone / "firmware/build/funmedal.bin"
        record["bridge_built"] = bridge.exists()
        record["firmware_bytes"] = image.stat().st_size if image.exists() else 0
        if bridge.exists():
            usage = subprocess.run([str(bridge)], capture_output=True)
            record["bridge_usage"] = usage.stderr.decode().strip()
        record["backup_in_clone"] = (clone / "firmware-backup").exists()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        write_summary(run_dir, record)
    assert record["bridge"] == 0 and record["bridge_built"], record
    assert record["firmware"] == 0 and record["firmware_bytes"] > 0, record
    assert "channel" in record["bridge_usage"] and not record["backup_in_clone"], record
