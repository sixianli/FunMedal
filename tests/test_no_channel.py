import pytest

from bridge_harness import BRIDGE, PLUGIN, ROOT, artifacts, bridge_cli, require_bridge_app, run_dir, write_summary  # noqa: F401

ARTIFACTS_PREFIX = "ob16"
ARTIFACTS_SUBDIR = "no-channel"
USAGE_EXIT = 64
SETUP_DOCS = ["README.md", "notes/voice.md", "notes/bridge.md", "notes/firmware.md"]
CHANNEL_SETUP = ["--dangerously-load-development-channels", "--mcp-config", "mcp.json", '"args": ["channel"]',
                 "funmedal-bridge channel", "`channel` 模式"]


@pytest.mark.parametrize("case", ["[OB-16] the bridge no longer has a channel mode"])
def test_no_channel_mode(case, run_dir):
    require_bridge_app()
    channel, _ = bridge_cli("channel")
    usage, _ = bridge_cli()
    summary = {"channel_exit": channel.returncode, "channel_stderr": channel.stderr.decode(),
               "usage": usage.stderr.decode(), "source_left": (ROOT / "mac/bridge/Sources/Channel.swift").exists()}
    write_summary(run_dir, summary)
    assert summary["channel_exit"] == USAGE_EXIT and "unknown mode channel" in summary["channel_stderr"], summary
    assert "channel" not in summary["usage"] and "serve|hook|status|quit|doctor" in summary["usage"], summary
    assert not summary["source_left"], summary


@pytest.mark.parametrize("case", ["[OB-16] the setup docs no longer tell anyone to start a channel or write mcp.json"])
def test_docs_have_no_channel_setup(case, run_dir):
    found = {doc: [term for term in CHANNEL_SETUP if term in (ROOT / doc).read_text(encoding="utf-8")]
             for doc in SETUP_DOCS}
    mcp_files = sorted(str(p.relative_to(ROOT)) for p in PLUGIN.rglob("*mcp*.json"))
    write_summary(run_dir, {"found": found, "plugin_mcp_files": mcp_files})
    assert all(terms == [] for terms in found.values()), found
    assert mcp_files == [], mcp_files
