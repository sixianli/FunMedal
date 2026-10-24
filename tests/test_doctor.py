import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from bridge_harness import APP, BRIDGE, artifacts, require_bridge_app, run_dir, write_summary  # noqa: F401
from test_plugin import desktop_claude_path

ARTIFACTS_PREFIX = "ob11"
ARTIFACTS_SUBDIR = "doctor"
TERMINAL_LINE = "Claude Code 版本（终端）"
DESKTOP_LINE = "Claude Code 版本（桌面 App）"
CHECKS = ["桥接程序", "蓝牙", "签名", "卡片", "固件版本", "插件", "hooks 开关", TERMINAL_LINE, DESKTOP_LINE]
LINE = re.compile(r"^(好|注意|不好|不知道) (.+?)：(.*)$")
GOOD_STATUS = {"ok": True, "pid": 4242, "bluetooth": "poweredOn", "authorization": "allowedAlways",
               "card": "connected", "card_version": 2, "bridge_version": 2}
GOOD_PLUGINS = [{"id": "funmedal@funmedal", "enabled": True}, {"id": "other@elsewhere", "enabled": True}]
GOOD = {"status": GOOD_STATUS, "version": "2.1.292 (Claude Code)", "plugins": GOOD_PLUGINS, "settings": {},
        "app": "copy", "claude": True, "desktop": {}}
GOOD_MARKS = {name: "好" for name in CHECKS}
FAKE_CLAUDE = """#!/bin/sh
if [ "$*" = "--version" ]; then printf '%s\\n' "$FAKE_CLAUDE_VERSION"; exit 0; fi
if [ "$*" = "plugin list --json" ]; then printf '%s\\n' "$FAKE_CLAUDE_PLUGINS"; exit 0; fi
exit 1
"""
DESKTOP_FAKE = """#!/bin/sh
if [ "$*" = "--version" ]; then {answer}; fi
if [ "$*" = "plugin list --json" ]; then printf '%s\\n' "$FAKE_CLAUDE_PLUGINS"; exit 0; fi
exit 1
"""
UNKNOWN_CARD = {"蓝牙": "不知道", "卡片": "不知道", "固件版本": "不知道"}


class FakeBridge:
    def __init__(self, status):
        self.path = os.path.join(tempfile.gettempdir(), f"fm-doctor-{uuid.uuid4().hex[:8]}.sock")
        self.status = status
        self.asked = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(4)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            with client:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.asked.append(json.loads(data))
                client.sendall(json.dumps(self.status).encode() + b"\n")

    def close(self):
        self.server.close()
        os.unlink(self.path)


def make_app(home, kind):
    if kind == "missing":
        return
    target = home / "Applications" / "FunMedalBridge.app"
    target.parent.mkdir(parents=True)
    shutil.copytree(APP, target)
    if kind == "unsigned":
        subprocess.run(["/usr/bin/codesign", "--remove-signature", str(target)], check=True, capture_output=True)
    if kind == "adhoc":
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(target)], check=True, capture_output=True)


def run_doctor(tmp_path, setup):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    if setup["settings"] is not None:
        (home / ".claude" / "settings.json").write_text(json.dumps(setup["settings"]), encoding="utf-8")
    make_app(home, setup["app"])
    tools = tmp_path / "bin"
    tools.mkdir()
    if setup["claude"]:
        fake = tools / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
    for version_dir, version in setup["desktop"].items():
        program = desktop_claude_path(home, version_dir)
        program.parent.mkdir(parents=True)
        answer = f"printf '%s\\n' '{version}'; exit 0" if version else "exit 1"
        program.write_text(DESKTOP_FAKE.format(answer=answer), encoding="utf-8")
        program.chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "LC_CTYPE": "C.UTF-8",
           "FAKE_CLAUDE_VERSION": setup["version"], "FAKE_CLAUDE_PLUGINS": json.dumps(setup["plugins"])}
    fake_bridge = FakeBridge(setup["status"]) if setup["status"] is not None else None
    socket_path = fake_bridge.path if fake_bridge else os.path.join(tempfile.gettempdir(), "fm-doctor-none.sock")
    try:
        done = subprocess.run([str(BRIDGE), "doctor", "--socket", socket_path], env=env, capture_output=True,
                              text=True, timeout=60)
    finally:
        asked = fake_bridge.asked if fake_bridge else []
        if fake_bridge:
            fake_bridge.close()
    lines = done.stdout.splitlines()
    parsed = [LINE.match(line) for line in lines]
    return {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "asked": asked,
            "marks": {m.group(2): m.group(1) for m in parsed if m},
            "details": {m.group(2): m.group(3) for m in parsed if m},
            "order": [m.group(2) for m in parsed if m], "unparsed": [l for l, m in zip(lines, parsed) if not m]}


def changed(**parts):
    setup = dict(GOOD)
    status = parts.pop("status_fields", None)
    if status is not None:
        setup["status"] = {k: v for k, v in (GOOD_STATUS | status).items() if v is not KeyError}
    setup.update(parts)
    return setup


