"""Extractor tests: both vendor formats, quirks, and edge cases."""
import json

import pytest

from handover.extractors.claude import ClaudeExtractor
from handover.extractors.codex import CodexExtractor
from .fixtures import (claude_session, codex_session_new_style,
                       codex_session_old_style, codex_subagent_session,
                       supersede_session)


def write(tmp_path, name, content, raw_bytes=None):
    p = tmp_path / name
    if raw_bytes is not None:
        p.write_bytes(raw_bytes)
    else:
        p.write_text(content, encoding="utf-8")
    return str(p)


# ---------- Claude ----------

def test_claude_goal_and_decision(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    assert t.session_id == "sess-1"
    assert t.source == "claude"
    assert t.cwd == "/tmp/proj"
    assert t.turns[0].role == "user"
    assert "refund endpoint" in t.turns[0].text


def test_claude_merges_split_assistant_records(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    asst = [x for x in t.turns if x.role == "assistant"]
    # The two records sharing message.id msg_1 must merge into ONE turn,
    # carrying both the text and the tool_use.
    merged = [x for x in asst if "refund endpoint now" in x.text]
    assert len(merged) == 1
    assert any(tc.name == "Bash" for tc in merged[0].tool_calls)


def test_claude_tool_result_failure_attached(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    fails = [tc for x in t.turns for tc in x.tool_calls if not tc.ok]
    assert len(fails) == 1
    assert fails[0].name == "Bash"
    assert "2 failed" in fails[0].output


def test_claude_system_wrapper_filtered_and_interrupt_flagged(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    texts = [x.text for x in t.turns if x.role == "user"]
    assert not any("system-reminder" in x for x in texts)
    assert any(x.interrupted for x in t.turns if x.role == "user")


def test_claude_malformed_line_skipped(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    assert t.raw_lines == 9  # 8 records + 1 malformed
    # goal, merged assistant, decision, interrupt
    # (the tool_result-only user record carries no text -> no turn)
    assert len(t.turns) == 4


def test_claude_empty_file(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "e.jsonl", ""))
    assert t.turns == [] and t.raw_lines == 0


def test_claude_non_utf8_bytes(tmp_path):
    raw = claude_session().encode("utf-8") + b"\xff\xfe bad bytes line\n"
    t = ClaudeExtractor().extract(write(tmp_path, "b.jsonl", "", raw_bytes=raw))
    assert len(t.turns) == 4  # no crash, bad line skipped


def test_claude_huge_file_streams(tmp_path):
    line = json.dumps({"type": "assistant", "uuid": "x", "sessionId": "s",
                       "message": {"id": "m", "role": "assistant",
                                   "content": [{"type": "text", "text": "filler"}]}})
    p = tmp_path / "big.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for _ in range(20000):
            fh.write(line + "\n")
    t = ClaudeExtractor().extract(str(p))
    assert t.raw_lines == 20000
    # all share message.id "m" -> merged into one turn
    assert len(t.turns) == 1


# ---------- Codex ----------

def test_codex_new_style(tmp_path):
    t = CodexExtractor().extract(write(tmp_path, "r.jsonl", codex_session_new_style()))
    assert t.session_id == "01abc"
    assert t.source == "codex"
    assert not t.is_subagent
    roles = [x.role for x in t.turns]
    assert "user" in roles and "assistant" in roles
    user = next(x for x in t.turns if x.role == "user")
    assert "Refactor the auth module" in user.text


def test_codex_exec_failure_captured(tmp_path):
    t = CodexExtractor().extract(write(tmp_path, "r.jsonl", codex_session_new_style()))
    fails = [tc for x in t.turns for tc in x.tool_calls if not tc.ok]
    assert len(fails) == 1
    assert "token expiry" in fails[0].output


def test_codex_unknown_types_ignored(tmp_path):
    t = CodexExtractor().extract(write(tmp_path, "r.jsonl", codex_session_new_style()))
    assert all("quantum" not in x.text for x in t.turns)


def test_codex_old_payload_envelope(tmp_path):
    t = CodexExtractor().extract(write(tmp_path, "r.jsonl", codex_session_old_style()))
    assert t.session_id == "02def"
    ok_calls = [tc for x in t.turns for tc in x.tool_calls]
    assert any(tc.ok for tc in ok_calls)  # successful exec end
    texts = " ".join(x.text for x in t.turns)
    assert "sqlite store" in texts


def test_codex_subagent_excluded_by_default(tmp_path):
    t = CodexExtractor().extract(
        write(tmp_path, "r.jsonl", codex_subagent_session()))
    assert t.is_subagent
    assert t.turns == []


def test_codex_subagent_included_on_request(tmp_path):
    t = CodexExtractor().extract(
        write(tmp_path, "r.jsonl", codex_subagent_session()),
        include_subagents=True)
    assert t.is_subagent
    assert any("review this command" in x.text for x in t.turns)


def test_codex_no_zst_needed(tmp_path):
    # Regression guard for the debunked claim: rollout files are plain jsonl.
    import glob
    assert not glob.glob(str(tmp_path / "*.zst"))
    t = CodexExtractor().extract(write(tmp_path, "r.jsonl", codex_session_new_style()))
    assert t.turns  # parses without any decompression dependency
