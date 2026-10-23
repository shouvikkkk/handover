"""Coverage audit: re-scan the source transcript for decision-like markers
that are NOT reflected in the handoff note, and score it.

This is handover's differentiator. Every other handoff tool writes a note
and trusts it. handover treats the note as a claim and the transcript as
ground truth, then reports the gap.

Fail-closed semantics: `verify --close` refuses to mark a handoff closed
below the coverage threshold unless --force is given, and a forced close is
logged into the JSON sidecar so it can't be mistaken for a clean one.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from .conflicts import content_words
from .extractors.base import BaseExtractor
from .heuristics import (DECIDED, DONE, DONT, FAILED, NEXT, REJECTED,
                         split_sentences)
from .model import Handoff, Transcript

_MARKER_SETS = DECIDED + REJECTED + FAILED + NEXT + DONT + DONE

# Coverage threshold below which --close refuses without --force.
DEFAULT_THRESHOLD = 0.8


@dataclass
class Marker:
    text: str
    turn_seq: int
    kind: str          # 'sentence' | 'tool-failure'


@dataclass
class AuditResult:
    total: int
    captured: int
    missed: List[Marker]
    score: float

    def to_dict(self) -> Dict:
        return {
            "total": self.total,
            "captured": self.captured,
            "missed": [{"text": m.text, "turn_seq": m.turn_seq, "kind": m.kind}
                       for m in self.missed],
            "score": self.score,
        }


def collect_markers(transcript: Transcript) -> List[Marker]:
    """All decision-like markers in the raw transcript, independent of
    whatever the extraction pipeline kept."""
    markers: List[Marker] = []
    for turn in transcript.turns:
        if turn.role in ("user", "assistant") and turn.text.strip():
            for sent in split_sentences(turn.text):
                low = sent.lower()
                if len(sent.split()) >= 4 and any(m in low for m, _ in _MARKER_SETS):
                    markers.append(Marker(text=sent, turn_seq=turn.seq, kind="sentence"))
        for tc in turn.tool_calls:
            if not tc.ok:
                markers.append(Marker(
                    text=f"{tc.name} failed: {(tc.output or '')[:120]}",
                    turn_seq=turn.seq, kind="tool-failure"))
    # Dedup identical markers.
    seen, out = set(), []
    for m in markers:
        key = m.text.lower()
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _covered(marker: Marker, note_texts: List[str]) -> bool:
    """A marker is covered if some note item shares enough of its vocabulary.

    Matching is on content words (stopwords stripped), so paraphrase is
    tolerated but a missing fact is not.
    """
    m_words = set(content_words(marker.text))
    if not m_words:
        return True
    for note in note_texts:
        n_words = set(content_words(note))
        if not n_words:
            continue
        overlap = len(m_words & n_words) / len(m_words)
        # Short markers (e.g. tool failures) need near-full overlap.
        needed = 0.8 if len(m_words) <= 4 else 0.5
        if overlap >= needed:
            return True
    return False


def audit(handoff: Handoff, transcript: Transcript) -> AuditResult:
    markers = collect_markers(transcript)
    note_texts = [i.text for i in handoff.items
                  if i.status in ("active", "stale", "superseded")]
    if handoff.goal:
        note_texts.append(handoff.goal)
    missed = [m for m in markers if not _covered(m, note_texts)]
    total = len(markers)
    captured = total - len(missed)
    score = (captured / total) if total else 1.0
    return AuditResult(total=total, captured=captured, missed=missed, score=score)


def close(handoff: Handoff, audit_result: AuditResult,
          threshold: float = DEFAULT_THRESHOLD, force: bool = False) -> int:
    """Attempt to mark the handoff closed.

    Returns 0 on close, 2 on refusal. A forced close is recorded in the
    sidecar as forced=True — never silently.
    """
    if audit_result.score >= threshold:
        handoff.closed = {"closed": True, "forced": False,
                          "coverage": round(audit_result.score, 3),
                          "threshold": threshold}
        return 0
    if force:
        print(f"WARNING: closing handoff at {audit_result.score:.0%} coverage "
              f"(threshold {threshold:.0%}) — forced", file=sys.stderr)
        handoff.closed = {"closed": True, "forced": True,
                          "coverage": round(audit_result.score, 3),
                          "threshold": threshold}
        return 0
    print(f"REFUSED: coverage {audit_result.score:.0%} below threshold "
          f"{threshold:.0%} — {len(audit_result.missed)} uncaptured markers. "
          f"Re-run with --force to close anyway (it will be logged).",
          file=sys.stderr)
    return 2


def handoff_from_note(note_path: str) -> Optional[Handoff]:
    """Best-effort reconstruction of a Handoff from a handoff.md note.

    Only the section structure is recovered (items lose their evidence
    links); enough for the audit's note side.
    """
    from .model import HandoffItem
    try:
        with open(note_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    import re
    handoff = Handoff(session_id="from-note", source="unknown", cwd=None,
                      transcript_path="", generated_at="", goal="", items=[])
    current: Optional[str] = None
    for line in text.splitlines():
        m = re.match(r"##\s+(.+?)\s*$", line)
        if m:
            sec = m.group(1).strip().upper().rstrip("]")
            current = sec if sec in ("NEXT", "DECIDED", "REJECTED", "FAILED", "DON'T") else None
            continue
        if current and line.strip().startswith("- "):
            body = line.strip()[2:].replace(" [STALE]", "")
            if body != "(none)":
                handoff.items.append(HandoffItem(section=current, text=body,
                                                 evidence="", turn_seq=-1,
                                                 confidence=0.5))
    return handoff


def audit_against_note(note_path: str, transcript: Transcript) -> Optional[AuditResult]:
    handoff = handoff_from_note(note_path)
    if handoff is None:
        return None
    return audit(handoff, transcript)