SITUATIONS = {
    "all-good": (GOOD, {}, None),
    "bridge-down": (changed(status=None), {"桥接程序": "不好"} | UNKNOWN_CARD, "open -g"),
    "bluetooth-denied": (changed(status_fields={"authorization": "denied"}), {"蓝牙": "不好"}, "隐私与安全性"),
    "bluetooth-unanswered": (changed(status_fields={"authorization": "notDetermined"}), {"蓝牙": "不好"}, "允许"),
    "bluetooth-off": (changed(status_fields={"bluetooth": "poweredOff", "card": "off", "card_version": None}),
                      {"蓝牙": "不好", "卡片": "不知道", "固件版本": "不知道"}, "打开"),
    "app-missing": (changed(app="missing"), {"签名": "不好"}, "install.sh"),
    "app-unsigned": (changed(app="unsigned"), {"签名": "不好"}, "install.sh"),
    "app-adhoc": (changed(app="adhoc"), {"签名": "注意"}, "Apple Development"),
    "card-away": (changed(status_fields={"card": "scanning", "card_version": None}),
                  {"卡片": "不好", "固件版本": "不知道"}, "README"),
    "firmware-behind": (changed(status_fields={"card": "mismatch", "card_version": 1}),
                        {"卡片": "不好", "固件版本": "不好"}, "固件"),
    "bridge-behind": (changed(status_fields={"card": "mismatch", "card_version": 3}),
                      {"卡片": "不好", "固件版本": "不好"}, "install.sh"),
    "firmware-without-version": (changed(status_fields={"card": "mismatch", "card_version": None}),
                                 {"卡片": "不好", "固件版本": "不好"}, "固件"),
    "resident-without-version": (changed(status_fields={"card_version": KeyError, "bridge_version": KeyError}),
                                 {"固件版本": "不好"}, "install.sh"),
    "pairing-failed": (changed(status_fields={"card": "pairing_failed", "card_version": None}),
                       {"卡片": "不好", "固件版本": "不知道"}, "点“连接”"),
    "plugin-missing": (changed(plugins=[{"id": "other@elsewhere", "enabled": True}]), {"插件": "不好"}, "install.sh"),
    "plugin-disabled": (changed(plugins=[{"id": "funmedal@funmedal", "enabled": False}]), {"插件": "不好"},
                        "claude plugin enable funmedal@funmedal"),
    "hooks-disabled": (changed(settings={"disableAllHooks": True}), {"hooks 开关": "不好"}, "disableAllHooks"),
    "hooks-no-settings": (changed(settings=None), {}, None),
    "claude-old": (changed(version="2.1.291 (Claude Code)"), {TERMINAL_LINE: "不好"}, "claude update"),
    "claude-missing": (changed(claude=False), {"插件": "不知道", TERMINAL_LINE: "不好", DESKTOP_LINE: "不好"},
                       "Code 页开一个会话"),
}


@pytest.mark.parametrize("case, way", [
    ("[OB-11] doctor reports every check as good when everything is in place", "all-good"),
    ("[OB-11] doctor says the bridge is not running and cannot judge what only the bridge knows", "bridge-down"),
    ("[OB-11] doctor reports bluetooth access refused", "bluetooth-denied"),
    ("[OB-11] doctor reports bluetooth access still waiting for an answer", "bluetooth-unanswered"),
    ("[OB-11] doctor reports bluetooth switched off", "bluetooth-off"),
    ("[OB-11] doctor reports the installed bridge missing", "app-missing"),
    ("[OB-11] doctor reports the installed bridge without a signature", "app-unsigned"),
    ("[OB-11] doctor reports the card not connected", "card-away"),
    ("[OB-11] doctor reports firmware older than the bridge", "firmware-behind"),
    ("[OB-11] doctor reports a bridge older than the firmware", "bridge-behind"),
    ("[OB-11] doctor reports firmware from before the version exchange", "firmware-without-version"),
    ("[OB-11] doctor reports a running bridge from before the version exchange", "resident-without-version"),
    ("[OB-11] doctor reports the plugin not installed", "plugin-missing"),
    ("[OB-11] doctor reports the plugin installed but turned off", "plugin-disabled"),
    ("[OB-11] doctor reports all hooks turned off in the user settings", "hooks-disabled"),
    ("[OB-11] doctor treats missing user settings as hooks left on", "hooks-no-settings"),
    ("[OB-11] doctor reports a Claude Code older than 2.1.292", "claude-old"),
    ("[OB-11] doctor reports no claude command", "claude-missing"),
])
def test_doctor(case, way, run_dir, tmp_path):
    check_situation(way, run_dir, tmp_path)


@pytest.mark.parametrize("case, way", [
    ("[OB-14] doctor reports a bridge that gave up after pairing failed three times and says how to pair again",
     "pairing-failed"),
])
def test_doctor_pairing(case, way, run_dir, tmp_path):
    check_situation(way, run_dir, tmp_path)


