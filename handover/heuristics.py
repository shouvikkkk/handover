"""Deterministic decision extraction — no LLM, fully offline.

Turns -> sentences -> section classification via marker phrases.
Every extracted item keeps the verbatim evidence quote so the
`verify` command can audit what the note claims against the transcript.

Heuristics are deliberately conservative: a sentence must contain a
marker AND carry content (enough words, a verb-ish shape) to be kept.
False positives are worse than false negatives here — the verifier
exists precisely to catch the negatives.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .model import Handoff, HandoffItem, Transcript

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

# (marker, weight) — weight feeds the confidence score.
DECIDED: List[Tuple[str, float]] = [
    ("decided to", 0.4), ("decision:", 0.4), ("decision was", 0.4),
    ("going with", 0.35), ("go with", 0.3), ("settled on", 0.35),
    ("we'll use", 0.35), ("will use", 0.25), ("let's use", 0.3),
    ("chose", 0.3), ("chosen", 0.3), ("picked", 0.25),
    ("final call", 0.4), ("sticking with", 0.35), ("locked in", 0.35),
    ("agreed to", 0.3), ("the approach is", 0.3),
    ("decision", 0.3), ("switched to", 0.3), ("reverted", 0.3),
]
REJECTED: List[Tuple[str, float]] = [
    ("rejected", 0.4), ("won't work", 0.35), ("doesn't work", 0.35),
    ("didn't work", 0.35), ("abandoned", 0.4), ("scrapped", 0.35),
    ("ruled out", 0.4), ("dead end", 0.4), ("instead of", 0.3),
    ("don't use", 0.3), ("do not use", 0.3), ("not viable", 0.35),
]
FAILED: List[Tuple[str, float]] = [
    ("failed", 0.35), ("error", 0.25), ("traceback", 0.4),
    ("crashed", 0.4), ("broke", 0.3), ("broken", 0.3),
    ("exit code", 0.35), ("still failing", 0.4), ("not working", 0.3),
    ("couldn't", 0.25), ("unable to", 0.3),
]
NEXT: List[Tuple[str, float]] = [
    ("next step", 0.4), ("todo", 0.3), ("to-do", 0.3),
    ("remaining", 0.3), ("still need to", 0.35), ("need to", 0.25),
    ("haven't yet", 0.3), ("left to do", 0.35), ("up next", 0.35),
    ("will do next", 0.35), ("follow-up", 0.3), ("not yet done", 0.35),
    ("open question", 0.35), ("unresolved", 0.35),
]
DONT: List[Tuple[str, float]] = [
    ("don't ", 0.25), ("do not ", 0.25), ("never ", 0.3),
    ("must not", 0.4), ("make sure not", 0.35), ("shouldn't", 0.3),
    ("avoid ", 0.3), ("do not commit", 0.4),
]
DONE: List[Tuple[str, float]] = [
    ("done", 0.3), ("finished", 0.3), ("completed", 0.3),
    ("works now", 0.35), ("fixed", 0.3), ("passing", 0.35),
    ("merged", 0.35), ("committed", 0.35), ("deployed", 0.35),
    ("verified", 0.3),
]

# Order matters: DON'T (prohibitions like "don't use X") outranks REJECTED
# (discarded approaches like "we rejected X"), because a sentence can match
# both marker sets and the prohibition reading is the safer one.
SECTION_ORDER = [
    ("DECIDED", DECIDED),
    ("DON'T", DONT),
    ("REJECTED", REJECTED),
    ("FAILED", FAILED),
    ("NEXT", NEXT),
    ("DONE", DONE),   # DONE feeds the NOW section; kept separate internally
]


def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def classify(sentence: str) -> Optional[Tuple[str, float]]:
    """Return (section, confidence) for a sentence, or None.

    First matching section in SECTION_ORDER wins; confidence accumulates
    from marker weights plus small bonuses for specificity.
    """
    low = " " + sentence.lower() + " "
    words = len(sentence.split())
    if words < 4:                      # too short to be a decision
        return None
    for section, markers in SECTION_ORDER:
        conf = 0.2
        hits = 0
        for marker, weight in markers:
            m = marker if marker.endswith((" ", ":")) else " " + marker + " "
            if m in low or (not marker.endswith((" ", ":")) and marker in low):
                hits += 1
                conf += weight
        if hits:
            if words >= 10:
                conf += 0.1            # specific sentences score higher
            if re.search(r"`[^`]+`", sentence):
                conf += 0.1           # names a file/symbol -> concrete
            return section, min(conf, 0.95)
    return None


def _tool_failure_items(transcript: Transcript) -> List[HandoffItem]:
    """FAILED items straight from tool traces — the strongest evidence class."""
    items: List[HandoffItem] = []
    for turn in transcript.turns:
        for tc in turn.tool_calls:
            if tc.ok:
                continue
            desc = tc.name
            args = tc.arguments or {}
            cmd = args.get("command") or args.get("file_path") or ""
            snippet = (tc.output or "")[:160].replace("\n", " ")
            text = f"{desc} failed"
            if cmd:
                text += f" ({str(cmd)[:80]})"
            if snippet:
                text += f" — {snippet}"
            items.append(HandoffItem(
                section="FAILED", text=text,
                evidence=snippet or f"{desc} returned a failure",
                turn_seq=turn.seq, timestamp=turn.timestamp,
                confidence=0.9,
            ))
    return items


def extract_handoff(transcript: Transcript, generated_at: str,
                    goal_override: Optional[str] = None) -> Handoff:
    """Run the full extraction pipeline on a parsed transcript."""
    items: List[HandoffItem] = []
    items.extend(_tool_failure_items(transcript))

    goal = goal_override or ""
    seen_evidence: Dict[str, bool] = {}

    for turn in transcript.turns:
        if turn.role not in ("user", "assistant"):
            continue
        if turn.role == "user" and not goal and turn.text.strip():
            goal = turn.text.strip()[:600]     # opening prompt is the goal
        for sent in split_sentences(turn.text):
            # Skip the goal sentence itself and user-interrupt noise.
            if turn.role == "user" and sent in (goal or ""):
                continue
            cls = classify(sent)
            if cls is None:
                continue
            section, conf = cls
            key = sent.lower()
            if key in seen_evidence:           # same sentence twice (retries)
                continue
            seen_evidence[key] = True
            items.append(HandoffItem(
                section=section, text=sent, evidence=sent,
                turn_seq=turn.seq, timestamp=turn.timestamp,
                confidence=conf,
            ))

    # Sort items by transcript order so the note reads chronologically.
    items.sort(key=lambda i: (i.turn_seq, i.section))

    warnings: List[str] = []
    if not any(i.section == "DECIDED" for i in items):
        warnings.append("no explicit decisions detected — the session may be exploratory")
    if any(t.interrupted for t in transcript.turns):
        warnings.append("session contains interrupted requests ([Request interrupted ...])")

    return Handoff(
        session_id=transcript.session_id,
        source=transcript.source,
        cwd=transcript.cwd,
        transcript_path=transcript.path,
        generated_at=generated_at,
        goal=goal,
        items=items,
        warnings=warnings,
    )
