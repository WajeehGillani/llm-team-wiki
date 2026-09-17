import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from llm_wiki.cli import main
from llm_wiki.llm import Answer, Claim, Client, Proposal, Topic
from llm_wiki.validation import lint, pages, safe_path
from llm_wiki.wiki import UNKNOWN, ask, ingest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def proposal(value="October 12, 2026"):
    return Proposal(title="Orbit kickoff", summary="Maya owns Project Orbit.", topics=[
        Topic(slug="project-orbit", title="Project Orbit", claims=[
            Claim(key="launch date", value=value), Claim(key="owner", value="Maya")]),
        Topic(slug="release-planning", title="Release planning", claims=[
            Claim(key="check-in", value="Friday")]),
    ])


class WikiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / "memory"
        self.note = self.folder / "note.md"
        self.note.write_bytes(b"Maya owns Orbit. Launch: October 12, 2026.\r\n")
        self.client = Mock()
        self.client.compile.return_value = proposal()

    def ingest(self):
        return ingest(self.root, self.note, self.client)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_ingest_preserves_bytes_and_creates_cited_linked_pages(self):
        source = self.ingest()
        self.assertEqual((self.root / "raw" / f"{source}.md").read_bytes(), self.note.read_bytes())
        topic = pages(self.root)["topics/project-orbit.md"]
        self.assertIn(f"../../raw/{source}.md", topic)
        self.assertIn("[Release planning](release-planning.md)", topic)
        self.assertEqual(lint(self.root), [])

    def test_duplicate_does_not_call_model_or_change_files(self):
        source = self.ingest()
        before = self.snapshot()
        self.assertEqual(self.ingest(), source + " already ingested")
        self.client.compile.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_new_note_updates_topic_and_keeps_conflicting_evidence(self):
        first = self.ingest()
        self.note.write_text("Sam reports launch October 19, 2026.")
        self.client.compile.return_value = proposal("October 19, 2026")
        second = self.ingest()
        topic = pages(self.root)["topics/project-orbit.md"]
        for expected in (first, second, "October 12, 2026", "October 19, 2026", "**Unresolved: launch date."):
            self.assertIn(expected, topic)
        self.assertEqual(lint(self.root), [])
        self.client.answer.return_value = Answer(text="October 19, 2026.", supported=True,
                                                pages=["topics/project-orbit.md"])
        response = ask(self.root, "When is launch?", self.client)
        self.assertIn("October 12, 2026", response)
        self.assertIn("is unresolved", response)

    def test_query_is_read_only_and_cited(self):
        self.ingest()
        before = self.snapshot()
        self.client.answer.return_value = Answer(text="Maya owns Orbit.", supported=True,
                                                pages=["topics/project-orbit.md"])
        result = ask(self.root, "Who owns Orbit?", self.client)
        self.assertIn("[topics/project-orbit.md](wiki/topics/project-orbit.md)", result)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_and_empty_wiki(self):
        self.assertEqual(ask(self.root, "Budget?", self.client), UNKNOWN)
        self.client.answer.assert_not_called()
        self.ingest()
        self.client.answer.return_value = Answer(text="Unknown.", supported=False, pages=[])
        self.assertEqual(ask(self.root, "Budget?", self.client), UNKNOWN)

    def test_invalid_answer_citations_are_rejected(self):
        self.ingest()
        for citations in ([], ["../../secret"], ["index.md"]):
            self.client.answer.return_value = Answer(text="Invented", supported=True, pages=citations)
            with self.assertRaisesRegex(ValueError, "citations"):
                ask(self.root, "Owner?", self.client)

    def test_lint_detects_broken_links_and_missing_evidence(self):
        source = self.ingest()
        (self.root / "raw" / f"{source}.md").unlink()
        self.assertTrue(any("broken link" in x for x in lint(self.root)))
        (self.root / "wiki/topics/uncited.md").write_text("# Uncited\n\nA claim.\n")
        self.assertTrue(any("missing source reference" in x for x in lint(self.root)))

    def test_model_failure_leaves_wiki_unchanged(self):
        self.ingest()
        before = self.snapshot()
        self.note.write_text("A new source")
        self.client.compile.side_effect = ValueError("Model unavailable")
        with self.assertRaises(ValueError):
            self.ingest()
        self.assertEqual(self.snapshot(), before)

    def test_topic_link_is_not_source_evidence_for_a_claim(self):
        source = self.ingest()
        topic = self.root / "wiki/topics/project-orbit.md"
        text = topic.read_text().replace(
            f"[{source}](../../raw/{source}.md)", "[Related](release-planning.md)", 1)
        topic.write_text(text)
        self.assertTrue(any("claim missing source reference" in x for x in lint(self.root)))

    def test_invalid_model_path_cannot_write_anything(self):
        bad = proposal()
        bad.topics[0].slug = "../../raw/overwrite"
        self.client.compile.return_value = bad
        with self.assertRaises(ValueError):
            self.ingest()
        self.assertFalse(self.root.exists())

    def test_model_text_cannot_inject_links_or_html(self):
        bad = proposal()
        bad.topics[0].claims[0].value = "[secret](../../secret) | <script>alert(1)</script>"
        self.client.compile.return_value = bad
        self.ingest()
        text = pages(self.root)["topics/project-orbit.md"]
        self.assertNotIn("<script>", text)
        self.assertNotIn("[secret]", text)
        self.assertEqual(lint(self.root), [])

    def test_symlink_and_parent_traversal_rejected(self):
        with self.assertRaises(ValueError):
            safe_path(self.root, "../secret")
        self.root.mkdir()
        (self.root / "wiki").symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.ingest()

    def test_unsupported_manual_edits_are_preserved(self):
        self.ingest()
        topic = self.root / "wiki/topics/project-orbit.md"
        topic.write_text(topic.read_text() + "\nManual addition.\n")
        before = self.snapshot()
        self.note.write_text("A new source")
        with self.assertRaisesRegex(ValueError, "manually edited"):
            self.ingest()
        self.assertEqual(self.snapshot(), before)

    def test_publication_failure_restores_previous_wiki(self):
        self.ingest()
        before = pages(self.root)
        self.note.write_text("A new source")
        real_replace = os.replace

        def fail_publish(source, destination):
            if Path(source).name == "wiki" and ".compile-" in str(Path(source).parent):
                raise OSError("Simulated disk error")
            return real_replace(source, destination)

        with patch("llm_wiki.wiki.os.replace", side_effect=fail_publish):
            with self.assertRaises(OSError):
                self.ingest()
        self.assertEqual(pages(self.root), before)
        self.ingest()  # Saved raw evidence can be retried.
        self.assertEqual(lint(self.root), [])

    def test_large_and_unsupported_notes_rejected_before_provider(self):
        self.note.write_bytes(b"a" * 32769)
        with self.assertRaises(ValueError):
            self.ingest()
        with self.assertRaises(ValueError):
            ingest(self.root, self.folder / "note.pdf", self.client)
        self.client.compile.assert_not_called()

    def test_cli_lint_requires_no_key(self):
        self.ingest()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--root", str(self.root), "lint"]), 0)
        self.assertIn("Wiki healthy", output.getvalue())

    def test_provider_refusal_is_explicit(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}), patch("llm_wiki.llm.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value.responses.parse.return_value = Mock(
                status="completed", output_parsed=None)
            with self.assertRaisesRegex(ValueError, "complete result"):
                Client().compile("note", {})

    def test_checked_in_demo_is_healthy_and_has_three_sources(self):
        self.assertEqual(lint(EXAMPLES / "memory"), [])
        self.assertEqual(len(list((EXAMPLES / "memory/raw").glob("*.md"))), 3)


if __name__ == "__main__":
    unittest.main()
