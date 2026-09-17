# A two-minute walkthrough

All people, project details, and notes here are fictional. The checked-in wiki was
rendered by the real compiler from **hand-authored mocked model responses**. The
question/answer below is illustrative; it is not a captured OpenAI response.

## 1. Inspect the result without a key

```console
$ llm-wiki --root examples/memory lint
Wiki healthy: all links and source references resolve.
```

Open the [index](memory/wiki/index.md), then [Project Orbit](memory/wiki/topics/project-orbit.md).
Follow a source link to see the original note. Compare the two launch-date rows:
both remain visible, with their own evidence and an unresolved conflict notice.

## 2. Compile the three notes yourself

After installing the package and exporting `OPENAI_API_KEY`:

```console
$ llm-wiki ingest examples/notes/01-project.md
Ingested: source-20028940d1f63e21
$ llm-wiki ingest examples/notes/02-decision.md
Ingested: source-43187c33af33cbb0
$ llm-wiki ingest examples/notes/03-conflicting-update.md
Ingested: source-d48efd0e049bd13f
$ llm-wiki lint
Wiki healthy: all links and source references resolve.
```

The source hashes are deterministic for these exact files. The model's topic
selection, summaries, and claim keys can vary. Inspect your output in `memory/`.

## 3. Ask and follow the evidence

```sh
llm-wiki ask "When does Project Orbit launch?"
llm-wiki ask "Why did the team choose SQLite?"
llm-wiki ask "What is the team's budget?"
```

An illustrative launch answer:

```text
The launch date is unresolved. The kickoff says October 12, 2026;
Sam's update says October 19, 2026. Maya has not confirmed either date.

Sources:
- [topics/project-orbit.md](wiki/topics/project-orbit.md)
```

The CLI also appends the recorded competing values and raw-source links. The
budget is absent from the supplied notes, so a grounded answer should be:

```text
That information is not in the wiki.
```

Repeating an identical ingest reports `already ingested`. Asking questions and
running lint leave the wiki unchanged.
