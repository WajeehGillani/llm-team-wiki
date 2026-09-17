"""Three commands; defaults to a disposable ./memory workspace."""

import argparse
import sys
from pathlib import Path

from llm_wiki.llm import Client
from llm_wiki.validation import lint
from llm_wiki.wiki import ask, ingest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Team notes → a cited Markdown wiki")
    parser.add_argument("--root", type=Path, default=Path("memory"), help="memory directory (default: ./memory)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="compile a .txt/.md note").add_argument("file", type=Path)
    commands.add_parser("ask", help="answer from the compiled wiki").add_argument("question")
    commands.add_parser("lint", help="check links and source references offline")
    args = parser.parse_args(argv)
    root = args.root.expanduser().absolute()
    try:
        if args.command == "lint":
            issues = lint(root)
            print("\n".join(issues) if issues else "Wiki healthy: all links and source references resolve.")
            return 1 if issues else 0
        client = Client()
        if args.command == "ingest":
            print("Ingested: " + ingest(root, args.file, client))
        else:
            print(ask(root, args.question, client))
        return 0
    except (ValueError, OSError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
