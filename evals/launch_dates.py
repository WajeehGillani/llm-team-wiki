"""Two real ingests; fail if a launch date, its evidence, or conflict disappears."""

import hashlib
import re
import sys
import tempfile
from pathlib import Path

from llm_wiki.llm import Client
from llm_wiki.validation import lint, pages
from llm_wiki.wiki import ingest, parse_topic

FIXTURES = Path(__file__).parent / "fixtures"
DATES = re.compile(
    r"\b(?:Oct(?:ober)?\.?\s+(\d{1,2}),?\s+2026|2026-10-(\d{2}))\b", re.IGNORECASE
)


def source_id(note: Path) -> str:
    return "source-" + hashlib.sha256(note.read_bytes()).hexdigest()[:16]


def check_dates(root: Path, expected: dict[int, str]) -> None:
    """Check claim rows, not dates that happen to survive in raw files or summaries."""
    issues = lint(root)
    if issues:
        raise AssertionError("Wiki health check failed: " + "; ".join(issues))
    groups = []
    for name, text in pages(root).items():
        if not name.startswith("topics/"):
            continue
        _, claims = parse_topic(text)
        fields = {}
        for (key, value), sources in claims.items():
            if "launch" not in key.casefold():
                continue
            dates = {int(first or second) for first, second in DATES.findall(value)}
            if not dates:
                continue
            if len(dates) != 1:
                raise AssertionError(f"{name}: competing launch dates collapsed into one claim")
            day = dates.pop()
            if day not in expected:
                raise AssertionError(f"{name}: invented launch date October {day}, 2026")
            if sources.intersection(expected.values()) != {expected[day]}:
                raise AssertionError(f"{name}: October {day} has missing or incorrect source attribution")
            fields.setdefault(key, set()).add(day)
        for key, dates in fields.items():
            if dates == set(expected):
                if len(expected) > 1 and f"**Unresolved: {key}.**" not in text:
                    raise AssertionError(f"{name}: launch dates are no longer marked unresolved")
                groups.append((name, key))
    if not groups:
        raise AssertionError(
            "Missing independently cited launch dates under the same topic/field: "
            + ", ".join(f"October {day}, 2026" for day in expected)
        )


def main() -> int:
    try:
        client = Client()  # Missing credentials are a failure, never a skipped evaluation.
        notes = [(12, FIXTURES / "launch-kickoff.md"), (19, FIXTURES / "launch-update.md")]
        expected = {}
        with tempfile.TemporaryDirectory(prefix="wiki-live-dates-") as temporary:
            root = Path(temporary) / "memory"
            for day, note in notes:
                ingest(root, note, client)  # Production code and real provider; no mocks.
                expected[day] = source_id(note)
                check_dates(root, expected)
                for _, original in notes[:len(expected)]:
                    raw = root / "raw" / f"{source_id(original)}.md"
                    if raw.read_bytes() != original.read_bytes():
                        raise AssertionError("Original source bytes changed")
                print(f"PASS: {len(expected)} independently cited launch date(s) preserved.", flush=True)
        print(f"PASS: real-model launch conflict regression ({client.model}).")
        return 0
    except (AssertionError, ValueError, OSError) as error:
        print(f"FAIL: live launch-date regression: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
