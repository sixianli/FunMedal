import hashlib
import os
import random
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from itertools import chain
from pathlib import Path

import pytest

from bridge_harness import ROOT, artifacts, run_dir, write_summary  # noqa: F401

ARTIFACTS_PREFIX = "fm16"
ARTIFACTS_SUBDIR = "release"
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_EMAIL = re.compile(rb"^(noreply@anthropic\.com|[A-Za-z0-9._%+-]+@users\.noreply\.github\.com)$")
USER_NAME = re.escape(Path.home().name.encode())
FINDINGS = {
    "user_name": re.compile(rb"(?<![A-Za-z0-9])" + USER_NAME + rb"(?![A-Za-z0-9])", re.IGNORECASE),
    "chip_unique_id": re.compile(rb"Optional unique 128-bit ID\s*=\s*[0-9a-f]{2}(?: [0-9a-f]{2}){15}"),
    "hardware_address": re.compile(rb"(?<![0-9A-Fa-f])(?<![0-9A-Fa-f][:-])[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}"
                                   rb"(?:\1[0-9A-Fa-f]{2}){4}(?![0-9A-Fa-f])(?![:-][0-9A-Fa-f])"),
    "card_identity": re.compile(rb"trae_card_[0-9A-Fa-f]{12}", re.IGNORECASE),
    "secret": re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
BACKUP_DIR = "firmware-backup/"
CARD_ID_IN_NAME = re.compile(r"trae_card_([0-9A-Fa-f]{12})", re.IGNORECASE)
FLASH_IMAGE_BYTES = 4 * 1024 * 1024
BLOCK_BYTES = 4096
FONT_FILES = ["third_party/noto-sans-sc/NotoSansSC-Regular.otf",
              "third_party/fusion-pixel/fusion-pixel-12px-proportional-zh_hans.bdf",
              "third_party/zcool-kuaile/ZCOOLKuaiLe-Regular.ttf"]
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
BRIDGE_MODES = "serve|hook|status|quit|doctor"


def git(*args, cwd=ROOT, data=None):
    return subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True, check=True).stdout


def backup_files(backup_dir):
    return sorted(p for p in Path(backup_dir).rglob("*") if p.is_file()) if Path(backup_dir).is_dir() else []


def finding_patterns(backup_dir):
    ids = sorted({m.group(1).lower().encode() for f in backup_files(backup_dir) if (m := CARD_ID_IN_NAME.search(f.name))})
    if not ids:
        return FINDINGS
    return {**FINDINGS, "local_card_id": re.compile(b"|".join(map(re.escape, ids)), re.IGNORECASE)}


def blocks(content):
    return (content[start:start + BLOCK_BYTES] for start in range(0, len(content) - BLOCK_BYTES + 1, BLOCK_BYTES))


def backup_blocks(backup_dir):
    return {hashlib.sha256(block).digest() for f in backup_files(backup_dir) for block in blocks(f.read_bytes())
            if block.count(block[:1]) != BLOCK_BYTES}


def blob_contents(root, shas):
    batch = git("cat-file", "--batch", cwd=root, data="".join(f"{sha}\n" for sha in shas).encode())
    contents, position = {}, 0
    for sha in shas:
        header_end = batch.index(b"\n", position)
        size = int(batch[position:header_end].split()[2])
        contents[sha] = batch[header_end + 1:header_end + 1 + size]
        position = header_end + 1 + size + 1
    return contents


def history_files(root):
    places = {}
    for commit in git("rev-list", "--reverse", "--all", cwd=root).decode().split():
        for entry in filter(None, git("ls-tree", "-r", "-z", commit, cwd=root).split(b"\0")):
            meta, path = entry.split(b"\t", 1)
            _, kind, sha = meta.split()
            if kind == b"blob":
                places.setdefault((sha.decode(), path.decode()), commit[:7])
    contents = blob_contents(root, list(dict.fromkeys(sha for sha, _ in places)))
    for (sha, path), commit in places.items():
        yield path, f"commit {commit}", contents[sha]


def index_files(root):
    entries = []
    for entry in filter(None, git("ls-files", "-s", "-z", cwd=root).split(b"\0")):
        meta, path = entry.split(b"\t", 1)
        mode, sha, _ = meta.split()
        if mode != b"160000":
            entries.append((path.decode(), sha.decode()))
    contents = blob_contents(root, list(dict.fromkeys(sha for _, sha in entries)))
    for path, sha in entries:
        yield path, "index", contents[sha]


def working_files(root):
    for name in filter(None, git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=root).split(b"\0")):
        path = root / name.decode()
        if path.is_symlink():
            yield name.decode(), "working tree", os.fsencode(os.readlink(path))
        elif path.is_file():
            yield name.decode(), "working tree", path.read_bytes()


def commit_and_tag_fields(root):
    log = git("log", "--all", "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x01", cwd=root)
    for record in filter(bytes.strip, log.split(b"\x01")):
        commit, *fields = record.strip().split(b"\0", 3)
        for name, value in zip(("author", "committer", "message"), fields):
            yield f"commit {commit[:7].decode()}: {name}", value
    tags = git("for-each-ref", "refs/tags", "--format=%(refname:short)%00%(taggername) %(taggeremail)%00%(contents)%01",
               cwd=root)
    for record in filter(bytes.strip, tags.split(b"\x01")):
        tag, *fields = record.strip().split(b"\0", 2)
        for name, value in zip(("tagger", "message"), fields):
            yield f"tag {tag.decode()}: {name}", value


