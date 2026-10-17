import json
import os
import queue
import re
import socket
import subprocess
import tempfile
import threading
import time
import uuid

import pytest

from bridge_harness import (BRIDGE, W, approve, artifacts, behavior, bridge, bridge_cli, card,  # noqa: F401
                            card_lines, event, info_fields, ink, press, require_bridge_app, run_dir, snapshot,
                            start_and_mark, wait_prompt, wait_until, write_summary)

import cardlink  # noqa: E402
import voice_audio  # noqa: E402

ARTIFACTS_PREFIX = "fm15"
ARTIFACTS_SUBDIR = "voice"
MIC_LINE = re.compile(r"MIC (\d+) ([0-9a-f]+)$")
VOICE_START = re.compile(r"VOICE start (\d+)$")
VOICE_END = re.compile(r"VOICE end (\d+) packets=(\d+) dropped=(\d+) reason=(\w+)$")
VOICE_RESULT = re.compile(r"VOICE result (\d+) (\w+)$")
CARD_RESULTS = {"queued", "empty", "no_session", "denied", "offline", "timeout"}
VOICE_BACKGROUND = cardlink.rgb888_to_565_rgb(0x5A189A)
PACKET_S = voice_audio.SAMPLES_PER_PACKET / voice_audio.SAMPLE_RATE
SHORT_S = 0.5
LIMIT_S = 15.0
LIMIT_PACKETS = 750
RESULT_WAIT_S = 15.0
RESULT_SHOW_S = 4.0
MIN_STACK_FREE_BYTES = 1024
KEY_GAP_LIMIT_MS = 30
TITLE_ROWS = (100, 150)
HINT_ROWS = (165, 195)
CHANNEL_PROTOCOL = "2025-06-18"
CHANNEL_RETRY_S = 2.0
CHANNEL_NAME = "funmedal"
METHOD_NOT_FOUND = -32601
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                         "clientInfo": {"name": "claude-code", "version": "2.1.292"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
SPOKEN = "请帮我把所有测试跑一遍，然后告诉我结果"
SPOKEN_KEYWORD = "测试"
SPEAKING_VOICE = "Tingting"
SPOKEN_HOLD_MS = 10000
SPEAKER_WAKE = "[[slnc 300]]"
PERSON_SECONDS = os.environ.get("FM_PERSON_AT_CARD_SECONDS")
PERSON_TRIES = 3
IMAGE_ROWS = (60, 160)
BUSY_TOOL = {"tool_name": "Bash", "tool_input": {"command": "make busy"}}
IMAGE_COLUMNS = (10, 230)
REFUSALS = {"image": ("image", 1), "result": ("voice", 1), "result-twice": ("voice", 2)}
REFUSED_LINES = {"image": "BLE reject image injected", "voice": "BLE reject voice injected"}
HEADER_ROWS = (0, 55)
RETRIED_RESULT_S = 5.0
RETRY_WAIT_S = (1.0, 1.5)
REJECT_REPLIES = {
    "ble reject image": "OK ble reject image 1",
    "ble reject voice 9": "OK ble reject voice 9",
    "ble reject none": "OK ble reject none",
    "ble reject voice 0": "ERR ble usage",
    "ble reject voice 10": "ERR ble usage",
    "ble reject image 2x": "ERR ble usage",
    "ble reject none 1": "ERR ble usage",
    "ble reject sound": "ERR ble usage",
    "ble reject": "ERR ble usage",
    "ble reject voice 1 2": "ERR ble usage",
}


class FakeBridgeSocket:
    def __init__(self):
        self.path = os.path.join(tempfile.gettempdir(), f"fm-chan-{uuid.uuid4().hex[:8]}.sock")
        self.listener = None

    def listen(self):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(4)

    def accept(self, timeout):
        self.listener.settimeout(timeout)
        client, _ = self.listener.accept()
        client.settimeout(5.0)
        return client, client.makefile("rb")

    def arrives(self, timeout):
        try:
            client, lines = self.accept(timeout)
        except socket.timeout:
            return False
        lines.close()
        client.close()
        return True

    def close(self):
        if self.listener is not None:
            self.listener.close()
            os.unlink(self.path)


class ChannelRun:
    def __init__(self, socket_path):
        self.proc = subprocess.Popen([str(BRIDGE), "channel", "--socket", socket_path], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.out = queue.Queue()
        self.transcript = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                message = {"unparsed": line.decode(errors="replace")}
            self.transcript.append({"dir": "out", "msg": message})
            self.out.put(message)

    def send(self, message):
        self.transcript.append({"dir": "in", "msg": message})
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def next(self, timeout=5.0):
        return self.out.get(timeout=timeout)

    def finish(self, timeout=5.0):
        started = time.monotonic()
        self.proc.stdin.close()
        code = self.proc.wait(timeout)
        return code, time.monotonic() - started

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


class FakeChannel:
    def __init__(self, bridge_socket, claude_pid):
        self.pid = claude_pid
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(bridge_socket)
        self.sock.settimeout(1.0)
        self.lines = self.sock.makefile("rb")
        self.sock.sendall((json.dumps({"op": "listen", "claude_pid": claude_pid}) + "\n").encode())
        self.ack = json.loads(self.lines.readline())
        self.said = []

    def collect(self):
        try:
            while line := self.lines.readline():
                self.said.append(json.loads(line))
        except socket.timeout:
            pass
        return self.said

    def close(self):
        self.lines.close()
        self.sock.close()


def hook_as(bridge, claude_pid, payload):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PID"}
    env["CLAUDE_PID"] = str(claude_pid)
    result, _ = bridge_cli("hook", "--socket", bridge.socket, payload=json.dumps(payload).encode(), env=env)
    assert result.returncode == 0, result


def built_in_speaker():
    named = os.environ.get("FM_SAY_DEVICE")
    if named:
        return named
    listing = subprocess.run(["say", "-a", "?"], capture_output=True, text=True, check=True).stdout
    names = [line.split(None, 1)[1].strip() for line in listing.splitlines() if len(line.split(None, 1)) == 2]
    built_in = [name for name in names if name.endswith("Speakers") or "Built-in" in name]
    if not built_in:
        pytest.fail(f"no built-in speaker among {names}; set FM_SAY_DEVICE to the device that reaches the card")
    return built_in[0]


def record_speaker_end(speaker, finished):
    speaker.wait()
    finished.append(time.time())


def wake_speaker(device):
    woken = time.time()
    subprocess.run(["say", "-a", device, SPEAKER_WAKE], check=True, timeout=15.0)
    return round(time.time() - woken, 3)


def talk(card, run_dir, hold_ms, start, speak=None, shot_name="result"):
    device = built_in_speaker() if speak else None
    wake_s = wake_speaker(device) if speak else None
    press(card, "up", f"hold {hold_ms}")
    pressed, pressed_t, _ = card.wait_for(lambda line: line == "KEY press up", 2.0, start)
    started, _, began = wait_voice(card, VOICE_START, 2.0, pressed)
    finished = []
    watcher = None
    speaker = None
    if speak:
        launched = time.time()
        speaker = subprocess.Popen(["say", "-a", device, "-v", SPEAKING_VOICE, speak])
        watcher = threading.Thread(target=record_speaker_end, args=(speaker, finished), daemon=True)
        watcher.start()
    try:
        ended, ended_t, end = wait_voice(card, VOICE_END, hold_ms / 1000 + 3.0, started)
        if watcher:
            watcher.join(10.0)
    finally:
        if speaker is not None and speaker.poll() is None:
            speaker.kill()
    _, result_t, result = wait_voice(card, VOICE_RESULT, RESULT_WAIT_S + 3.0, ended)
    shot = snapshot(card, run_dir, shot_name)
    wait_voice_screen(card, "none", RESULT_SHOW_S + 3.0)
    spoken = {"wake_s": wake_s, "speaker_s": round(finished[0] - launched, 3),
              "speech_done_before_release_s": round(ended_t - finished[0], 3)} if finished else {}
    return {"clip": int(began.group(1)), "packets": int(end.group(2)), "result": result.group(2),
            "result_after_end_s": result_t - ended_t, "speaker": device, "shot": shot, **spoken}


def spoke_before_release(got):
    return got.get("speech_done_before_release_s", -1.0) > 0


def voice_log(bridge, kind):
    return [e for e in bridge.log_entries() if e.get("kind") == kind]


def say_line(text, clip):
    return (json.dumps({"say": text, "clip": clip}, ensure_ascii=False) + "\n").encode()


def pushed(text):
    return {"jsonrpc": "2.0", "method": "notifications/claude/channel",
            "params": {"content": text, "meta": {"chat_id": "card"}}}


def capture(card, packets, gain):
    start = card.mark()
    reply = card.request(f"mic {packets} {gain}", timeout=packets * PACKET_S + 10.0)
    assert reply.startswith(f"OK mic packets={packets} gain={gain} "), reply
    lines = [(t, MIC_LINE.match(line)) for t, line in card.since(start) if line.startswith("MIC ")]
    raw = [bytes.fromhex(found.group(2)) for _, found in lines]
    samples = [s for packet in raw for s in voice_audio.decode(packet)]
    return {"reply": reply, "fields": info_fields(reply), "sequence": [int(found.group(1)) for _, found in lines],
            "headers": [voice_audio.header(packet) for packet in raw], "sizes": sorted({len(p) for p in raw}),
            "span_s": lines[-1][0] - lines[0][0] if lines else 0.0, "samples": samples}


def wait_voice(card, pattern, timeout, start):
    index, t, line = card.wait_for(lambda line: pattern.match(line) is not None, timeout, start)
    return index, t, pattern.match(line)


def voice_lines(card, start):
    return card_lines(card, start, "VOICE ")


def wait_voice_screen(card, name, timeout):
    return wait_until(lambda: info_fields(card.request("info"))["voice"] == name, timeout, f"voice screen {name}")


@pytest.mark.parametrize("case", ["[FM-15] the microphone records room sound at 16000 samples a second"])
def test_mic_records(case, card, run_dir):
    packets = 100
    quiet = capture(card, packets, 0)
    loud = capture(card, packets, 7)
    record = {}
    for name, got in (("gain0", quiet), ("gain7", loud)):
        voice_audio.write_wav(run_dir / f"mic-{name}.wav", got["samples"])
        record[name] = {"reply": got["reply"], "sizes": got["sizes"], "span_s": got["span_s"],
                        "decoded_rms": voice_audio.rms(got["samples"]),
                        "decoded_ac_rms": voice_audio.rms(got["samples"], remove_mean=True),
                        "types": sorted({h["type"] for h in got["headers"]}), "wav": f"mic-{name}.wav"}
    write_summary(run_dir, record)
    expected_span = (packets - 1) * PACKET_S
    for name, got in (("gain0", quiet), ("gain7", loud)):
        assert got["reply"].startswith(f"OK mic packets={packets} "), got["reply"]
        assert got["sequence"] == list(range(packets)), name
        assert got["sizes"] == [voice_audio.AUDIO_PACKET] and record[name]["types"] == [voice_audio.TYPE_AUDIO]
        assert 0.9 * expected_span <= got["span_s"] <= 1.1 * expected_span, (name, got["span_s"])
        card_rms = int(got["fields"]["rms"])
        assert abs(record[name]["decoded_rms"] - card_rms) <= max(3.0, 0.25 * card_rms), (name, record[name])
    assert int(quiet["fields"]["peak"]) < 32767, quiet["reply"]
    assert record["gain7"]["decoded_ac_rms"] > 2 * record["gain0"]["decoded_ac_rms"], record


@pytest.mark.parametrize("case", ["[FM-15] without a Mac the card says it is not connected"])
def test_push_to_talk_offline(case, card, run_dir):
    wait_until(lambda: info_fields(card.request("info"))["ble"] == "advertising", 10.0, "card advertising")
    start = card.mark()
    card.request("frame 0")
    press(card, "up", "hold 1500")
    pressed, pressed_t, _ = card.wait_for(lambda line: line == "KEY press up", 2.0, start)
    _, result_t, found = wait_voice(card, VOICE_RESULT, 2.0, pressed)
    shot = snapshot(card, run_dir, "offline")
    wait_voice_screen(card, "none", RESULT_SHOW_S + 3.0)
    card.request("frame auto")
    record = {"lines": voice_lines(card, start), "result_after_s": result_t - pressed_t, "png": shot["png"],
              "title_ink": ink(shot["pixels"], TITLE_ROWS, VOICE_BACKGROUND),
              "hint_ink": ink(shot["pixels"], HINT_ROWS, VOICE_BACKGROUND),
              "background": shot["pixels"][5 * W + 5] == VOICE_BACKGROUND}
    write_summary(run_dir, record)
    assert found.group(2) == "offline", record
    assert record["result_after_s"] < 0.5, record
    assert not any(line.startswith(("VOICE start", "VOICE end")) for line in record["lines"]), record
    assert record["background"] and record["title_ink"] > 200 and record["hint_ink"] > 100, record


@pytest.mark.parametrize("case", ["[FM-15] a press shorter than half a second sends nothing"])
def test_short_press(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    press(card, "up", "hold 300")
    card.wait_for(lambda line: line == "VOICE short", 3.0, start)
    time.sleep(0.2)
    lines = voice_lines(card, start)
    info = info_fields(card.request("info"))
    write_summary(run_dir, {"lines": lines, "info": info})
    assert lines == ["VOICE short"], lines
    assert info["voice"] == "none", info


@pytest.mark.parametrize("case", ["[FM-15] holding past 15 seconds stops at the limit without starving the keys"])
def test_limit(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    card.request("info")
    press(card, "up", f"hold {int((LIMIT_S + 2) * 1000)}")
    pressed, pressed_t, _ = card.wait_for(lambda line: line == "KEY press up", 2.0, start)
    started, started_t, began = wait_voice(card, VOICE_START, 2.0, pressed)
    listening = snapshot(card, run_dir, "listening")
    ended, ended_t, end = wait_voice(card, VOICE_END, LIMIT_S + 3.0, started)
    during = info_fields(card.request("info"))
    _, result_t, result = wait_voice(card, VOICE_RESULT, RESULT_WAIT_S + 3.0, ended)
    wait_voice_screen(card, "none", RESULT_SHOW_S + 3.0)
    stacks = info_fields(card.request("stacks"))
    record = {"lines": voice_lines(card, start), "start_after_s": started_t - pressed_t,
              "end_after_s": ended_t - pressed_t, "result_after_end_s": result_t - ended_t, "during": during,
              "stacks": stacks, "png": listening["png"],
              "listening_background": listening["pixels"][5 * W + 5] == VOICE_BACKGROUND,
              "listening_ink": ink(listening["pixels"], TITLE_ROWS, VOICE_BACKGROUND)}
    write_summary(run_dir, record)
    assert SHORT_S - 0.1 <= record["start_after_s"] <= SHORT_S + 0.3, record
    assert end.group(1) == began.group(1) and end.group(4) == "limit", record
    assert int(end.group(2)) == LIMIT_PACKETS, record
    assert LIMIT_S - 0.3 <= record["end_after_s"] <= LIMIT_S + 0.5, record
    assert result.group(2) in CARD_RESULTS, record
    assert int(during["key_gap_ms"]) <= KEY_GAP_LIMIT_MS, record
    assert all(int(v) >= MIN_STACK_FREE_BYTES for v in stacks.values()), record
    assert record["listening_background"] and record["listening_ink"] > 200, record


@pytest.mark.parametrize("case", ["[FM-15] talking on the approval screen comes back to it and approval still works"])
def test_talk_over_approval(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    run = bridge.hook_async(approve("t", "Bash", {"command": "make talk"}))
    shown = wait_prompt(card, start)
    press(card, "up", "hold 1500")
    wait_voice(card, VOICE_START, 3.0, shown["index"])
    ended, _, _ = wait_voice(card, VOICE_END, 3.0, shown["index"])
    covered = card.mark()
    press(card, "ok", "long")
    _, _, result = wait_voice(card, VOICE_RESULT, RESULT_WAIT_S + 3.0, ended)
    covered_decisions = card_lines(card, covered, "DECISION")
    wait_voice_screen(card, "none", RESULT_SHOW_S + 3.0)
    back = info_fields(card.request("info"))
    press(card, "ok", "long")
    card.wait_for(lambda line: line == f"DECISION {shown['id']} allow", 5.0, covered)
    answered = run.result()
    write_summary(run_dir, {"lines": voice_lines(card, start), "covered_decisions": covered_decisions, "back": back,
                            "answered": answered, "result": result.group(2)})
    assert covered_decisions == [], covered_decisions
    assert back["prompt"] == str(shown["id"]), back
    assert behavior(answered["stdout"]) == {"behavior": "allow"}, answered


@pytest.mark.parametrize("case", ["[FM-15] the channel server registers as a Claude Code channel and pushes text in"])
def test_channel_server(case, run_dir):
    require_bridge_app()
    fake = FakeBridgeSocket()
    fake.listen()
    channel = ChannelRun(fake.path)
    record = {}
    try:
        channel.send(INITIALIZE)
        record["init"] = channel.next()
        record["early_connect"] = fake.arrives(1.0)
        channel.send(INITIALIZED)
        client, lines = fake.accept(5.0)
        record["listen"] = json.loads(lines.readline())
        client.sendall(b'{"ok":true}\n')
        client.sendall(say_line(SPOKEN, 5))
        record["pushed"] = channel.next()
        for request_id, method in ((2, "ping"), (3, "server/discover"), (4, "tools/list")):
            channel.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}})
        channel.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}})
        channel.send({"jsonrpc": "2.0", "id": 5, "method": "ping"})
        record["replies"] = [channel.next() for _ in range(4)]
        record["exit"], record["exit_s"] = channel.finish()
        record["bridge_saw_close"] = lines.read() == b""
        lines.close()
        client.close()
    finally:
        channel.stop()
        fake.close()
        record["transcript"] = channel.transcript
        record["stderr"] = channel.proc.stderr.read().decode()
        write_summary(run_dir, record)
    result = record["init"]["result"]
    assert record["init"]["id"] == 1 and result["protocolVersion"] == CHANNEL_PROTOCOL, record["init"]
    assert result["capabilities"] == {"experimental": {"claude/channel": {}}}, result
    assert result["serverInfo"]["name"] == CHANNEL_NAME and result["instructions"], result
    assert not record["early_connect"], record
    assert record["listen"] == {"op": "listen", "claude_pid": os.getpid()}, record["listen"]
    assert record["pushed"] == pushed(SPOKEN), record["pushed"]
    replies = record["replies"]
    assert replies[0] == {"jsonrpc": "2.0", "id": 2, "result": {}}, replies
    assert [(r["id"], r["error"]["code"]) for r in replies[1:3]] == [(3, METHOD_NOT_FOUND), (4, METHOD_NOT_FOUND)]
    assert replies[3] == {"jsonrpc": "2.0", "id": 5, "result": {}}, replies
    assert record["exit"] == 0 and record["exit_s"] < 2.0 and record["bridge_saw_close"], record


