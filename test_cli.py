"""CLI end-to-end smoke tests (subprocess, no network)."""
import json
import subprocess
import sys

from .fixtures import claude_session, codex_session_new_style

CLI = [sys.executable, "-m", "handover"]


REPO_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)


def run(tmp_path, *args, cwd=None, env_extra=None):
    import os
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)  # keep ~/.handover out of the real home
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(CLI + list(args), cwd=cwd or str(tmp_path),
                          capture_output=True, text=True, env=env)


def test_extract_claude_end_to_end(tmp_path):
    src = tmp_path / "sess.jsonl"
    src.write_text(claude_session())
    out = tmp_path / "out"
    r = run(tmp_path, "extract", "--path", str(src), "--out", str(out))
    assert r.returncode == 0, r.stderr
    md = out / "handoff.md"
    assert "pytest markers" in md.read_text()
    sidecar = json.loads((out / "handoff.json").read_text())
    assert sidecar["session_id"] == "sess-1"
    assert sidecar["closed"] is None


def test_extract_auto_detects_codex(tmp_path):
    # Filename is NOT rollout-*: source must come from content sniffing.
    src = tmp_path / "weird-name.jsonl"
    src.write_text(codex_session_new_style())
    r = run(tmp_path, "extract", "--path", str(src), "--out", str(tmp_path / "out"))
    assert r.returncode == 0, r.stderr
    assert "(codex)" in r.stdout


def test_verify_and_close_roundtrip(tmp_path):
    src = tmp_path / "sess.jsonl"
    src.write_text(claude_session())
    out = tmp_path / "out"
    assert run(tmp_path, "extract", "--path", str(src), "--out", str(out)).returncode == 0
    r = run(tmp_path, "verify", str(out / "handoff.md"),
            "--transcript", str(src), "--close")
    assert r.returncode == 0, r.stderr + r.stdout
    sidecar = json.loads((out / "handoff.json").read_text())
    assert sidecar["closed"]["closed"] is True
    assert sidecar["closed"]["forced"] is False


def test_verify_sidecar_fallback_no_transcript_arg(tmp_path):
    src = tmp_path / "sess.jsonl"
    src.write_text(claude_session())
    out = tmp_path / "out"
    assert run(tmp_path, "extract", "--path", str(src), "--out", str(out)).returncode == 0
    # No --transcript: the sidecar's recorded transcript_path is used.
    r = run(tmp_path, "verify", str(out / "handoff.md"))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "100%" in r.stdout


def test_extract_empty_session_refuses(tmp_path):
    src = tmp_path / "empty.jsonl"
    src.write_text("")
    r = run(tmp_path, "extract", "--path", str(src), "--out", str(tmp_path / "o"))
    assert r.returncode != 0
    assert "zero turns" in r.stderr


def test_inject_dry_run_prints_config(tmp_path):
    r = run(tmp_path, "inject", "--target", "claude")
    assert r.returncode == 0
    assert "SessionStart" in r.stdout
    r = run(tmp_path, "inject", "--target", "codex")
    assert r.returncode == 0
    assert "codex-handover" in r.stdout