def private_findings(root, backup_dir):
    patterns = finding_patterns(backup_dir)
    digests = backup_blocks(backup_dir)
    found = defaultdict(set)

    def look(place, value):
        for kind, pattern in patterns.items():
            if pattern.search(value):
                found[kind].add(place)
        if any(not ALLOWED_EMAIL.match(m.group(0)) for m in EMAIL.finditer(value)):
            found["email"].add(place)

    for path, where, content in chain(history_files(root), index_files(root), working_files(root)):
        place = f"{where}: {path}"
        look(place, path.encode())
        look(place, content)
        if path.startswith(BACKUP_DIR):
            found["firmware_backup"].add(place)
        if path.endswith(".bin") and len(content) >= FLASH_IMAGE_BYTES:
            found["flash_image"].add(place)
        if any(hashlib.sha256(block).digest() in digests for block in blocks(content)):
            found["backup_content"].add(place)
    for place, value in commit_and_tag_fields(root):
        look(place, value)
    return found


@pytest.mark.parametrize("case", ["[FM-16] the privacy check finds data planted in commit and tag details, paths, links, the index and partial dumps"])
def test_privacy_check_finds_planted_data(case, run_dir, tmp_path):
    repo, backup = tmp_path / "repo", tmp_path / "backup"
    backup.mkdir()
    dump = random.Random(7).randbytes(2 * BLOCK_BYTES)
    (backup / "factory-full8MB.bin").write_bytes(dump)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    address_start = ":".join(["aa", "bb", "cc", "dd", "ee"])
    log_name = "logs/" + "_".join(["TRAE", "CARD", "0123456789AB"]) + ".txt"

    def commit(message, author="Someone"):
        git("add", "-A", cwd=repo)
        git("-c", f"user.name={author}", "-c", "user.email=1+someone@users.noreply.github.com",
            "commit", "-q", "-m", message, cwd=repo)
        return git("rev-parse", "--short=7", "HEAD", cwd=repo).decode().strip()

    (repo / "clean.txt").write_text("nothing to see\n")
    by_user = commit("start", author=Path.home().name)
    (repo / "notes.txt").write_text("more\n")
    with_address = commit(f"seen at {address_start}:01")
    (repo / "logs").mkdir()
    (repo / log_name).write_text("ok\n")
    with_identity = commit("add a log")
    (repo / "dash.txt").write_text("-".join(["AA", "BB", "CC", "DD", "EE", "03"]) + "\n")
    (repo / "case.txt").write_text(f"/Users/{Path.home().name.upper()}/work\n")
    with_variants = commit("more notes")
    git("-c", f"user.name={Path.home().name}", "-c", "user.email=1+someone@users.noreply.github.com",
        "tag", "-a", "v0", "-m", "first", cwd=repo)
    (repo / "link").symlink_to(f"/Users/{Path.home().name}/nowhere")
    (repo / "staged.txt").write_text(f"peer {address_start}:02\n")
    (repo / "part.bin").write_bytes(dump[BLOCK_BYTES:])
    git("add", "staged.txt", "part.bin", cwd=repo)
    (repo / "staged.txt").write_text("clean now\n")
    found = private_findings(repo, backup)
    got = {(kind, where) for kind, places in found.items() for where in places}
    write_summary(run_dir, {"found": sorted(got)})
    expected = {
        ("user_name", f"commit {by_user}: author"),
        ("hardware_address", f"commit {with_address}: message"),
        ("card_identity", f"commit {with_identity}: {log_name}"),
        ("hardware_address", f"commit {with_variants}: dash.txt"),
        ("user_name", f"commit {with_variants}: case.txt"),
        ("user_name", "tag v0: tagger"),
        ("user_name", "working tree: link"),
        ("hardware_address", "index: staged.txt"),
        ("backup_content", "index: part.bin"),
        ("backup_content", "working tree: part.bin"),
    }
    assert expected <= got, sorted(expected - got)
    assert not any("clean.txt" in where for _, where in got), sorted(got)


@pytest.mark.parametrize("case", ["[FM-16] nothing private is in the files or the history that would be published"])
def test_nothing_private(case, run_dir):
    found = private_findings(ROOT, ROOT / BACKUP_DIR)
    report = {kind: {"count": len(where), "where": sorted(where)} for kind, where in sorted(found.items())}
    backups = len(backup_files(ROOT / BACKUP_DIR))
    write_summary(run_dir, {"note": "only kinds and paths are recorded, never the matched text",
                            "backup_files": backups, "found": report})
    assert backups > 0, f"no flash backup under {BACKUP_DIR}: README step 1 makes one, and without it the backup check finds nothing"
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
        for font in FONT_FILES:
            shutil.copy(ROOT / font, clone / font)
        steps = {
            "fonts": ["sh", "tools/fetch-fonts.sh"],
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
    assert record["fonts"] == 0, record
    assert record["firmware"] == 0 and record["firmware_bytes"] > 0, record
    assert BRIDGE_MODES in record["bridge_usage"] and not record["backup_in_clone"], record
