"""Shared data model: Transcript -> Turn -> Handoff -> HandoffItem."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """One tool invocation observed in a transcript."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    output: str = ""
    ok: bool = True
    turn_seq: int = -1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Turn:
    """One conversational turn: user text, assistant text, or a tool trace."""

    seq: int                       # line/record index in source file
    role: str                      # 'user' | 'assistant' | 'system'
    text: str                      # plain text content
    timestamp: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    source: str = "unknown"        # 'claude' | 'codex'
    interrupted: bool = False      # user hit Esc / [Request interrupted ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Transcript:
    """A parsed session file from one vendor."""

    session_id: str
    source: str                    # 'claude' | 'codex'
    cwd: Optional[str]
    turns: List[Turn] = field(default_factory=list)
    path: str = ""
    is_subagent: bool = False      # codex guardian/sub-agent threads
    raw_lines: int = 0             # lines seen on disk (for audit parity)


SECTIONS = ("NOW", "NEXT", "DECIDED", "REJECTED", "FAILED", "DON'T", "GOAL")


@dataclass
class HandoffItem:
    """One extracted fact in the handoff note."""

    section: str                   # one of SECTIONS
    text: str                      # the fact, as written for the next agent
    evidence: str                  # short verbatim quote from the transcript
    turn_seq: int                  # source turn index
    timestamp: Optional[str] = None
    confidence: float = 0.5
    status: str = "active"         # active | superseded | stale | done
    superseded_by: Optional[int] = None   # index of the HandoffItem that replaced this
    merged_from: List[int] = field(default_factory=list)  # dup turn indexes folded in

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Handoff:
    """The portable handoff artifact: one note + one JSON sidecar."""

    session_id: str
    source: str
    cwd: Optional[str]
    transcript_path: str
    generated_at: str
    goal: str
    items: List[HandoffItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    closed: Optional[Dict[str, Any]] = None  # set by `verify --close`

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def items_in(self, section: str) -> List[HandoffItem]:
        return [i for i in self.items if i.section == section and i.status == "active"]
