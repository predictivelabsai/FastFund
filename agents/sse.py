"""Server-Sent Events helpers for the agentic chat UI."""
from __future__ import annotations

import json
from typing import Any

TOKEN = "token"
TOOL_START = "tool_start"
TOOL_END = "tool_end"
SOURCES = "sources"
DONE = "done"
ERROR = "error"


def event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"
