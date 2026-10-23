"""Conflict-awareness: supersede detection, staleness, cross-session dedupe.

- Supersede: a later DECIDED item that shares subject keywords with an
  earlier one AND carries reversal language marks the earlier item
  superseded. This is the "later session contradicts an earlier decision"
  case — the next agent must not resurrect the dead decision.
- Staleness: NEXT items whose timestamp is older than `stale_after_days`
  get flagged stale (still shown, never silently dropped — the Medium
  piece's vanishing-todo failure is exactly what we refuse to repeat).
- Dedupe: identical facts extracted from interleaved sessions are folded
  into one item; the duplicate turn indexes are kept in `merged_from`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import List, Tuple

from .model import Handoff, HandoffItem

REVERSAL_MARKERS = [
    "instead", "revert", "went back to", "going back to", "actually",
    "switched to", "no longer", "scrap that", "changed my mind",
    "overrode", "override", "replacing", "replaced",
]

_STOPWORDS = frozenset("""
the a an and or to of in on for with is are was were be been being it its
this that these those as at by from we you they he she our your their
will would can could should shall may might must do does did done not no
so if then than too very just about into over after before between but
""".split())


def content_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9_]{3,}", text.lower())
            if w not in _STOPWORDS]


def shared_keywords(a: str, b: str) -> int:
    return len(set(content_words(a)) & set(content_words(b)))


def _has_reversal(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in REVERSAL_MARKERS)


def detect_supersedes(handoff: Handoff) -> List[Tuple[int, int]]:
    """Return (superseded_idx, superseding_idx) pairs; mutates statuses."""
    decided = [(i, it) for i, it in enumerate(handoff.items)
               if it.section == "DECIDED" and it.status == "active"]
    pairs: List[Tuple[int, int]] = []
    for x in range(len(decided)):
        for y in range(x + 1, len(decided)):
            i, earlier = decided[x]
            j, later = decided[y]
            if earlier.status != "active" or later.status != "active":
                continue
            if shared_keywords(earlier.text, later.text) >= 2 and _has_reversal(later.text):
                earlier.status = "superseded"
                earlier.superseded_by = j
                pairs.append((i, j))
    return pairs


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_staleness(handoff: Handoff, stale_after_days: int = 7,
                     now: datetime | None = None) -> List[int]:
    """Flag old NEXT items as stale. Returns affected item indexes."""
    now = now or datetime.now(timezone.utc)
    flagged: List[int] = []
    for i, item in enumerate(handoff.items):
        if item.section != "NEXT" or item.status != "active":
            continue
        ts = _parse_ts(item.timestamp)
        if ts is None:
            continue
        if (now - ts).days >= stale_after_days:
            item.status = "stale"
            flagged.append(i)
    return flagged


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_items(handoff: Handoff) -> int:
    """Fold duplicate facts (same section + same normalized text) into one.

    Returns the number of duplicates folded.
    """
    seen: dict[str, int] = {}
    folded = 0
    for i, item in enumerate(handoff.items):
        key = item.section + "|" + hashlib.sha1(
            _normalize(item.text).encode()).hexdigest()
        if key in seen:
            first = handoff.items[seen[key]]
            first.merged_from.append(item.turn_seq)
            first.confidence = min(0.95, first.confidence + 0.05)
            item.status = "duplicate"
            folded += 1
        else:
            seen[key] = i
    return folded


def apply_conflict_awareness(handoff: Handoff, stale_after_days: int = 7,
                             now: datetime | None = None) -> dict:
    """Run all three detectors; return a summary for reporting."""
    pairs = detect_supersedes(handoff)
    stale = detect_staleness(handoff, stale_after_days, now)
    folded = dedupe_items(handoff)
    return {
        "superseded": len(pairs),
        "stale": len(stale),
        "duplicates_folded": folded,
    }
