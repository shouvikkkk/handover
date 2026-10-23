"""handover CLI.

    handover extract  [--path FILE | --session ID] [--source claude|codex|auto]
                      [--out DIR] [--include-subagents] [--goal TEXT]
    handover verify   NOTE.md --transcript FILE [--close] [--force]
                      [--threshold 0.8] [--json]
    handover inject   --target claude|codex [--note NOTE.md] [--apply]
    handover list     [--source claude|codex|auto]

No network calls, no model calls, stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import __version__
from .conflicts import apply_conflict_awareness
from .discovery import find_sessions, latest_for_cwd
from .extractors.claude import ClaudeExtractor
from .extractors.codex import CodexExtractor
from .heuristics import extract_handoff
from .hooks_gen import (apply_claude_settings, claude_hook_script,
                        claude_settings_snippet, codex_wrapper,
                        install_latest_note)
from .model import Transcript
from .render import write_artifact
from .verifier import (DEFAULT_THRESHOLD, audit, audit_against_note, close,
                       handoff_from_note)


def _extractor_for(source: str):
    if source == "claude":
        return ClaudeExtractor()
    if source == "codex":
        return CodexExtractor()
    raise ValueError(f"unknown source: {source}")


def _detect_source(path: str) -> str:
    """Sniff the first JSON record: session_meta -> codex, sessionId/uuid -> claude."""
    from .extractors.base import stream_jsonl
    for _n, rec in stream_jsonl(path):
        if rec is None:
            continue
        if rec.get("type") == "session_meta":
            return "codex"
        if "sessionId" in rec or "parentUuid" in rec:
            return "claude"
        # Unknown shape: fall through to filename heuristics.
        break
    name = os.path.basename(path)
    return "codex" if "rollout-" in name else "claude"


def _resolve_transcript(args) -> Transcript:
    if args.path:
        source = args.source
        if source == "auto":
            source = _detect_source(args.path)
        ex = _extractor_for(source)
        kw = {"include_subagents": args.include_subagents} if source == "codex" else {}
        return ex.extract(args.path, **kw)
    sessions = find_sessions(args.source)
    if args.session:
        match = [s for s in sessions if s.session_id == args.session]
        if not match:
            sys.exit(f"error: no session {args.session!r} found")
        sf = match[0]
    else:
        sf = latest_for_cwd(os.getcwd(), args.source)
        if sf is None:
            sys.exit("error: no sessions found on this machine "
                     "(~/.claude/projects, ~/.codex/sessions)")
    ex = _extractor_for(sf.source)
    kw = {"include_subagents": args.include_subagents} if sf.source == "codex" else {}
    t = ex.extract(sf.path, session_id=sf.session_id, **kw)
    if t.is_subagent and not args.include_subagents:
        sys.exit("error: that session is a sub-agent thread; "
                 "re-run with --include-subagents to read it")
    return t


def cmd_extract(args) -> int:
    transcript = _resolve_transcript(args)
    if not transcript.turns:
        sys.exit("error: transcript parsed to zero turns — refusing to write an empty handoff")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    handoff = extract_handoff(transcript, generated_at, goal_override=args.goal)
    report = apply_conflict_awareness(handoff, stale_after_days=args.stale_after_days)
    audit_result = audit(handoff, transcript)
    coverage = audit_result.to_dict()
    out_dir = args.out or os.path.join(os.getcwd(), ".handover",
                                       transcript.session_id[:8])
    paths = write_artifact(handoff, out_dir, coverage)
    # Install the note where the inject hook can find it.
    install_latest_note(paths["markdown"], transcript.cwd or os.getcwd())
    print(f"extracted {len([i for i in handoff.items if i.status == 'active'])} items "
          f"from {len(transcript.turns)} turns ({transcript.source})")
    print(f"coverage: {audit_result.score:.0%} "
          f"({audit_result.captured}/{audit_result.total} markers)")
    if report["superseded"]:
        print(f"superseded decisions: {report['superseded']}")
    if report["stale"]:
        print(f"stale todos: {report['stale']}")
    if report["duplicates_folded"]:
        print(f"duplicate facts folded: {report['duplicates_folded']}")
    if handoff.warnings:
        for w in handoff.warnings:
            print(f"warning: {w}")
    print(f"note:  {paths['markdown']}")
    print(f"json:  {paths['json']}")
    return 0


def cmd_verify(args) -> int:
    transcript_path = args.transcript
    handoff = None
    if transcript_path is None:
        # Fall back to the sidecar: handoff.json records the transcript path.
        sidecar_path = (args.note[:-3] + ".json") if args.note.endswith(".md") \
            else args.note
        try:
            with open(sidecar_path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
        except OSError:
            sys.exit(f"error: no --transcript given and no sidecar at {sidecar_path}")
        transcript_path = sidecar.get("transcript_path")
        from .model import HandoffItem, Handoff as HandoffModel
        handoff = HandoffModel(
            session_id=sidecar.get("session_id", ""), source=sidecar.get("source", ""),
            cwd=sidecar.get("cwd"), transcript_path=transcript_path or "",
            generated_at=sidecar.get("generated_at", ""), goal=sidecar.get("goal", ""),
            items=[HandoffItem(**i) for i in sidecar.get("items", [])],
            warnings=sidecar.get("warnings", []), closed=sidecar.get("closed"))
        if not transcript_path or not os.path.exists(transcript_path):
            sys.exit("error: sidecar's transcript_path is missing or gone; "
                     "pass --transcript FILE explicitly")
    ex = _extractor_for(_detect_source(transcript_path))
    transcript = ex.extract(transcript_path)
    if handoff is None:
        result = audit_against_note(args.note, transcript)
    else:
        result = audit(handoff, transcript)
    if result is None:
        sys.exit(f"error: could not read note {args.note}")
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"coverage: {result.score:.0%} "
              f"({result.captured}/{result.total} markers)")
        if result.missed:
            print(f"\nuncaptured ({len(result.missed)}):")
            for m in result.missed[:20]:
                print(f"  [turn {m.turn_seq}] {m.text[:120]}")
            if len(result.missed) > 20:
                print(f"  ... and {len(result.missed) - 20} more")
    if args.close:
        if handoff is None:
            handoff = handoff_from_note(args.note)
            assert handoff is not None
        rc = close(handoff, result, threshold=args.threshold, force=args.force)
        if rc == 0:
            # Persist the close record into the sidecar so it is auditable.
            sidecar_path = (args.note[:-3] + ".json") if args.note.endswith(".md") \
                else args.note
            if os.path.exists(sidecar_path):
                try:
                    with open(sidecar_path, encoding="utf-8") as fh:
                        sc = json.load(fh)
                    sc["closed"] = handoff.closed
                    with open(sidecar_path, "w", encoding="utf-8") as fh:
                        json.dump(sc, fh, indent=2)
                    print(f"recorded close in {sidecar_path}")
                except OSError as exc:
                    print(f"warning: could not update sidecar: {exc}")
        return rc
    return 0 if result.score >= args.threshold else 2


def cmd_inject(args) -> int:
    if args.note:
        dest = install_latest_note(args.note, os.getcwd())
        print(f"installed note -> {dest}")
    if args.target == "claude":
        info = claude_hook_script(apply=args.apply)
        if args.apply:
            settings = apply_claude_settings()
            print(f"hook script: {info['script_path']}")
            print(f"merged SessionStart hook into {settings} (backup kept)")
        else:
            print("--- hook script (~/.handover/inject-hook.sh) ---")
            from .hooks_gen import HOOK_SCRIPT
            print(HOOK_SCRIPT)
            print("--- merge into ~/.claude/settings.json ---")
            print(claude_settings_snippet())
            print("\nre-run with --apply to install both (backups kept)")
    else:
        info = codex_wrapper(apply=args.apply)
        if args.apply:
            print(f"wrapper installed: {info['script_path']}")
            print('start Codex with: ~/.handover/codex-handover.sh "your prompt"')
        else:
            print("--- wrapper script (~/.handover/codex-handover.sh) ---")
            print(info["script"])
            print("\nre-run with --apply to install")
    return 0


def cmd_list(args) -> int:
    sessions = find_sessions(args.source)
    if not sessions:
        print("no sessions found")
        return 0
    for s in sessions[:30]:
        ts = datetime.fromtimestamp(s.mtime).strftime("%Y-%m-%d %H:%M")
        print(f"{ts}  {s.source:6}  {s.session_id[:12]}  {s.path}")
    if len(sessions) > 30:
        print(f"... and {len(sessions) - 30} more")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="handover",
        description="Portable, verified session handoffs between coding agents.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("extract", help="build a handoff from a session file")
    e.add_argument("--path", help="explicit transcript file")
    e.add_argument("--session", help="session id (see `handover list`)")
    e.add_argument("--source", default="auto", choices=["auto", "claude", "codex"])
    e.add_argument("--out", help="output directory (default .handover/<session>)")
    e.add_argument("--goal", help="override the extracted goal line")
    e.add_argument("--include-subagents", action="store_true",
                   help="also read Codex sub-agent/guardian threads")
    e.add_argument("--stale-after-days", type=int, default=7,
                   help="flag NEXT items older than N days as stale")
    e.set_defaults(func=cmd_extract)

    v = sub.add_parser("verify", help="audit a note's coverage of its transcript")
    v.add_argument("note", help="handoff.md to audit")
    v.add_argument("--transcript", help="source transcript file")
    v.add_argument("--close", action="store_true",
                   help="mark the handoff closed if coverage passes")
    v.add_argument("--force", action="store_true",
                   help="close even below threshold (logged)")
    v.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    i = sub.add_parser("inject", help="generate next-session hook config")
    i.add_argument("--target", default="claude", choices=["claude", "codex"])
    i.add_argument("--note", help="handoff.md to install as the latest note")
    i.add_argument("--apply", action="store_true",
                   help="write files (backups kept)")
    i.set_defaults(func=cmd_inject)

    l = sub.add_parser("list", help="list sessions on this machine")
    l.add_argument("--source", default="auto", choices=["auto", "claude", "codex"])
    l.set_defaults(func=cmd_list)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