@pytest.mark.parametrize("case", ["[FM-15] the channel server reaches a bridge that starts later or restarts"])
def test_channel_reconnects(case, run_dir):
    require_bridge_app()
    fake = FakeBridgeSocket()
    channel = ChannelRun(fake.path)
    record = {}
    try:
        channel.send(INITIALIZE)
        channel.next()
        channel.send(INITIALIZED)
        time.sleep(1.0)
        channel.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        record["ping_without_bridge"] = channel.next()
        fake.listen()
        began = time.monotonic()
        client, lines = fake.accept(CHANNEL_RETRY_S + 3.0)
        record["first_after_s"] = time.monotonic() - began
        record["first"] = json.loads(lines.readline())
        client.sendall(b'{"ok":true}\n')
        lines.close()
        client.close()
        began = time.monotonic()
        client, lines = fake.accept(CHANNEL_RETRY_S + 3.0)
        record["again_after_s"] = time.monotonic() - began
        record["again"] = json.loads(lines.readline())
        client.sendall(b'{"ok":true}\n')
        client.sendall(say_line(SPOKEN, 6))
        record["pushed"] = channel.next()
        record["exit"], _ = channel.finish()
        lines.close()
        client.close()
    finally:
        channel.stop()
        fake.close()
        record["transcript"] = channel.transcript
        write_summary(run_dir, record)
    listen = {"op": "listen", "claude_pid": os.getpid()}
    assert record["ping_without_bridge"] == {"jsonrpc": "2.0", "id": 2, "result": {}}, record
    assert record["first"] == listen and record["again"] == listen, record
    assert record["first_after_s"] <= CHANNEL_RETRY_S + 1.0, record
    assert record["again_after_s"] <= CHANNEL_RETRY_S + 1.0, record
    assert record["pushed"] == pushed(SPOKEN) and record["exit"] == 0, record


