import plistlib
import re
import shutil
import subprocess
import uuid

import pytest

from bridge_harness import APP, ROOT, Bridge, artifacts, card, require_bridge_app, run_dir, write_summary  # noqa: F401

ARTIFACTS_PREFIX = "ob10"
ARTIFACTS_SUBDIR = "signing"
CERTIFICATE = "Apple Development: "
CERTIFICATE_NAME = re.compile(r'"Apple Development: [^"]*"')


def signature(app):
    shown = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(app)],
                           capture_output=True, text=True, check=True).stderr.splitlines()
    required = subprocess.run(["/usr/bin/codesign", "-d", "-r-", str(app)], capture_output=True, text=True, check=True)
    designated = next(line.removeprefix("# ") for line in (required.stdout + required.stderr).splitlines()
                      if line.removeprefix("# ").startswith("designated => "))
    return {"adhoc": "Signature=adhoc" in shown, "authority": field(shown, "Authority"),
            "cdhash": field(shown, "CDHash"), "designated": designated}


def field(lines, key):
    return next((line.split("=", 1)[1] for line in lines if line.startswith(f"{key}=")), None)


def shareable(signed):
    return {"adhoc": signed["adhoc"], "certificate": (signed["authority"] or "").startswith(CERTIFICATE),
            "cdhash": signed["cdhash"], "designated": CERTIFICATE_NAME.sub('"Apple Development: <name>"',
                                                                        signed["designated"])}


def build_changed_copy(workdir, run_dir):
    source = workdir / "bridge"
    shutil.copytree(ROOT / "mac" / "bridge", source, ignore=shutil.ignore_patterns("build"))
    info = source / "Info.plist"
    plist = plistlib.loads(info.read_bytes())
    plist["FunMedalSigningProbe"] = uuid.uuid4().hex
    info.write_bytes(plistlib.dumps(plist))
    built = subprocess.run(["sh", str(source / "build.sh")], capture_output=True, text=True)
    (run_dir / "build.txt").write_text(built.stdout + built.stderr, encoding="utf-8")
    assert built.returncode == 0, built.stderr
    return (source / "build" / "FunMedalBridge.app").resolve()


def running_program(pid):
    return subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("case", ["[OB-10] a bridge rebuilt with different content is signed with the same "
                                  "certificate rule and gets Bluetooth access without asking again"])
def test_rebuilt_bridge_keeps_access(case, card, run_dir, tmp_path):
    require_bridge_app()
    repo = signature(APP)
    rebuilt_app = build_changed_copy(tmp_path, run_dir)
    rebuilt = signature(rebuilt_app)
    summary = {"repo": shareable(repo), "rebuilt": shareable(rebuilt)}
    write_summary(run_dir, summary)
    for signed in (summary["repo"], summary["rebuilt"]):
        assert not signed["adhoc"] and signed["certificate"], summary
        assert "cdhash" not in signed["designated"], summary
    assert repo["designated"] == rebuilt["designated"] and repo["cdhash"] != rebuilt["cdhash"], summary
    bridge = Bridge(run_dir, app=rebuilt_app)
    try:
        summary["status"] = bridge.start()
        summary["program"] = running_program(summary["status"]["pid"])
    finally:
        write_summary(run_dir, summary)
        bridge.quit()
    status = summary["status"]
    assert summary["program"] == str(rebuilt_app / "Contents" / "MacOS" / "funmedal-bridge"), summary
    assert status["authorization"] == "allowedAlways", summary
