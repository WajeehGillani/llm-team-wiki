"""Mutation checks prove the live evaluation's oracle rejects data loss."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from evals.launch_dates import FIXTURES, check_dates, main, source_id
from llm_wiki.llm import Claim, Proposal, Topic
from llm_wiki.wiki import ingest


class DateOracleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "memory"
        self.expected = {}
        client = Mock()
        for day, filename in ((12, "launch-kickoff.md"), (19, "launch-update.md")):
            note = FIXTURES / filename
            self.expected[day] = source_id(note)
            client.compile.return_value = Proposal(
                title="Launch note", summary=f"October {day}, 2026 is the claimed launch date.",
                topics=[Topic(slug="project-orbit", title="Project Orbit", claims=[
                    Claim(key="launch date", value=f"October {day}, 2026")])],
            )
            ingest(self.root, note, client)
        self.topic = self.root / "wiki/topics/project-orbit.md"

    def edit(self, transform):
        self.topic.write_text(transform(self.topic.read_text()), encoding="utf-8")

    def test_accepts_both_independently_cited_dates(self):
        check_dates(self.root, self.expected)

    def test_rejects_either_missing_date_even_if_raw_and_summary_keep_it(self):
        original = self.topic.read_text()
        for day in (12, 19):
            with self.subTest(day=day):
                self.topic.write_text("\n".join(
                    line for line in original.splitlines() if f"October {day}, 2026" not in line
                ) + "\n")
                with self.assertRaisesRegex(AssertionError, "Missing independently cited"):
                    check_dates(self.root, self.expected)

    def test_rejects_date_with_wrong_but_existing_source(self):
        self.edit(lambda text: text.replace(self.expected[19], self.expected[12]))
        with self.assertRaisesRegex(AssertionError, "source attribution"):
            check_dates(self.root, self.expected)

    def test_rejects_missing_citation(self):
        source = self.expected[19]
        self.edit(lambda text: text.replace(f"[{source}](../../raw/{source}.md)", ""))
        with self.assertRaises(AssertionError):
            check_dates(self.root, self.expected)

    def test_rejects_combined_date_claim(self):
        self.edit(lambda text: text.replace("October 19, 2026", "October 12, 2026 or October 19, 2026"))
        with self.assertRaisesRegex(AssertionError, "collapsed into one claim"):
            check_dates(self.root, self.expected)

    def test_rejects_invented_third_date(self):
        self.edit(lambda text: text.replace("October 19, 2026", "October 15, 2026"))
        with self.assertRaisesRegex(AssertionError, "invented launch date"):
            check_dates(self.root, self.expected)

    def test_rejects_dates_split_across_different_fields(self):
        self.edit(lambda text: text.replace("| launch date | October 19", "| revised launch date | October 19"))
        with self.assertRaisesRegex(AssertionError, "Missing independently cited"):
            check_dates(self.root, self.expected)

    def test_rejects_missing_conflict_notice(self):
        self.edit(lambda text: text.replace("**Unresolved: launch date.**", "Resolved."))
        with self.assertRaisesRegex(AssertionError, "no longer marked unresolved"):
            check_dates(self.root, self.expected)

    def test_accepts_iso_date_spelling(self):
        self.edit(lambda text: text.replace("October 12, 2026", "2026-10-12").replace("October 19, 2026", "2026-10-19"))
        check_dates(self.root, self.expected)

    def test_missing_key_fails_instead_of_skipping(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main(), 1)
        self.assertIn("OPENAI_API_KEY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