@pytest.mark.parametrize("case,way,claude_pid", [
    ("[FM-15] hook reports the Claude Code process named in CLAUDE_PID", "named", "4242"),
    ("[FM-15] hook reports its parent process when CLAUDE_PID is missing", "missing", None),
    ("[FM-15] hook reports its parent process when CLAUDE_PID is not a number", "garbled", "4242x"),
])
def test_hook_reports_claude_process(case, way, claude_pid, run_dir):
    require_bridge_app()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PID"}
    if claude_pid is not None:
        env["CLAUDE_PID"] = claude_pid
    expected = int(claude_pid) if way == "named" else os.getpid()
    fake = FakeBridgeSocket()
    fake.listen()
    sent = []

    def serve(replies):
        client, lines = fake.accept(5.0)
        sent.append(json.loads(lines.readline()))
        for reply in replies:
            client.sendall(reply)
        client.shutdown(socket.SHUT_WR)
        lines.read()
        lines.close()
        client.close()

    try:
        for payload, replies in ((event("SessionStart", "p", source="startup"), [b'{"ok":true}\n']),
                                 (approve("p", "Bash", {"command": "ls"}),
                                  [b'{"ok":true,"id":1}\n', b'{"decision":null}\n'])):
            server = threading.Thread(target=serve, args=(replies,))
            server.start()
            result, _ = bridge_cli("hook", "--socket", fake.path, payload=json.dumps(payload).encode(), env=env)
            server.join(5.0)
            assert result.returncode == 0 and result.stdout == b"", result
    finally:
        fake.close()
        write_summary(run_dir, {"way": way, "claude_pid_env": claude_pid, "sent": sent})
    assert [m["op"] for m in sent] == ["event", "approve"], sent
    assert all(m["claude_pid"] == expected for m in sent), (expected, sent)


