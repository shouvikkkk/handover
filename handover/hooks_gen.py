"""`handover inject` — auto-inject the handoff note at next-session start.

Claude Code: generates a SessionStart hook snippet to merge into
~/.claude/settings.json, plus the tiny shell script the hook runs.
The hook prints the latest handoff note for the current directory;
Claude Code adds hook stdout to the session context automatically.

Codex CLI: has no SessionStart hook system, so the equivalent is a
wrapper script that prepends "read this handoff note first" to your
prompt, plus an AGENTS.md pointer (Codex reads AGENTS.md at startup).
Both generators PRINT their output; --apply writes files with backups.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from typing import Dict

HOOK_SCRIPT = """#!/bin/sh
# handover SessionStart hook: print the latest handoff note for this project.
# Install: handover inject --target claude --apply
NOTE_DIR="$HOME/.handover/latest"
CWD_MUNGED=$(printf '%s' "$PWD" | sed 's/[^A-Za-z0-9]/-/g')
NOTE="$NOTE_DIR/$CWD_MUNGED.md"
if [ -f "$NOTE" ]; then
  printf '=== HANDOFF FROM PREVIOUS SESSION ===\\n'
  cat "$NOTE"
  printf '\\n=== END HANDOFF ===\\n'
fi
exit 0
"""

CLAUDE_HOOK_SNIPPET = {
    "hooks": {
        "SessionStart": [
            {"hooks": [{"type": "command",
                        "command": os.path.expanduser("~/.handover/inject-hook.sh")}]}
        ]
    }
}

CODEX_WRAPPER = """#!/bin/sh
# handover Codex wrapper: start Codex with the handoff note pre-loaded.
# Usage: codex-handover.sh "your prompt here"
NOTE_DIR="$HOME/.handover/latest"
CWD_MUNGED=$(printf '%s' "$PWD" | sed 's/[^A-Za-z0-9]/-/g')
NOTE="$NOTE_DIR/$CWD_MUNGED.md"
if [ -f "$NOTE" ]; then
  PRE="Read the handoff note below first, then continue the work it describes.\\n\\n$(cat "$NOTE")\\n\\n"
  exec codex "${PRE}$*"
else
  exec codex "$@"
fi
"""


def _backup(path: str) -> None:
    if os.path.exists(path):
        shutil.copy2(path, path + ".handover-bak")


def install_latest_note(note_path: str, cwd: str) -> str:
    """Copy a handoff note into ~/.handover/latest/<munged-cwd>.md so hooks
    can find it per project. Returns the destination path."""
    import re
    dest_dir = os.path.expanduser("~/.handover/latest")
    os.makedirs(dest_dir, exist_ok=True)
    munged = re.sub(r"[^A-Za-z0-9]", "-", cwd or "default")
    dest = os.path.join(dest_dir, munged + ".md")
    shutil.copy2(note_path, dest)
    return dest


def claude_hook_script(apply: bool = False) -> Dict[str, str]:
    """Return the hook script text; with apply=True also install it."""
    path = os.path.expanduser("~/.handover/inject-hook.sh")
    if apply:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _backup(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HOOK_SCRIPT)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return {"script_path": path, "script": HOOK_SCRIPT}


def claude_settings_snippet() -> str:
    return json.dumps(CLAUDE_HOOK_SNIPPET, indent=2)


def apply_claude_settings() -> str:
    """Merge the SessionStart hook into ~/.claude/settings.json with backup.
    Never clobbers existing hooks — appends to the SessionStart list."""
    path = os.path.expanduser("~/.claude/settings.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup(path)
    settings: Dict = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            try:
                settings = json.load(fh) or {}
            except json.JSONDecodeError:
                settings = {}
    hooks = settings.setdefault("hooks", {})
    starts = hooks.setdefault("SessionStart", [])
    new_hook = CLAUDE_HOOK_SNIPPET["hooks"]["SessionStart"][0]
    if new_hook not in starts:
        starts.append(new_hook)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
    return path


def codex_wrapper(apply: bool = False) -> Dict[str, str]:
    path = os.path.expanduser("~/.handover/codex-handover.sh")
    if apply:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _backup(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(CODEX_WRAPPER)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return {"script_path": path, "script": CODEX_WRAPPER}
