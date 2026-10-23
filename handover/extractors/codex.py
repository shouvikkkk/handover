"""Codex CLI rollout extractor.

Format (verified Oct 2026 against Codex desktop + CLI behavior):
  ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
  (or under $CODEX_HOME; archived rollouts sit flat in archived_sessions/)

IMPORTANT, verified: these files are PLAIN .jsonl. The claim that Codex
writes compressed `.jsonl.zst` rollouts is false — no zstandard dependency
is needed.

Line shapes seen in the wild:
  new style: {"timestamp": "...", "type": "session_meta", <flattened payload fields>}
  old style: {"type": "session_meta", "payload": {...}}

The first line is always a `session_meta` record:
  {"type":"session_meta","payload":{"id":..., "cwd":..., "cli_version":...,
    "originator":..., "source": "vscode" | {"subagent": {...}},
    "parent_thread_id": ...}}

Content-bearing rollout items (RolloutItem variants; names drift between
releases, so we map the known ones AND fall back to generic text
extraction):
  - user_message        -> the user's prompt
  - response_item       -> assistant message / function_call
  - event_msg           -> older wrapper; payload.type may be agent_message,
                           exec_command_begin/end, mcp_tool_call_begin/end,
                           web_search_begin/end, reasoning, token_count ...
  - exec_command_begin/end (top-level in some builds)
  - approval_decision, token_count, turn_end -> structural, mostly skipped

Sub-agent threads: session_meta.source is an object like
{"subagent": {"other": "guardian"}} — Codex's automatic pre-command
reviews. These are NOT the user's session and are excluded by default
(see --include-subagents).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..model import ToolCall, Transcript, Turn
from .base import BaseExtractor, stream_jsonl

_TEXT_KEYS = ("text", "content", "message", "prompt", "command", "reasoning_summary")


def _envelope(rec: dict) -> tuple[str, dict, Optional[str]]:
    """Return (type, payload_fields, timestamp) for either envelope style."""
    rtype = str(rec.get("type", ""))
    ts = rec.get("timestamp")
    payload = rec.get("payload")
    if isinstance(payload, dict):
        fields = dict(payload)
    else:
        fields = {k: v for k, v in rec.items() if k not in ("type", "timestamp")}
    return rtype, fields, (str(ts) if ts is not None else None)


def _recursive_text(obj: Any, key_hint: Optional[str] = None, depth: int = 0) -> List[str]:
    """Collect human-readable strings from an arbitrary payload."""
    if depth > 6:
        return []
    out: List[str] = []
    if isinstance(obj, str):
        if key_hint in _TEXT_KEYS and obj.strip() and len(obj.strip()) > 1:
            out.append(obj)
        return out
    if isinstance(obj, list):
        for item in obj:
            out.extend(_recursive_text(item, key_hint, depth + 1))
        return out
    if isinstance(obj, dict):
        # Known content-block shapes first.
        ctype = obj.get("type")
        if ctype in ("text", "input_text", "output_text", "reasoning"):
            t = obj.get("text") or obj.get("content") or obj.get("summary")
            if isinstance(t, str) and t.strip():
                out.append(t)
            return out
        if ctype == "function_call":
            # handled by the caller; skip here
            return out
        for k, v in obj.items():
            if k in ("id", "call_id", "tool_use_id", "timestamp", "cwd",
                     "cli_version", "originator", "source", "parent_thread_id"):
                continue
            out.extend(_recursive_text(v, k, depth + 1))
        return out
    return out


class CodexExtractor(BaseExtractor):
    source_name = "codex"

    def extract(self, path: str, session_id: Optional[str] = None,
               include_subagents: bool = False) -> Transcript:
        turns: List[Turn] = []
        detected_session = session_id
        cwd: Optional[str] = None
        is_subagent = False
        parent_thread: Optional[str] = None
        raw_lines = 0
        pending_exec: Dict[str, ToolCall] = {}
        skip_session = False

        for n, rec in stream_jsonl(path):
            raw_lines += 1
            if rec is None or not isinstance(rec, dict):
                continue
            rtype, fields, ts = _envelope(rec)

            if rtype == "session_meta":
                detected_session = str(fields.get("id") or detected_session or "unknown")
                cwd = fields.get("cwd") or cwd
                parent_thread = fields.get("parent_thread_id")
                source = fields.get("source")
                if isinstance(source, dict) and "subagent" in source:
                    is_subagent = True
                    if not include_subagents:
                        skip_session = True
                        break
                continue

            if skip_session:
                continue

            if rtype in ("user_message",):
                texts = _recursive_text(fields)
                text = "\n".join(t for t in texts if t.strip()).strip()
                if text:
                    turns.append(Turn(seq=n, role="user", text=text,
                                      timestamp=ts, source="codex"))

            elif rtype in ("response_item",):
                item = fields.get("item") if isinstance(fields.get("item"), dict) else fields
                itype = str(item.get("type", ""))
                if itype == "function_call":
                    name = str(item.get("name") or "unknown")
                    args = item.get("arguments")
                    if not isinstance(args, dict):
                        args = {"arguments": args} if args else {}
                    call_id = str(item.get("call_id") or item.get("id") or f"line-{n}")
                    tc = ToolCall(name=name, arguments=args, turn_seq=n)
                    pending_exec[call_id] = tc
                    turns.append(Turn(seq=n, role="assistant", text="",
                                      timestamp=ts, source="codex",
                                      tool_calls=[tc]))
                else:
                    texts = _recursive_text(item)
                    text = "\n".join(t for t in texts if t.strip()).strip()
                    if text:
                        turns.append(Turn(seq=n, role="assistant", text=text,
                                          timestamp=ts, source="codex"))

            elif rtype in ("exec_command_begin",):
                call_id = str(fields.get("call_id") or fields.get("id") or f"line-{n}")
                cmd = fields.get("command")
                if isinstance(cmd, list):
                    cmd = " ".join(str(c) for c in cmd)
                tc = ToolCall(name="exec", arguments={"command": cmd}, turn_seq=n)
                pending_exec[call_id] = tc
                turns.append(Turn(seq=n, role="assistant", text="",
                                  timestamp=ts, source="codex", tool_calls=[tc]))

            elif rtype in ("exec_command_end",):
                call_id = str(fields.get("call_id") or fields.get("id") or "")
                exit_code = fields.get("exit_code", 0)
                output = fields.get("formatted_output") or fields.get("output") or ""
                stderr = fields.get("stderr") or ""
                if isinstance(output, list):
                    output = "\n".join(str(x) for x in output)
                full = str(output)
                if stderr:
                    full += "\n" + str(stderr)
                tc = pending_exec.pop(call_id, None)
                if tc is None:
                    tc = ToolCall(name="exec", turn_seq=n)
                    turns.append(Turn(seq=n, role="assistant", text="",
                                      timestamp=ts, source="codex", tool_calls=[tc]))
                tc.ok = (exit_code == 0)
                tc.output = full[:4000]

            elif rtype in ("event_msg",):
                # Older wrapper: payload carries its own type.
                inner = fields.get("type")
                if inner == "agent_message":
                    texts = _recursive_text(fields)
                    text = "\n".join(t for t in texts if t.strip()).strip()
                    if text:
                        turns.append(Turn(seq=n, role="assistant", text=text,
                                          timestamp=ts, source="codex"))
                elif inner == "user_message":
                    texts = _recursive_text(fields)
                    text = "\n".join(t for t in texts if t.strip()).strip()
                    if text:
                        turns.append(Turn(seq=n, role="user", text=text,
                                          timestamp=ts, source="codex"))
                elif inner in ("exec_command_begin", "mcp_tool_call_begin"):
                    name = str(fields.get("command") or fields.get("tool") or inner)
                    call_id = str(fields.get("call_id") or f"line-{n}")
                    tc = ToolCall(name=name,
                                  arguments={"command": fields.get("command")},
                                  turn_seq=n)
                    pending_exec[call_id] = tc
                    turns.append(Turn(seq=n, role="assistant", text="",
                                      timestamp=ts, source="codex", tool_calls=[tc]))
                elif inner in ("exec_command_end", "mcp_tool_call_end"):
                    call_id = str(fields.get("call_id") or "")
                    ok = not fields.get("error")
                    output = fields.get("output") or fields.get("result") or ""
                    tc = pending_exec.pop(call_id, None)
                    if tc is None:
                        tc = ToolCall(name=inner, turn_seq=n)
                        turns.append(Turn(seq=n, role="assistant", text="",
                                          timestamp=ts, source="codex", tool_calls=[tc]))
                    tc.ok = bool(ok)
                    tc.output = str(output)[:4000]
                # agent_reasoning, token_count, turn_end, approval_decision:
                # structural or duplicative — deliberately skipped.

            # Anything else: unknown rollout item; ignored on purpose.
            # The format is undocumented and drifts; unknown items must not
            # crash extraction. _recursive_text is the safety net only for
            # the mapped types above.

        return Transcript(
            session_id=detected_session or "unknown",
            source="codex",
            cwd=cwd,
            turns=turns,
            path=path,
            is_subagent=is_subagent,
            raw_lines=raw_lines,
        )
