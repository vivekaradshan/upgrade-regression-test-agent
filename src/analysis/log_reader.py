"""Reads and, when necessary, truncates Spark job log files.

Full logs can run to thousands of lines; feeding all of it to pattern
matching or an LLM is wasteful. When a log exceeds max_bytes, keep the
first 50 lines (job startup), the first exception/stack trace found (the
part that usually explains the failure), and the last 200 lines (where a
fatal error usually surfaces) - dropping the noisy middle.
"""

from __future__ import annotations

from pathlib import Path

TRUNCATION_MARKER = "... [TRUNCATED] ..."
FIRST_LINES_COUNT = 50
LAST_LINES_COUNT = 200


class LogReader:
    @staticmethod
    def read_log(log_path: str, max_bytes: int = 500_000) -> str:
        content = Path(log_path).read_text()

        if len(content.encode("utf-8")) <= max_bytes:
            return content

        lines = content.splitlines()
        first_lines = lines[:FIRST_LINES_COUNT]
        last_lines = lines[-LAST_LINES_COUNT:]
        exception_trace = LogReader._extract_exception_trace(lines)

        parts = list(first_lines) + ["", TRUNCATION_MARKER, ""]
        if exception_trace:
            parts += exception_trace + ["", TRUNCATION_MARKER, ""]
        parts += last_lines

        return "\n".join(parts)

    @staticmethod
    def _extract_exception_trace(lines: list[str]) -> list[str]:
        start_idx = None
        for i, line in enumerate(lines):
            if "Traceback" in line or "Exception" in line:
                start_idx = i
                break

        if start_idx is None:
            return []

        end_idx = start_idx + 1
        while end_idx < len(lines) and lines[end_idx].strip() != "":
            end_idx += 1

        return lines[start_idx:end_idx]