@pytest.mark.parametrize("case", ["[FM-15] with no channel connected the card says no session can take the words"])
def test_voice_no_session(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    before = bridge.status()
    got = talk(card, run_dir, 1500, start)
    results = voice_log(bridge, "voice_result")
    write_summary(run_dir, {"before": before, "talk": {k: v for k, v in got.items() if k != "shot"},
                            "png": got["shot"]["png"], "lines": voice_lines(card, start), "results": results,
                            "hint_ink": ink(got["shot"]["pixels"], HINT_ROWS, VOICE_BACKGROUND)})
    assert before["channels"] == [] and before["speech"] == "authorized", before
    assert got["result"] == "no_session" and got["result_after_end_s"] < 1.0, got
    assert [(r["clip"], r["result"]) for r in results] == [(got["clip"], "no_session")], results
    assert ink(got["shot"]["pixels"], HINT_ROWS, VOICE_BACKGROUND) > 100


@pytest.mark.parametrize("case", ["[FM-15] saying nothing comes back as not heard"])
def test_voice_silence(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    listener = FakeChannel(bridge.socket, 1111)
    try:
        got = talk(card, run_dir, 2000, start)
        said = listener.collect()
    finally:
        listener.close()
    results = voice_log(bridge, "voice_result")
    write_summary(run_dir, {"talk": {k: v for k, v in got.items() if k != "shot"}, "png": got["shot"]["png"],
                            "said": said, "results": results, "lines": voice_lines(card, start)})
    assert listener.ack == {"ok": True}, listener.ack
    assert got["result"] == "empty" and said == [], (got, said)
    assert [(r["clip"], r["result"]) for r in results] == [(got["clip"], "empty")], results


@pytest.mark.parametrize("case", ["[FM-15] spoken words reach the session that last needed the user and show on the card"])
def test_voice_spoken(case, card, bridge, run_dir):
    start = start_and_mark(bridge, card)
    newer, older = FakeChannel(bridge.socket, 1111), FakeChannel(bridge.socket, 2222)
    try:
        hook_as(bridge, 2222, event("SessionStart", "a", source="startup"))
        hook_as(bridge, 1111, event("UserPromptSubmit", "b", prompt="x"))
        hook_as(bridge, 1111, event("Stop", "b", stop_hook_active=False))
        hook_as(bridge, 2222, event("PreToolUse", "a", tool_use_id="t1", **BUSY_TOOL))
        hook_as(bridge, 2222, event("PostToolUse", "a", tool_use_id="t1", tool_response={}, **BUSY_TOOL))
        status = bridge.status()
        got = talk(card, run_dir, SPOKEN_HOLD_MS, start, speak=SPOKEN)
        said = {"newer": newer.collect(), "older": older.collect()}
    finally:
        older.close()
        newer.close()
    results = voice_log(bridge, "voice_result")
    log_text = bridge.log.read_text(encoding="utf-8")
    heard = said["newer"][0]["say"] if said["newer"] else ""
    image_ink = ink(got["shot"]["pixels"], IMAGE_ROWS, VOICE_BACKGROUND, IMAGE_COLUMNS)
    write_summary(run_dir, {"status": status, "talk": {k: v for k, v in got.items() if k != "shot"},
                            "png": got["shot"]["png"], "said": said, "results": results, "image_ink": image_ink,
                            "lines": voice_lines(card, start), "writes": voice_log(bridge, "write")})
    assert sorted(status["channels"]) == [1111, 2222], status
    assert spoke_before_release(got), got
    assert got["result"] == "queued", got
    assert [m["clip"] for m in said["newer"]] == [got["clip"]] and said["older"] == [], said
    assert SPOKEN_KEYWORD in heard, heard
    assert [(r["clip"], r["result"], r["pid"]) for r in results] == [(got["clip"], "queued", 1111)], results
    assert results[0]["chars"] == len(heard) and image_ink == results[0]["ink"] > 0, (results, image_ink)
    assert heard not in log_text and SPOKEN not in log_text


@pytest.mark.parametrize("case", ["[FM-15] after the bridge restarts, words still go to the session that last needed the user"])
def test_voice_after_restart(case, card, bridge, run_dir):
    start_and_mark(bridge, card)
    before = [FakeChannel(bridge.socket, 2222), FakeChannel(bridge.socket, 1111)]
    try:
        hook_as(bridge, 2222, event("SessionStart", "a", source="startup"))
        hook_as(bridge, 1111, event("UserPromptSubmit", "b", prompt="x"))
        hook_as(bridge, 1111, event("Stop", "b", stop_hook_active=False))
    finally:
        for channel in before:
            channel.close()
    bridge.quit()
    start = start_and_mark(bridge, card)
    after = {"older_first": FakeChannel(bridge.socket, 2222), "needed": FakeChannel(bridge.socket, 1111),
             "never_needed_last": FakeChannel(bridge.socket, 3333)}
    try:
        status = bridge.status()
        got = talk(card, run_dir, SPOKEN_HOLD_MS, start, speak=SPOKEN)
        said = {name: channel.collect() for name, channel in after.items()}
    finally:
        for channel in after.values():
            channel.close()
    results = voice_log(bridge, "voice_result")
    write_summary(run_dir, {"status": status, "talk": {k: v for k, v in got.items() if k != "shot"},
                            "said": said, "results": results, "lines": voice_lines(card, start),
                            "starts": voice_log(bridge, "start"), "listens": voice_log(bridge, "listen")})
    assert len(voice_log(bridge, "start")) == 2, results
    assert status["channels"] == [2222, 1111, 3333], status
    assert spoke_before_release(got), got
    assert got["result"] == "queued", got
    assert {name: [m["clip"] for m in messages] for name, messages in said.items()} == \
        {"older_first": [], "needed": [got["clip"]], "never_needed_last": []}, said
    assert SPOKEN_KEYWORD in said["needed"][0]["say"], said
    assert [(r["clip"], r["result"], r["pid"]) for r in results] == [(got["clip"], "queued", 1111)], results


def card_writes(bridge, kind, what, clip):
    return [e for e in voice_log(bridge, kind) if e.get("what") == what and e.get("clip") == clip]


def result_screen(shot):
    pixels = shot["pixels"]
    return {"png": shot["png"], "header_ink": ink(pixels, HEADER_ROWS, VOICE_BACKGROUND),
            "title_ink": ink(pixels, TITLE_ROWS, VOICE_BACKGROUND),
            "hint_ink": ink(pixels, HINT_ROWS, VOICE_BACKGROUND),
            "image_ink": ink(pixels, IMAGE_ROWS, VOICE_BACKGROUND, IMAGE_COLUMNS)}


def retry_gaps(bridge, clip):
    entries = [e for e in bridge.log_entries() if e.get("what") == "voice" and e.get("clip") == clip
               and e.get("kind") in ("write", "write_error")]
    return [round(later["t"] - entry["t"], 3) for entry, later in zip(entries, entries[1:])
            if entry["kind"] == "write_error" and later["kind"] == "write"]


@pytest.mark.parametrize("case", ["[FM-15] the card's test command for refusing voice writes takes a count from 1 to 9 and turns down anything else"])
def test_reject_command_usage(case, card, run_dir):
    replies = {}
    try:
        for command in REJECT_REPLIES:
            replies[command] = card.request(command)
    finally:
        cleared = card.request("ble reject none")
    write_summary(run_dir, {"replies": replies, "cleared": cleared})
    assert {command: reply.split(":")[0] for command, reply in replies.items()} == REJECT_REPLIES, replies
    assert cleared == "OK ble reject none", cleared


@pytest.mark.parametrize("way,case", [
    ("image", "[FM-15] when the card refuses a piece of the text image, the words still arrive and the card shows the result without the image"),
    ("result", "[FM-15] when the card refuses the result once, the bridge writes it again and the card shows the words"),
    ("result-twice", "[FM-15] when the card refuses the result twice, the bridge stops and the card says the Mac did not answer"),
])
def test_card_refuses_voice_write(way, case, card, bridge, run_dir):
    kind, times = REFUSALS[way]
    start = start_and_mark(bridge, card)
    channel = FakeChannel(bridge.socket, 1111)
    try:
        hook_as(bridge, 1111, event("UserPromptSubmit", "b", prompt="x"))
        armed = card.request(f"ble reject {kind} {times}")
        assert armed == f"OK ble reject {kind} {times}", armed
        refused = talk(card, run_dir, SPOKEN_HOLD_MS, start, speak=SPOKEN, shot_name="refused")
        recovered = talk(card, run_dir, SPOKEN_HOLD_MS, card.mark(), speak=SPOKEN, shot_name="recovered")
        said = {message["clip"]: message["say"] for message in channel.collect()}
    finally:
        try:
            channel.close()
        finally:
            cleared = card.request("ble reject none")
    clips = {"refused": refused["clip"], "recovered": recovered["clip"]}
    results = {name: [r["result"] for r in voice_log(bridge, "voice_result") if r["clip"] == clip]
               for name, clip in clips.items()}
    chars = {r["clip"]: r.get("chars") for r in voice_log(bridge, "voice_result")}
    errors = {name: [e["what"] for e in voice_log(bridge, "write_error") if e.get("clip") == clip]
              for name, clip in clips.items()}
    image_writes = {name: [e["offset"] for e in card_writes(bridge, "write", "voice_image", clip)]
                    for name, clip in clips.items()}
    result_writes = {name: [e["result"] for e in card_writes(bridge, "write", "voice", clip)]
                     for name, clip in clips.items()}
    drawn = {r["clip"]: r.get("ink") for r in voice_log(bridge, "voice_result")}
    screens = {"refused": result_screen(refused["shot"]), "recovered": result_screen(recovered["shot"])}
    rejects = card_lines(card, start, "BLE reject")
    retry_waits = retry_gaps(bridge, refused["clip"])
    write_summary(run_dir, {"armed": armed, "cleared": cleared, "retry_waits_s": retry_waits, "clips": clips,
                            "card_results": [refused["result"], recovered["result"]],
                            "bridge_results": results, "write_errors": errors, "image_writes": image_writes,
                            "result_writes": result_writes, "drawn_ink": drawn, "screens": screens,
                            "rejects": rejects, "heard_chars": {c: len(t) for c, t in said.items()},
                            "bridge_chars": chars,
                            "talk": [{k: v for k, v in got.items() if k != "shot"} for got in (refused, recovered)]})
    assert cleared == "OK ble reject none", cleared
    assert rejects == [REFUSED_LINES[kind]] * times, rejects
    assert results == {"refused": ["queued"], "recovered": ["queued"]}, results
    assert sorted(said) == sorted(clips.values()) and all(len(said[c]) == chars[c] > 0 for c in said), (said, chars)
    assert errors == {"refused": ["voice_image" if kind == "image" else "voice"] * times, "recovered": []}, errors
    assert result_writes["recovered"] == ["queued"], result_writes
    refused_screen = screens["refused"]
    if way == "image":
        assert image_writes["refused"] == [0] and result_writes["refused"] == ["queued"], (image_writes, result_writes)
        assert refused["result"] == "queued", refused
        assert refused_screen["header_ink"] == 0 and refused_screen["title_ink"] > 200, screens
    else:
        assert len(image_writes["refused"]) > 1 and image_writes["refused"][0] == 0, image_writes
        assert result_writes["refused"] == ["queued", "queued"], result_writes
        assert len(retry_waits) == 1 and RETRY_WAIT_S[0] <= retry_waits[0] < RETRY_WAIT_S[1], retry_waits
    if way == "result":
        assert refused["result"] == "queued" and refused["result_after_end_s"] < RETRIED_RESULT_S, refused
        assert refused_screen["header_ink"] > 0 and refused_screen["image_ink"] == drawn[refused["clip"]] > 0, (
            screens, drawn)
    if way == "result-twice":
        assert refused["result"] == "timeout", refused
        assert RESULT_WAIT_S - 0.5 <= refused["result_after_end_s"] <= RESULT_WAIT_S + 1.0, refused
        assert refused_screen["header_ink"] == 0 and refused_screen["title_ink"] > 200, screens
        assert refused_screen["hint_ink"] > 100, screens
    assert recovered["result"] == "queued", recovered
    assert screens["recovered"]["header_ink"] > 0, screens
    assert screens["recovered"]["image_ink"] == drawn[recovered["clip"]] > 0, (screens, drawn)


@pytest.mark.skipif(not PERSON_SECONDS, reason="needs a person at the card; set FM_PERSON_AT_CARD_SECONDS")
@pytest.mark.parametrize("case", ["[FM-15] a person holds the middle key, says the test phrase and the words arrive"])
def test_person_speaks(case, card, bridge, run_dir):
    seconds = float(PERSON_SECONDS)
    start = start_and_mark(bridge, card)
    channel = FakeChannel(bridge.socket, 1111)
    tries = []
    try:
        hook_as(bridge, 1111, event("UserPromptSubmit", "b", prompt="x"))
        mark = start
        for attempt in range(1, PERSON_TRIES + 1):
            (run_dir / f"ready-{attempt}.txt").write_text(f"{time.time():.3f}\n", encoding="utf-8")
            started, _, began = wait_voice(card, VOICE_START, seconds, mark)
            ended, _, end = wait_voice(card, VOICE_END, LIMIT_S + 3.0, started)
            mark, _, result = wait_voice(card, VOICE_RESULT, RESULT_WAIT_S + 3.0, ended)
            wait_voice_screen(card, "none", RESULT_SHOW_S + 3.0)
            tries.append({"clip": int(began.group(1)), "packets": int(end.group(2)), "result": result.group(2)})
        said = {message["clip"]: message["say"] for message in channel.collect()}
    finally:
        channel.close()
    for attempt in tries:
        attempt["heard"] = said.get(attempt["clip"], "")
    injected = card_lines(card, start, "OK key")
    write_summary(run_dir, {"note": "only the agreed test phrase is spoken", "tries": tries, "injected": injected,
                            "results": voice_log(bridge, "voice_result")})
    assert injected == [], injected
    assert [attempt["result"] for attempt in tries] == ["queued"] * PERSON_TRIES, tries
    assert all(SPOKEN_KEYWORD in attempt["heard"] for attempt in tries), tries
