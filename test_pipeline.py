"""Pipeline tests: heuristics, conflicts, verifier, render."""
from handover.conflicts import apply_conflict_awareness
from handover.extractors.claude import ClaudeExtractor
from handover.extractors.codex import CodexExtractor
from handover.heuristics import classify, extract_handoff
from handover.render import render_markdown
from handover.verifier import audit, close
from .fixtures import claude_session, codex_session_new_style, supersede_session


def write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------- heuristics ----------

def test_classify_sections():
    assert classify("I decided to go with pytest markers for the tests.")[0] == "DECIDED"
    assert classify("The nose plugin approach was rejected after review.")[0] == "REJECTED"
    assert classify("Next step: write the refund route handler.")[0] == "NEXT"
    assert classify("Don't use the old token helper anywhere.")[0] == "DON'T"
    assert classify("All tests are passing now on main.")[0] == "DONE"
    assert classify("ok") is None          # too short
    assert classify("The weather is nice today outside.") is None  # no marker


def test_extract_handoff_sections(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    by = {}
    for i in h.items:
        by.setdefault(i.section, []).append(i)
    assert "pytest markers" in by["DECIDED"][0].text
    assert "refund route" in by["NEXT"][0].text
    assert any(i.confidence >= 0.9 for i in by["FAILED"])  # tool failure = strong
    assert h.goal.startswith("Add a refund endpoint")


def test_tool_failure_becomes_failed_item(tmp_path):
    t = CodexExtractor().extract(write(tmp_path, "r.jsonl", codex_session_new_style()))
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    failed = [i for i in h.items if i.section == "FAILED"]
    assert failed and "token expiry" in failed[0].text


# ---------- conflicts ----------

def test_supersede_detection(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", supersede_session()))
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    report = apply_conflict_awareness(h)
    assert report["superseded"] == 1
    sup = [i for i in h.items if i.status == "superseded"]
    assert sup and "SQLite" in sup[0].text
    assert h.items[sup[0].superseded_by].status == "active"


def test_staleness_flagging(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    from datetime import datetime, timezone
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)  # a month later
    report = apply_conflict_awareness(h, stale_after_days=7, now=now)
    assert report["stale"] >= 1
    assert any(i.status == "stale" for i in h.items if i.section == "NEXT")


def test_dedupe_folds_duplicates(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    import copy
    # simulate an interleaved-session duplicate: same fact, separate object
    h.items.append(copy.copy(h.items[0]))
    report = apply_conflict_awareness(h)
    assert report["duplicates_folded"] == 1
    dupes = [i for i in h.items if i.status == "duplicate"]
    assert len(dupes) == 1 and dupes[0].merged_from


# ---------- verifier ----------

def test_verifier_full_coverage(tmp_path):
    path = write(tmp_path, "s.jsonl", claude_session())
    t = ClaudeExtractor().extract(path)
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    result = audit(h, t)
    assert result.score == 1.0
    assert result.missed == []


def test_verifier_catches_missed_decision(tmp_path):
    path = write(tmp_path, "s.jsonl", claude_session())
    t = ClaudeExtractor().extract(path)
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    # Simulate a hand-written note that dropped the pytest-markers decision.
    h.items = [i for i in h.items if "pytest markers" not in i.text]
    result = audit(h, t)
    assert result.score < 1.0
    assert any("pytest markers" in m.text for m in result.missed)


def test_close_refuses_below_threshold(tmp_path):
    path = write(tmp_path, "s.jsonl", claude_session())
    t = ClaudeExtractor().extract(path)
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    h.items = [i for i in h.items if "pytest markers" not in i.text]
    result = audit(h, t)
    rc = close(h, result, threshold=0.8, force=False)
    assert rc == 2
    assert h.closed is None


def test_close_force_is_logged(tmp_path):
    path = write(tmp_path, "s.jsonl", claude_session())
    t = ClaudeExtractor().extract(path)
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    h.items = [i for i in h.items if "pytest markers" not in i.text]
    result = audit(h, t)
    rc = close(h, result, threshold=0.8, force=True)
    assert rc == 0
    assert h.closed["forced"] is True
    assert h.closed["coverage"] < 0.8


def test_close_clean_pass(tmp_path):
    path = write(tmp_path, "s.jsonl", claude_session())
    t = ClaudeExtractor().extract(path)
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    result = audit(h, t)
    assert close(h, result) == 0
    assert h.closed["forced"] is False


# ---------- render ----------

def test_render_sections_present(tmp_path):
    t = ClaudeExtractor().extract(write(tmp_path, "s.jsonl", claude_session()))
    h = extract_handoff(t, "2026-10-20T11:00:00+00:00")
    md = render_markdown(h)
    for section in ("## NOW", "## NEXT", "## DECIDED", "## FAILED", "## DON'T"):
        assert section in md
    assert "pytest markers" in md
