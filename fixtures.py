"""Synthetic transcripts in both vendor formats for tests."""
import json


def claude_session() -> str:
    lines = [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "timestamp": "2026-10-20T10:00:00Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"role": "user",
                     "content": [{"type": "text",
                                  "text": "Add a refund endpoint to the API"}]}},
        # One response split across two records (real Claude Code behavior).
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "timestamp": "2026-10-20T10:00:05Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"id": "msg_1", "model": "claude-x", "role": "assistant",
                     "usage": {"input_tokens": 10, "output_tokens": 20},
                     "content": [{"type": "text",
                                  "text": "I'll add the refund endpoint now."}]}},
        {"type": "assistant", "uuid": "a2", "parentUuid": "u1",
         "timestamp": "2026-10-20T10:00:05Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"id": "msg_1", "model": "claude-x", "role": "assistant",
                     "usage": {"input_tokens": 10, "output_tokens": 20},
                     "content": [{"type": "tool_use", "id": "toolu_1",
                                  "name": "Bash",
                                  "input": {"command": "pytest -q"}}]}},
        {"type": "user", "uuid": "u2", "parentUuid": "a2",
         "timestamp": "2026-10-20T10:00:40Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                  "content": "2 failed, 5 passed"}]}},
        {"type": "assistant", "uuid": "a3", "parentUuid": "u2",
         "timestamp": "2026-10-20T10:01:00Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"id": "msg_2", "role": "assistant",
                     "content": [{"type": "text",
                                  "text": "I decided to go with pytest markers "
                                          "instead of nose for the new tests. "
                                          "Next step: write the refund route."}]}},
        # System wrapper — must be filtered, not treated as user text.
        {"type": "user", "uuid": "u3", "parentUuid": "a3",
         "timestamp": "2026-10-20T10:02:00Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"role": "user",
                     "content": [{"type": "text",
                                  "text": "<system-reminder>compact now</system-reminder>"}]}},
        # Interrupted request.
        {"type": "user", "uuid": "u4", "parentUuid": "a3",
         "timestamp": "2026-10-20T10:03:00Z", "sessionId": "sess-1",
         "cwd": "/tmp/proj",
         "message": {"role": "user",
                     "content": [{"type": "text",
                                  "text": "[Request interrupted by user]"}]}},
        "this line is not json",
        {"type": "queue-operation", "uuid": "q1",
         "timestamp": "2026-10-20T10:04:00Z", "sessionId": "sess-1"},
    ]
    return "\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines)


def codex_session_new_style() -> str:
    lines = [
        {"timestamp": "2026-10-20T10:00:00Z", "type": "session_meta",
         "id": "01abc", "cwd": "/tmp/proj", "cli_version": "1.2.3",
         "originator": "codex_cli_rs", "source": "vscode"},
        {"timestamp": "2026-10-20T10:00:01Z", "type": "user_message",
         "id": "um1",
         "content": [{"type": "input_text", "text": "Refactor the auth module"}]},
        {"timestamp": "2026-10-20T10:00:05Z", "type": "exec_command_begin",
         "call_id": "c1", "command": ["npm", "test"]},
        {"timestamp": "2026-10-20T10:00:40Z", "type": "exec_command_end",
         "call_id": "c1", "exit_code": 1,
         "formatted_output": "FAIL tests/auth.test.ts ✕ token expiry"},
        {"timestamp": "2026-10-20T10:01:00Z", "type": "response_item",
         "item": {"type": "message", "role": "assistant",
                  "content": [{"type": "output_text",
                               "text": "We decided to switch from JWT to "
                                       "session cookies after the expiry bug. "
                                       "Don't use the old token helper."}]}},
        # Unknown future rollout item — must not crash the reader.
        {"timestamp": "2026-10-20T10:02:00Z", "type": "quantum_entanglement_v9",
         "payload": {"mystery": True}},
    ]
    return "\n".join(json.dumps(l) for l in lines)


def codex_session_old_style() -> str:
    lines = [
        {"type": "session_meta",
         "payload": {"id": "02def", "cwd": "/tmp/old", "cli_version": "0.9",
                     "originator": "cli", "source": "terminal"}},
        {"type": "event_msg",
         "payload": {"type": "agent_message",
                     "message": "I decided to keep the sqlite store for now."}},
        {"type": "event_msg",
         "payload": {"type": "exec_command_begin", "call_id": "e1",
                     "command": "make build"}},
        {"type": "event_msg",
         "payload": {"type": "exec_command_end", "call_id": "e1",
                     "output": "build ok", "error": None}},
    ]
    return "\n".join(json.dumps(l) for l in lines)


def codex_subagent_session() -> str:
    lines = [
        {"timestamp": "2026-10-20T10:00:00Z", "type": "session_meta",
         "id": "03ghi", "cwd": "/tmp/proj", "cli_version": "1.2.3",
         "originator": "cli",
         "source": {"subagent": {"other": "guardian"}},
         "parent_thread_id": "01abc"},
        {"timestamp": "2026-10-20T10:00:01Z", "type": "user_message",
         "id": "um9", "content": [{"type": "input_text",
                                   "text": "review this command"}]},
    ]
    return "\n".join(json.dumps(l) for l in lines)


def supersede_session() -> str:
    """Two DECIDED items where the later one reverses the earlier."""
    lines = [
        {"type": "user", "uuid": "u1", "timestamp": "2026-10-20T10:00:00Z",
         "sessionId": "s", "cwd": "/x",
         "message": {"role": "user",
                     "content": [{"type": "text", "text": "Pick a database"}]}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-20T10:01:00Z",
         "sessionId": "s", "cwd": "/x",
         "message": {"id": "m1", "role": "assistant",
                     "content": [{"type": "text",
                                  "text": "I decided to use SQLite for the "
                                          "session store, it keeps things simple."}]}},
        {"type": "assistant", "uuid": "a2", "timestamp": "2026-10-20T10:02:00Z",
         "sessionId": "s", "cwd": "/x",
         "message": {"id": "m2", "role": "assistant",
                     "content": [{"type": "text",
                                  "text": "Actually, I reverted the SQLite "
                                          "decision and switched to Postgres "
                                          "for the session store instead."}]}},
    ]
    return "\n".join(json.dumps(l) for l in lines)
