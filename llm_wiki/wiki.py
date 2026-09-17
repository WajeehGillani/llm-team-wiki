"""Compile notes into Markdown; retain evidence instead of replacing claims."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from llm_wiki.llm import Answer, Proposal
from llm_wiki.validation import CITATION, MAX_NOTE, MAX_WIKI, lint, pages, plain, read_text, safe_path, unplain

UNKNOWN = "That information is not in the wiki."


def parse_topic(text):
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("Invalid topic heading")
    claims = {}
    for line in lines:
        if not line.startswith("| ") or line.startswith(("| Claim |", "| ---")):
            continue
        cells = line.split("|")
        if len(cells) != 5:
            raise ValueError("Invalid topic claim table")
        key, value = unplain(cells[1].strip()), unplain(cells[2].strip())
        sources = CITATION.findall(cells[3])
        if not key or not value or not sources:
            raise ValueError("A topic claim is missing text or source evidence")
        claims.setdefault((key, value), set()).update(sources)
    if not claims:
        raise ValueError("Topic has no cited claims")
    return unplain(lines[0][2:]), claims


def render_topic(slug, topics):
    title, claims = topics[slug]
    rows = [f"# {plain(title)}", "", "## Claims", "",
            "| Claim | Value | Sources |", "| --- | --- | --- |"]
    fields = {}
    own_sources = set()
    for (key, value), sources in sorted(claims.items()):
        refs = ", ".join(f"[{s}](../../raw/{s}.md)" for s in sorted(sources))
        rows.append(f"| {plain(key)} | {plain(value)} | {refs} |")
        fields.setdefault(key, set()).add(value)
        own_sources.update(sources)
    conflicts = [key for key, values in fields.items() if len(values) > 1]
    rows += ["", "## Conflicts", ""]
    rows += [f"- **Unresolved: {plain(key)}.** Competing claims above retain their sources."
             for key in sorted(conflicts)] or ["No competing values recorded."]
    rows += ["", "## Related topics", ""]
    links = []
    for other, (label, facts) in sorted(topics.items()):
        if other != slug and own_sources.intersection(set().union(*facts.values())):
            links.append(f"- [{plain(label)}]({other}.md)")
    rows += links or ["No related topics yet."]
    return "\n".join(rows) + "\n"


def ingest(root: Path, source: Path, client) -> str:
    if source.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("Use a UTF-8 .txt or .md note.")
    # Binary read preserves original newlines and bytes.
    note = read_text(source, MAX_NOTE)
    if not note.strip():
        raise ValueError("The note is empty.")
    original = note.encode("utf-8")
    source_id = "source-" + hashlib.sha256(original).hexdigest()[:16]
    raw = safe_path(root, f"raw/{source_id}.md")
    existing = pages(root)
    if existing:
        issues = lint(root)
        if issues:
            raise ValueError("Fix wiki health issues before ingest: " + "; ".join(issues))
    if raw.exists() and raw.read_bytes() != original:
        raise ValueError("Source ID collision; existing source was preserved.")
    if raw.exists() and f"sources/{source_id}.md" in existing:
        return source_id + " already ingested"
    topics = {}
    for name, text in existing.items():
        if name.startswith("topics/"):
            slug = name.removeprefix("topics/").removesuffix(".md")
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
                raise ValueError("Invalid existing topic path")
            topics[slug] = parse_topic(text)
        elif name != "index.md" and not re.fullmatch(r"sources/source-[0-9a-f]{16}\.md", name):
            raise ValueError("Unknown wiki page; preserve it outside the managed wiki.")
    # Refuse to discard unsupported manual sections during a render.
    for slug in topics:
        if existing[f"topics/{slug}.md"] != render_topic(slug, topics):
            raise ValueError("Topic was manually edited; preserve edits before ingest.")
    proposal = Proposal.model_validate(client.compile(note, existing).model_dump())
    if len({t.slug for t in proposal.topics}) != len(proposal.topics):
        raise ValueError("Model returned duplicate topic paths.")
    for topic in proposal.topics:
        title, claims = topics.setdefault(topic.slug, (topic.title, {}))
        for claim in topic.claims:
            key = " ".join(claim.key.casefold().split())
            value = " ".join(claim.value.split())
            claims.setdefault((key, value), set()).add(source_id)
    updated = dict(existing)
    for slug in topics:
        updated[f"topics/{slug}.md"] = render_topic(slug, topics)
    related = "\n".join(f"- [{plain(t.title)}](../topics/{t.slug}.md)" for t in proposal.topics)
    updated[f"sources/{source_id}.md"] = (
        f"# {plain(proposal.title)}\n\n{plain(proposal.summary)}\n\n"
        f"Source: [{source_id}](../../raw/{source_id}.md)\n\n## Topics\n\n{related}\n"
    )
    index = ["# Team Wiki", "", "## Topics", ""]
    index += [f"- [{plain(title)}](topics/{slug}.md)" for slug, (title, _) in sorted(topics.items())]
    index += ["", "## Sources", ""]
    index += [f"- [{text.splitlines()[0][2:]}]({name})" for name, text in sorted(updated.items())
              if name.startswith("sources/")]
    updated["index.md"] = "\n".join(index) + "\n"
    if sum(len(text.encode("utf-8")) for text in updated.values()) > MAX_WIKI:
        raise ValueError("Result exceeds the 96 KiB wiki limit; nothing was written.")
    # Finish all model calls/validation before touching published data.
    root.mkdir(parents=True, exist_ok=True)
    wiki = safe_path(root, "wiki")
    safe_path(root, "raw").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".compile-", dir=root) as temporary:
        stage = Path(temporary) / "wiki"
        stage.mkdir()
        for name, text in updated.items():
            path = safe_path(stage, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if not raw.exists():
            # Exclusive creation prevents overwriting original evidence.
            with raw.open("xb") as stream:
                stream.write(original)
        backup = Path(temporary) / "previous"
        if wiki.exists():
            os.replace(wiki, backup)
        try:
            os.replace(stage, wiki)
        except BaseException:
            if backup.exists():
                os.replace(backup, wiki)
            raise
    return source_id


def ask(root: Path, question: str, client) -> str:
    if not question.strip() or len(question) > 4000:
        raise ValueError("Use a question between 1 and 4000 characters.")
    content = pages(root)
    if not content:
        return UNKNOWN
    issues = lint(root)
    if issues:
        raise ValueError("Fix wiki health issues before asking: " + "; ".join(issues))
    answer = Answer.model_validate(client.answer(question, content).model_dump())
    if not answer.supported:
        return UNKNOWN
    if not answer.pages or any(p not in content or p == "index.md" for p in answer.pages):
        raise ValueError("Model returned missing or invalid citations.")
    output = [plain(answer.text), "", "Sources:"]
    output += [f"- [{p}](wiki/{p})" for p in dict.fromkeys(answer.pages)]
    # Keep unresolved evidence visible even if synthesis omits an alternative.
    # This small demo reports all recorded conflicts, including possibly unrelated ones.
    notices = []
    for name, text in content.items():
        if not name.startswith("topics/"):
            continue
        _, claims = parse_topic(text)
        fields = {}
        for (key, value), sources in claims.items():
            fields.setdefault(key, []).append((value, sources))
        for key, values in fields.items():
            if len(values) > 1:
                notices.append(f"- [{name}](wiki/{name}): **{plain(key)} is unresolved**.")
                for value, sources in values:
                    refs = ", ".join(f"[{s}](raw/{s}.md)" for s in sorted(sources))
                    notices.append(f"  - {plain(value)} — {refs}")
    if notices:
        output += ["", "Recorded conflicts (may include other topics):", *notices]
    return "\n".join(output)
