"""Base reader: robust, streaming JSONL parsing shared by all extractors."""
from __future__ import annotations

import json
from typing import Iterator, Optional

from ..model import Transcript


def stream_jsonl(path: str) -> Iterator[tuple[int, Optional[dict]]]:
    """Yield (line_number, parsed_obj). Malformed/non-UTF8 lines yield None.

    Streaming keeps memory flat on huge session files; errors='replace'
    keeps non-UTF8 bytes from killing the whole parse.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                yield n, json.loads(line)
            except (json.JSONDecodeError, ValueError):
                yield n, None


class BaseExtractor:
    """Each vendor extractor turns a session file into a Transcript."""

    source_name = "unknown"

    def extract(self, path: str, session_id: Optional[str] = None) -> Transcript:
        raise NotImplementedError
