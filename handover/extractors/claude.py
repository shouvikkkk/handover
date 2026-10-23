"""Claude Code transcript extractor.

Format (verified Oct 2026 against documented behavior of Claude Code 2.1):
  ~/.claude/projects/<munged-cwd>/<session-uuid>.jsonl
one JSON object per line with an envelope:
  {type, uuid, parentUuid, timestamp, sessionId, cwd, gitBranch, version, message}

Record `type` values seen in the wild: user, assistant, system,
queue-operation, attachment, custom-title, ai-title, summary,
file-history-snapshot, pr-link, result, isMeta wrappers.

Important real-world quirks handled here:
  - Newer Claude Code writes ONE assistant record PER content block of a
    response, each with its own uuid but the same message.id and identical
    usage numbers. We merge records by message.id into a single Turn so
    tool calls, text and token accounting are not duplicated.
  - user records also carry tool_result blocks (the tool's answer) and
    <system-reminder> / [Request interrupted ...] wrappers, which are NOT
    user-typed text. Those are filtered or flagged.
  - toolUseResult beside a tool_result can hold structured output; we
    keep the plain-text block as the canonical output.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..model import ToolCall, Transcript, Turn
from .base import BaseExtractor, stream_jsonl

# Record types that never carry conversational content.
_SKIP_TYPES = {
    "queue-operation",
    "attachment",
    "custom-title",
    "ai-title",
    "summary",
    "file-history-snapshot",
    "pr-link",
    "result",
    "system",
}


def _block_text(block: dict) -> str:
    """Plain text from a text/thinking content block."""
    if not isinstance(block, dict):
        return ""
    if block.get("type") in ("text", "thinking"):
        t = block.get("text", "")
        return t if isinstance(t, str) else ""
    return ""


def _is_system_wrapper(text: str) -> bool:
    low = text.strip().lower()
    return (
        "<system-reminder>" in low
        or "[request interrupted" in low
        or low.startswith("<command-name>")
        or low.startswith("<local-command-stdout>")
    )


class ClaudeExtractor(BaseExtractor):
    source_name = "claude"

    def extract(self, path: str, session_id: Optional[str] = None) -> Transcript:
        turns: List[Turn] = []
        # message.id -> Turn, to merge per-block assistant records.
        assistant_by_msg: Dict[str, Turn] = {}
        pending_tools: Dict[str, ToolCall] = {}
        raw_lines = 0
        detected_session = session_id
        cwd = None

        for n, rec in stream_jsonl(path):
            raw_lines += 1
            if rec is None or not isinstance(rec, dict):
                continue
            rtype = rec.get("type")
            if detected_session is None and rec.get("sessionId"):
                detected_session = str(rec.get("sessionId"))
            if cwd is None and rec.get("cwd"):
                cwd = str(rec.get("cwd"))

            if rtype in _SKIP_TYPES:
                continue

            message = rec.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                content = []
            timestamp = rec.get("timestamp")

            if rtype == "assistant":
                msg_id = str(message.get("id") or rec.get("uuid") or f"line-{n}")
                turn = assistant_by_msg.get(msg_id)
                if turn is None:
                    turn = Turn(seq=n, role="assistant", text="", timestamp=timestamp,
                                source="claude")
                    assistant_by_msg[msg_id] = turn
                    turns.append(turn)
                texts: List[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype in ("text", "thinking"):
                        t = _block_text(block)
                        if t.strip():
                            texts.append(t)
                    elif btype == "tool_use":
                        tc = ToolCall(
                            name=str(block.get("name", "unknown")),
                            arguments=block.get("input") if isinstance(block.get("input"), dict) else {},
                            turn_seq=n,
                        )
                        tid = str(block.get("id", ""))
                        if tid:
                            pending_tools[tid] = tc
                        turn.tool_calls.append(tc)
                if texts:
                    chunk = "\n".join(texts)
                    turn.text = (turn.text + "\n" + chunk).strip() if turn.text else chunk

            elif rtype == "user":
                texts: List[str] = []
                interrupted = False
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype in ("text", "thinking"):
                        t = _block_text(block)
                        if not t.strip():
                            continue
                        if "[request interrupted" in t.strip().lower():
                            interrupted = True
                        if _is_system_wrapper(t):
                            continue
                        texts.append(t)
                    elif btype == "tool_result":
                        tid = str(block.get("tool_use_id", ""))
                        out = self._result_text(block)
                        is_err = bool(block.get("is_error"))
                        tc = pending_tools.pop(tid, None)
                        if tc is not None:
                            tc.output = out[:4000]
                            tc.ok = not is_err and not _looks_like_error(out)
                        else:
                            # Orphan tool_result: keep as a failed-trace if it errors.
                            if is_err or _looks_like_error(out):
                                turns.append(Turn(seq=n, role="system",
                                                 text=f"tool_result error: {out[:500]}",
                                                 timestamp=timestamp, source="claude",
                                                 tool_calls=[ToolCall(name="unknown",
                                                                      output=out[:4000],
                                                                      ok=False, turn_seq=n)]))
                text = "\n".join(texts).strip()
                if text or interrupted:
                    turns.append(Turn(seq=n, role="user", text=text,
                                      timestamp=timestamp, source="claude",
                                      interrupted=interrupted))
            # unknown record types are ignored deliberately (format moves fast)

        # Any tool_use that never got a result: keep as-is (still informative).
        return Transcript(
            session_id=detected_session or "unknown",
            source="claude",
            cwd=cwd,
            turns=turns,
            path=path,
            raw_lines=raw_lines,
        )

    @staticmethod
    def _result_text(block: dict) -> str:
        content = block.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") in ("text",):
                        parts.append(str(item.get("text", "")))
                    elif "text" in item:
                        parts.append(str(item["text"]))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(content or "")


def _looks_like_error(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in (
        "traceback", "error:", "failed", "exit code", "command not found",
        "no such file", "permission denied", "✕", "exception",
    ))