@pytest.mark.parametrize("case, way", [
    ("[OB-10] doctor reports a bridge signed with a certificate as good", "all-good"),
    ("[OB-10] doctor notes an ad-hoc signed bridge and says how to sign it with an Apple Development certificate",
     "app-adhoc"),
])
def test_doctor_signing(case, way, run_dir, tmp_path):
    check_situation(way, run_dir, tmp_path)


def check_situation(way, run_dir, tmp_path):
    require_bridge_app()
    setup, bad, fix = SITUATIONS[way]
    result = run_doctor(tmp_path, setup)
    write_summary(run_dir, result)
    expected = GOOD_MARKS | bad
    assert result["order"] == CHECKS and result["unparsed"] == [], result
    assert result["marks"] == expected, result
    assert result["exit"] == (1 if "不好" in expected.values() else 0), result
    assert all("改法：" in result["details"][name] for name, mark in expected.items() if mark == "不好"), result
    if fix:
        assert any(fix in result["details"][name] for name in bad), result
    if setup["status"] is not None:
        assert result["asked"] == [{"op": "status"}], result


NEW_DESKTOP = {"2.1.299": "2.1.299 (Claude Code)"}
DESKTOP_SITUATIONS = {
    "desktop-only": (changed(claude=False, desktop={"2.1.280": "2.1.280 (Claude Code)"} | NEW_DESKTOP), {},
                     {TERMINAL_LINE: "没找到", DESKTOP_LINE: "2.1.299", "插件": "装着，也开着"}),
    "desktop-newest-broken": (changed(claude=False, desktop=NEW_DESKTOP | {"2.1.305": None}), {},
                              {DESKTOP_LINE: "2.1.299"}),
    "both-new": (changed(version="2.1.302 (Claude Code)", desktop=NEW_DESKTOP), {},
                 {TERMINAL_LINE: "2.1.302", DESKTOP_LINE: "2.1.299"}),
    "terminal-old": (changed(version="2.1.291 (Claude Code)", desktop=NEW_DESKTOP), {TERMINAL_LINE: "注意"},
                     {TERMINAL_LINE: "claude update", DESKTOP_LINE: "2.1.299"}),
    "desktop-old": (changed(version="2.1.302 (Claude Code)", desktop={"2.1.290": "2.1.290 (Claude Code)"}),
                    {DESKTOP_LINE: "注意"}, {DESKTOP_LINE: "Check for Updates"}),
    "desktop-old-only": (changed(claude=False, desktop={"2.1.290": "2.1.290 (Claude Code)"}), {DESKTOP_LINE: "不好"},
                         {DESKTOP_LINE: "Check for Updates", TERMINAL_LINE: "没找到"}),
    "terminal-unreadable": (changed(version="error: broken install", desktop=NEW_DESKTOP), {TERMINAL_LINE: "不知道"},
                            {TERMINAL_LINE: "版本号"}),
    "nothing": (changed(claude=False), {"插件": "不知道", TERMINAL_LINE: "不好", DESKTOP_LINE: "不好"},
                {DESKTOP_LINE: "Code 页开一个会话"}),
    "desktop-plugin-disabled": (changed(claude=False, desktop=NEW_DESKTOP,
                                        plugins=[{"id": "funmedal@funmedal", "enabled": False}]),
                                {"插件": "不好"}, {"插件": "Manage plugins"}),
}


@pytest.mark.parametrize("case, way", [
    ("[OB-17] doctor with only the Claude desktop app reads its newest bundled Claude Code, ignores older folders, "
     "checks the plugin through it and does not ask for a terminal claude", "desktop-only"),
    ("[OB-17] doctor skips a bundled Claude Code that cannot report its version", "desktop-newest-broken"),
    ("[OB-17] doctor reports the terminal and desktop app versions on separate lines", "both-new"),
    ("[OB-17] doctor notes an old terminal claude without failing when the desktop app is new enough",
     "terminal-old"),
    ("[OB-17] doctor notes an old desktop app without failing when the terminal claude is new enough", "desktop-old"),
    ("[OB-17] doctor fails when the only Claude Code is an old desktop app and says how to update it",
     "desktop-old-only"),
    ("[OB-17] doctor cannot judge a terminal claude that gives no version", "terminal-unreadable"),
    ("[OB-17] doctor with neither a terminal claude nor the desktop app's Claude Code says to open a Code session",
     "nothing"),
    ("[OB-17] doctor tells a desktop app user where to turn the plugin back on", "desktop-plugin-disabled"),
])
def test_doctor_desktop(case, way, run_dir, tmp_path):
    require_bridge_app()
    setup, changed_marks, words = DESKTOP_SITUATIONS[way]
    result = run_doctor(tmp_path, setup)
    write_summary(run_dir, result)
    expected = GOOD_MARKS | changed_marks
    assert result["order"] == CHECKS and result["unparsed"] == [], result
    assert result["marks"] == expected, result
    assert result["exit"] == (1 if "不好" in expected.values() else 0), result
    assert all("改法：" in result["details"][name] for name, mark in expected.items() if mark in ("不好", "注意")), result
    assert all(word in result["details"][name] for name, word in words.items()), result
