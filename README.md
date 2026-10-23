# handover

**Portable, verified session handoffs between coding agents.** Switch from Claude Code to Codex (or back) mid-task without the 11-minute copy-paste tax — and without silently losing decisions in transit.

`handover` reads the session files your agents already write to disk, extracts a structured handoff note (NOW / NEXT / DECIDED / REJECTED / FAILED / DON'T), and — the part no other handoff tool does — **audits the note against the transcript** so you know exactly what got dropped before the next agent starts.

No LLM calls. No server. No dependencies beyond the Python standard library. Fully offline.

## The problem

Switching coding agents mid-task is a clipboard tax. A developer who timed it:

> "I timed six of those handoffs in one week: 11 minutes each, on average, of pure copy-paste and re-orientation… Roughly 66 minutes a week spent being a clipboard."
> — [dev.to/thedevtimeline](https://dev.to/thedevtimeline) (Oct 10, 2026)

And hand-written notes silently lose work:

> "Tasks from the previous session were silently disappearing."
> "If they're not written, the next agent decides 'redoing everything is the safe choice.' That happened."
> — [@claire_ai on Medium](https://medium.com/@claire_ai/i-had-a-sessionend-hook-write-claude-codes-handoff-notes-4807dd7624b2) (Oct 22, 2026)

Existing tools either dump raw turns ("The file holds recent turns, not a note written for the next agent" — PonyMux author) or trust whatever the agent wrote. `handover` treats the note as a claim and the transcript as ground truth.

## Quickstart

```bash
# No install needed — stdlib only, Python 3.9+
git clone https://github.com/shouvikkkk/handover
cd handover

# 1. Extract a handoff from your latest session (auto-detects Claude/Codex)
python -m handover extract

# 2. Audit it — what did the note miss?
python -m handover verify .handover/<session>/handoff.md --transcript <file>

# 3. Inject it into your next session automatically
python -m handover inject --target claude --apply
```

## Usage

### `handover extract`

Parses a Claude Code transcript (`~/.claude/projects/*/*.jsonl`) or a Codex CLI rollout (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) into `.handover/<session>/`:

- `handoff.md` — the note the next agent reads first
- `handoff.json` — the machine-readable sidecar (evidence links, timestamps, confidence)

```bash
python -m handover extract --path ~/.claude/projects/-home-me-proj/<id>.jsonl
python -m handover extract --session 01abc123...      # from `handover list`
python -m handover extract --source codex              # latest Codex session
python -m handover extract --goal "Migrate billing to Stripe"
```

Extraction is deterministic pattern/structural heuristics — no model involved, so the same transcript always yields the same note. Tool failures come straight from the traces (non-zero exits, `tool_result` errors), decisions/rejections/todos from marker phrases, each item keeping its verbatim evidence quote and source turn.

### `handover verify` — the coverage audit

Re-scans the source transcript for decision-like markers **not** reflected in the note and reports a coverage score:

```bash
python -m handover verify .handover/<session>/handoff.md --transcript <file>
# coverage: 92% (23/25 markers)
# uncaptured (2):
#   [turn 41] We decided to pin the Postgres version after the breakage...
```

`--close` marks a handoff closed in the JSON sidecar — **fail-closed**: below the threshold (default 80%) it refuses with exit code 2 unless `--force` is given, and a forced close is logged as `forced: true` so it can never be mistaken for a clean one.

### `handover inject`

- **Claude Code**: generates a `SessionStart` hook script + the `settings.json` snippet that prints the latest per-project note into every new session. `--apply` installs both (backups kept).
- **Codex CLI**: has no SessionStart hook system, so `inject` generates a wrapper script (`codex-handover.sh`) that prepends the note to your prompt, plus guidance for the `AGENTS.md` pointer Codex reads at startup.

### Conflict awareness

- **Supersede detection**: a later decision sharing subject keywords with an earlier one, plus reversal language ("actually…", "reverted", "switched to"), marks the earlier decision `superseded` — the next agent sees what died, not just what's alive.
- **Stale todos**: NEXT items older than N days (default 7) are flagged `[STALE]` — still shown, never silently dropped (the vanishing-todo failure from the Medium piece is exactly what this refuses to repeat).
- **Dedupe**: identical facts from interleaved sessions fold into one item, keeping the duplicate turn indexes.

### `handover list`

Lists sessions on the machine, newest first — handy for picking `--session`.

## How the verifier works

1. **Collect markers** — every sentence in the transcript matching the decision/rejection/failure/todo/prohibition marker sets, plus every failed tool call. This is independent of whatever the extractor kept.
2. **Check coverage** — a marker is covered if some note item shares enough of its content words (stopwords stripped, ≥50% overlap; ≥80% for short markers like tool failures). Paraphrase is tolerated; a missing fact is not.
3. **Score & gate** — coverage = captured / total. `--close` refuses below threshold; `--force` closes but records it.

## How it differs

| Tool | Scope | How the note is made | Verified complete? |
|---|---|---|---|
| [ai-memory](https://github.com/akitaonrails/ai-memory) | 20+ agents, via a memory server you run | Agent-written, model-mediated workstreams | No — trusts the writer |
| [claude-mem](https://github.com/thedotmack/claude-mem) | Claude Code only | LLM observer compresses tool output | No — lossy by design |
| [Hindsight](https://www.hindsight.technology/) | Claude + Codex | Post-hoc analytics of what worked | No — retrospective, no handoff artifact |
| [cwc-long-running-agents](https://github.com/anthropics/cwc-long-running-agents) | Claude Code | Hooks + PROGRESS.md discipline | No |
| ccx / PonyMux handoff tools | Claude Code | Recent-turns note | No — "not a note written for the next agent" |
| [agent-handoff](https://github.com/saeedzarbi/agent-handoff) | OpenCode/Codex → 5 agents | Log-to-task converter | No — single conversion, no audit |
| [claude-auto-handoff](https://github.com/alexknowshtml/claude-auto-handoff) | Claude Code | Haiku-written brief | No |
| **handover** | **Claude Code ↔ Codex** | **Deterministic, offline, stdlib-only** | **Yes — coverage audit, fail-closed close** |

The honest gap: nobody else checks the note against the transcript. handover is also the only one with zero install footprint — no server, no worker process, no model spend, nothing to keep running.

## Verified format notes

Both vendor formats are undocumented and drift between releases; these behaviors were verified against documented real-session behavior (Oct 2026) and the extractors are written defensively against them:

- **Claude Code** writes one record per line at `~/.claude/projects/<munged-cwd>/<session-uuid>.jsonl`. Newer versions write **one assistant record per content block** of a response (same `message.id`, identical usage numbers) — handover merges them, so tool calls and tokens aren't double-counted. `user` records carry `tool_result` blocks and `<system-reminder>` wrappers, which are filtered from "what you said".
- **Codex** writes plain-JSONL rollout files at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` (or under `$CODEX_HOME`; `session_index.jsonl` holds thread names). **They are not compressed** — the `.jsonl.zst` claim floating around is wrong; no zstandard dependency needed. The first line is always `session_meta`; sub-agent ("guardian") threads are excluded by default. RolloutItem names change between releases, so unknown item types are skipped, never fatal.

## Limitations

- **Heuristic extraction, not understanding.** The extractor finds decisions by marker phrases and structure; a decision expressed with none of them will be missed — which is exactly what `verify` exists to catch. Always read the coverage report.
- **Format fragility.** Both vendors ship undocumented formats that change. Unknown record types are ignored by design, but a major schema change can silently reduce extraction quality. Pin your CLI versions and re-run `verify`.
- **No semantic dedupe.** Duplicate folding is text-normalization based; the same fact phrased two ways counts twice.
- **Timestamps for staleness** depend on the transcript's clock; interleaved sessions across machines may skew it.
- **Read-only on your history, but treat transcripts as sensitive** — they contain full prompts and tool output. handover never sends anything anywhere (there is no network code in this repo), but the notes it writes inherit that sensitivity.

## License

MIT — Copyright (c) 2026 Shouvik Maji. See [LICENSE](LICENSE).
