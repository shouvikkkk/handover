"""Find session files on disk. Read-only; never writes into vendor folders."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

_ROLLOUT_RE = re.compile(r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[0-9a-fA-F-]{36}\.jsonl$")


@dataclass
class SessionFile:
    source: str            # 'claude' | 'codex'
    session_id: str
    path: str
    mtime: float


def _claude_projects_dir() -> str:
    return os.path.expanduser(
        os.environ.get("CLAUDE_CONFIG_DIR", os.path.join("~", ".claude"))
        + "/projects")


def _codex_sessions_dir() -> str:
    return os.path.expanduser(
        os.path.join(os.environ.get("CODEX_HOME", os.path.join("~", ".codex")),
                     "sessions"))


def find_claude_sessions() -> List[SessionFile]:
    out: List[SessionFile] = []
    root = _claude_projects_dir()
    if not os.path.isdir(root):
        return out
    for project in sorted(os.listdir(root)):
        pdir = os.path.join(root, project)
        if not os.path.isdir(pdir):
            continue
        for name in sorted(os.listdir(pdir)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(pdir, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            out.append(SessionFile(source="claude", session_id=name[:-6],
                                   path=path, mtime=mtime))
    return out


def find_codex_sessions() -> List[SessionFile]:
    out: List[SessionFile] = []
    root = _codex_sessions_dir()
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if not _ROLLOUT_RE.match(name):
                continue
            path = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            # session id = uuid suffix after the timestamp
            sid = name[len("rollout-"):name.rfind(".jsonl")][20:]
            out.append(SessionFile(source="codex", session_id=sid,
                                   path=path, mtime=mtime))
    return out


def find_sessions(source: str = "auto") -> List[SessionFile]:
    """All known sessions, newest first. source: claude | codex | auto."""
    out: List[SessionFile] = []
    if source in ("auto", "claude"):
        out.extend(find_claude_sessions())
    if source in ("auto", "codex"):
        out.extend(find_codex_sessions())
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def latest_for_cwd(cwd: Optional[str] = None,
                   source: str = "auto") -> Optional[SessionFile]:
    """Best guess at "the session I was just working in": newest session
    whose project folder matches cwd, else the newest session overall."""
    sessions = find_sessions(source)
    if not sessions:
        return None
    if cwd:
        munged = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        for s in sessions:
            if s.source == "claude" and munged in s.path:
                return s
            if s.source == "codex":
                return s  # codex cwd lives inside the file; check cheaply later
    return sessions[0]
