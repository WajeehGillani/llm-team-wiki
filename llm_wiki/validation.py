"""Bounded reads, safe local paths, and an offline wiki link checker."""

import html
import re
from pathlib import Path, PurePosixPath

LINK = re.compile(r"\[[^\]\n]*\]\(([^)\n]+)\)")
SOURCE = re.compile(r"source-[0-9a-f]{16}\.md")
CITATION = re.compile(r"\[(source-[0-9a-f]{16})\]\(../../raw/\1\.md\)")
MAX_NOTE = 32_768
MAX_WIKI = 98_304


def safe_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError(f"Unsafe path: {name}")
    if root.is_symlink():
        raise ValueError("Memory root must not be a symlink")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlinks are not supported: {name}")
    return path


def read_text(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Size limit exceeded: {path.name}")
    return data.decode("utf-8")


def plain(text: str) -> str:
    # Escape model text so it cannot introduce Markdown links, HTML, or table rows.
    text = " ".join(text.split())
    return "".join(f"&#{ord(c)};" if c in "&<>|[]`()!*_#\\" else c for c in text)


def unplain(text: str) -> str:
    return html.unescape(text)


def pages(root: Path) -> dict[str, str]:
    folder = safe_path(root, "wiki")
    if not folder.exists():
        return {}
    found = {}
    total = 0
    for path in sorted(folder.rglob("*")):
        safe_path(root, path.relative_to(root).as_posix())
        if path.is_file() and path.suffix == ".md":
            text = read_text(path, MAX_WIKI)
            total += len(text.encode("utf-8"))
            if total > MAX_WIKI:
                raise ValueError("Wiki exceeds 96 KiB; this demo is designed for a small corpus.")
            found[path.relative_to(folder).as_posix()] = text
    return found


def lint(root: Path) -> list[str]:
    issues = []
    content = pages(root)
    if not content:
        return ["No wiki pages yet; ingest a note first."]
    for name, text in content.items():
        sources = []
        for target in LINK.findall(text):
            # Generated links are relative; normalize only within this memory root.
            absolute = root / "wiki" / Path(name).parent / target
            if target.startswith(("/", "http:", "https:")) or "\\" in target:
                issues.append(f"{name}: unsupported link {target}")
                continue
            try:
                # Inspect before resolving, so symlinks cannot disappear in normalization.
                if any(p.is_symlink() for p in (absolute, *absolute.parents)
                       if p == root or p.is_relative_to(root)):
                    raise ValueError("symlink")
                relative = absolute.resolve().relative_to(root.resolve()).as_posix()
                destination = safe_path(root, relative)
                if not destination.is_file():
                    issues.append(f"{name}: broken link {target}")
                if relative.startswith("raw/") and SOURCE.fullmatch(destination.name):
                    sources.append(target)
            except ValueError:
                issues.append(f"{name}: unsafe link {target}")
        if name != "index.md" and not sources:
            issues.append(f"{name}: missing source reference")
        if name.startswith("topics/"):
            for line in text.splitlines():
                if line.startswith("| ") and not line.startswith(("| Claim |", "| ---")):
                    cells = line.split("|")
                    if len(cells) != 5 or not CITATION.search(cells[3]):
                        issues.append(f"{name}: claim missing source reference")
    return issues
