#!/usr/bin/env python3
"""Build Feishu-safe XML and sync fixed AI Cultivation docs.

The script reads .local/feishu/manifest.json so future updates do not
need to rediscover space IDs, wiki tokens, or object tokens. Local Markdown is
kept as the authoring source; this script generates Feishu-adapted XML before
uploading to avoid Markdown rendering edge cases in Feishu.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".local/feishu/manifest.json"
DEFAULT_ADAPTED_DIR = ROOT / ".local" / "build" / "feishu-adapted"
TABLE_COLUMN_WIDTH = 160
CALLOUT_COLORS = {
    "NOTE": ("light-blue", "blue"),
    "TIP": ("light-green", "green"),
    "IMPORTANT": ("light-yellow", "yellow"),
    "WARNING": ("light-red", "red"),
    "CAUTION": ("light-red", "red"),
}
MARKDOWN_RESIDUE_MARKERS = ("```", "[!", "](")


class FeishuXmlAdapter:
    """Convert the project's Markdown subset into Feishu XML blocks."""

    def __init__(self, manifest: dict[str, Any], source_path: Path) -> None:
        self.manifest = manifest
        self.source_path = source_path
        self.link_map = self._build_link_map(manifest)
        self.title: str | None = None

    def convert(self, markdown: str) -> str:
        lines = markdown.splitlines()
        blocks: list[str] = []
        paragraph: list[str] = []
        list_items: list[tuple[str, str]] = []
        index = 0

        def flush_paragraph() -> None:
            nonlocal paragraph
            if not paragraph:
                return
            text = " ".join(line.strip() for line in paragraph if line.strip())
            if text:
                blocks.append(f"<p>{self.inline(text)}</p>")
            paragraph = []

        def flush_list() -> None:
            nonlocal list_items
            if not list_items:
                return
            current_kind: str | None = None
            current_items: list[str] = []

            def emit_group() -> None:
                if not current_kind or not current_items:
                    return
                tag = "ol" if current_kind == "ol" else "ul"
                rendered: list[str] = []
                for item in current_items:
                    if tag == "ol":
                        rendered.append(f'<li seq="auto">{self.inline(item)}</li>')
                    else:
                        rendered.append(f"<li>{self.inline(item)}</li>")
                blocks.append(f"<{tag}>" + "".join(rendered) + f"</{tag}>")

            for kind, item in list_items:
                if current_kind != kind:
                    emit_group()
                    current_kind = kind
                    current_items = []
                current_items.append(item)
            emit_group()
            list_items = []

        def flush_all_text() -> None:
            flush_paragraph()
            flush_list()

        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped:
                flush_all_text()
                index += 1
                continue

            if stripped.startswith("```"):
                flush_all_text()
                lang = stripped[3:].strip()
                code_lines: list[str] = []
                index += 1
                while index < len(lines) and not lines[index].strip().startswith("```"):
                    code_lines.append(lines[index])
                    index += 1
                if index < len(lines):
                    index += 1
                code = "\n".join(code_lines)
                if lang == "mermaid":
                    blocks.append(f'<whiteboard type="mermaid">{escape_xml(code)}</whiteboard>')
                else:
                    safe_lang = escape_attr(lang or "text")
                    blocks.append(
                        f'<pre lang="{safe_lang}"><code>{escape_xml(code)}</code></pre>'
                    )
                continue

            if stripped.startswith("|") and self._looks_like_table(lines, index):
                flush_all_text()
                table_lines: list[str] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    table_lines.append(lines[index].strip())
                    index += 1
                blocks.append(self.table(table_lines))
                continue

            if stripped.startswith(">"):
                flush_all_text()
                quote_lines: list[str] = []
                while index < len(lines) and lines[index].strip().startswith(">"):
                    quote_lines.append(lines[index].strip()[1:].strip())
                    index += 1
                blocks.append(self.quote_or_callout(quote_lines))
                continue

            heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if heading:
                flush_all_text()
                level = len(heading.group(1))
                text = heading.group(2).strip()
                if self.title is None and level == 1:
                    self.title = plain_inline(text)
                else:
                    blocks.append(f"<h{level}>{self.inline(text)}</h{level}>")
                index += 1
                continue

            if stripped == "---":
                flush_all_text()
                blocks.append("<hr/>")
                index += 1
                continue

            unordered = re.match(r"^[-*+]\s+(.+)$", stripped)
            ordered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
            if unordered or ordered:
                flush_paragraph()
                kind = "ul" if unordered else "ol"
                item = (unordered or ordered).group(1).strip()
                list_items.append((kind, item))
                index += 1
                continue

            flush_list()
            paragraph.append(stripped)
            index += 1

        flush_all_text()

        doc_title = self.title or self.source_path.stem
        title_block = f"<title>{escape_xml(doc_title)}</title>"
        return title_block + "\n\n" + "\n\n".join(blocks).strip() + "\n"

    def quote_or_callout(self, quote_lines: list[str]) -> str:
        if not quote_lines:
            return "<blockquote></blockquote>"

        match = re.match(r"^\[!(\w+)\]\s*(.*)$", quote_lines[0])
        if match:
            kind = match.group(1).upper()
            emoji = match.group(2).strip() or "💡"
            content_lines = [line for line in quote_lines[1:] if line.strip()]
            body = self._paragraph_blocks(content_lines)
            background, border = CALLOUT_COLORS.get(kind, ("light-blue", "blue"))
            return (
                f'<callout emoji="{escape_attr(emoji)}" background-color="{background}" '
                f'border-color="{border}">\n{body}\n</callout>'
            )

        body = self._paragraph_blocks([line for line in quote_lines if line.strip()])
        return f"<blockquote>\n{body}\n</blockquote>"

    def table(self, table_lines: list[str]) -> str:
        rows = [split_table_row(line) for line in table_lines]
        if len(rows) >= 2 and is_separator_row(rows[1]):
            header = rows[0]
            body_rows = rows[2:]
        else:
            header = rows[0]
            body_rows = rows[1:]

        column_count = max(len(row) for row in [header] + body_rows if row)
        header = normalize_row(header, column_count)
        body_rows = [normalize_row(row, column_count) for row in body_rows]

        colgroup = (
            f'<colgroup><col span="{column_count}" width="{TABLE_COLUMN_WIDTH}"/></colgroup>'
        )
        thead = "<thead><tr>" + "".join(
            self.table_cell("th", cell, is_header=True) for cell in header
        ) + "</tr></thead>"
        tbody_rows = []
        for row in body_rows:
            tbody_rows.append(
                "<tr>" + "".join(self.table_cell("td", cell) for cell in row) + "</tr>"
            )
        tbody = "<tbody>" + "".join(tbody_rows) + "</tbody>"
        return f"<table>\n{colgroup}\n{thead}\n{tbody}\n</table>"

    def table_cell(self, tag: str, text: str, *, is_header: bool = False) -> str:
        attrs = ['vertical-align="middle"']
        if is_header:
            attrs.insert(0, 'background-color="light-gray"')
        return f'<{tag} {" ".join(attrs)}><p align="center">{self.inline(text)}</p></{tag}>'

    def inline(self, text: str) -> str:
        placeholders: list[str] = []

        def store(value: str) -> str:
            placeholders.append(value)
            return f"\u0000{len(placeholders) - 1}\u0000"

        def replace_link(match: re.Match[str]) -> str:
            label = escape_xml(match.group(1))
            href = self.resolve_link(match.group(2))
            if not href:
                return label
            return store(f'<a href="{escape_attr(href)}">{label}</a>')

        def replace_code(match: re.Match[str]) -> str:
            return store(f"<code>{escape_xml(match.group(1))}</code>")

        text = re.sub(r"`([^`]+)`", replace_code, text)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)
        text = escape_xml(text)

        text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)

        for idx, value in enumerate(placeholders):
            text = text.replace(escape_xml(f"\u0000{idx}\u0000"), value)
        return text

    def resolve_link(self, href: str) -> str | None:
        href = href.strip()
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", href):
            return href
        if href.startswith("#"):
            return None

        candidate = PurePosixPath(self.source_path.parent.as_posix()) / href
        normalized = str(candidate)
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith("../"):
            normalized = str(PurePosixPath(normalized))
        return self.link_map.get(normalized)

    def _paragraph_blocks(self, lines: list[str]) -> str:
        paragraphs: list[str] = []
        current: list[str] = []
        for line in lines:
            if not line.strip():
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                continue
            current.append(line.strip())
        if current:
            paragraphs.append(" ".join(current))
        if not paragraphs:
            return "<p></p>"
        return "\n".join(f"  <p>{self.inline(paragraph)}</p>" for paragraph in paragraphs)

    def _looks_like_table(self, lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        return is_separator_row(split_table_row(lines[index + 1].strip()))

    def _build_link_map(self, manifest: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for doc in manifest.get("documents", []):
            source = str(PurePosixPath(doc.get("local_source", "")))
            if source and doc.get("wiki_url"):
                result[source] = doc["wiki_url"]
        return result


def escape_xml(text: str) -> str:
    return html.escape(text, quote=False)


def escape_attr(text: str) -> str:
    return html.escape(text, quote=True)


def plain_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    return text


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def is_separator_row(row: list[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{1,}:?", cell.strip()) for cell in row)


def normalize_row(row: list[str], column_count: int) -> list[str]:
    if len(row) < column_count:
        return row + [""] * (column_count - len(row))
    return row[:column_count]


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Manifest not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def document_key(index: int, doc: dict[str, Any]) -> str:
    return f"{index:02d}"


def adapted_filename(index: int, doc: dict[str, Any]) -> str:
    token = re.sub(r"[^0-9A-Za-z_-]+", "-", doc.get("wiki_token", "doc")).strip("-")
    return f"{document_key(index, doc)}-{token}.xml"


def matches_doc(query: str, index: int, doc: dict[str, Any]) -> bool:
    needle = query.casefold()
    fields = [
        document_key(index, doc),
        doc.get("title", ""),
        doc.get("local_source", ""),
        doc.get("wiki_url", ""),
        doc.get("wiki_token", ""),
        doc.get("obj_token", ""),
    ]
    return any(needle in str(value).casefold() for value in fields)


def select_documents(
    docs: list[dict[str, Any]], queries: list[str]
) -> list[tuple[int, dict[str, Any]]]:
    indexed = list(enumerate(docs))
    if not queries:
        return indexed

    selected: list[tuple[int, dict[str, Any]]] = []
    seen: set[int] = set()
    missing: list[str] = []

    for query in queries:
        matched = [(idx, doc) for idx, doc in indexed if matches_doc(query, idx, doc)]
        if not matched:
            missing.append(query)
            continue
        for idx, doc in matched:
            if idx not in seen:
                selected.append((idx, doc))
                seen.add(idx)

    if missing:
        raise SystemExit("No document matched: " + ", ".join(missing))
    return selected


def print_space(manifest: dict[str, Any]) -> None:
    space = manifest.get("space", {})
    print(f"Project: {manifest.get('project', 'Unknown')}")
    print(f"Profile: {manifest.get('profile', 'main')}")
    print(f"Space: {space.get('name', '')} ({space.get('space_id', '')})")
    print(f"Homepage: {space.get('url_hint', '')}")


def list_documents(manifest: dict[str, Any]) -> None:
    print_space(manifest)
    print()
    for index, doc in enumerate(manifest.get("documents", [])):
        print(f"{document_key(index, doc)}  {doc.get('title', '')}")
        print(f"    source: {doc.get('local_source', '')}")
        print(f"    wiki:   {doc.get('wiki_url', '')}")
        print(f"    obj:    {doc.get('obj_token', '')}")


def build_adapted_docs(
    manifest: dict[str, Any],
    selected: list[tuple[int, dict[str, Any]]],
    adapted_dir: Path,
) -> dict[int, Path]:
    adapted_dir.mkdir(parents=True, exist_ok=True)
    built: dict[int, Path] = {}

    for index, doc in selected:
        source = ROOT / doc["local_source"]
        if not source.exists():
            raise SystemExit(f"Missing local source for {doc.get('title', '')}: {source}")
        markdown = source.read_text(encoding="utf-8")
        adapter = FeishuXmlAdapter(manifest, source.relative_to(ROOT))
        xml = adapter.convert(markdown)
        target = adapted_dir / adapted_filename(index, doc)
        validate_adapted_xml(xml, target)
        target.write_text(xml, encoding="utf-8")
        built[index] = target
    return built


def validate_adapted_xml(xml: str, target: Path) -> None:
    try:
        ET.fromstring(f"<root>{xml}</root>")
    except ET.ParseError as error:
        raise SystemExit(f"Generated XML is invalid for {target}: {error}") from error

    leftovers = [marker for marker in MARKDOWN_RESIDUE_MARKERS if marker in xml]
    if leftovers:
        raise SystemExit(
            f"Generated XML still contains Markdown markers for {target}: "
            + ", ".join(leftovers)
        )


def content_arg(source: Path) -> str:
    """lark-cli requires @file content paths to be relative to cwd."""
    try:
        return f"@{source.relative_to(ROOT)}"
    except ValueError:
        return f"@{source}"


def update_command(profile: str, source: Path, doc: dict[str, Any]) -> list[str]:
    return [
        "lark-cli",
        "--profile",
        profile,
        "docs",
        "+update",
        "--api-version",
        "v2",
        "--doc",
        doc["wiki_url"],
        "--command",
        "overwrite",
        "--doc-format",
        "xml",
        "--content",
        content_arg(source),
    ]


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def sync_documents(
    manifest: dict[str, Any],
    selected: list[tuple[int, dict[str, Any]]],
    *,
    apply: bool,
    profile: str,
    adapted_dir: Path,
    build_only: bool,
) -> None:
    if apply and shutil.which("lark-cli") is None:
        raise SystemExit("lark-cli not found in PATH")

    built = build_adapted_docs(manifest, selected, adapted_dir)

    print_space(manifest)
    print()
    print("Mode: " + ("BUILD ONLY" if build_only else "APPLY" if apply else "DRY RUN"))
    print(f"Adapted XML dir: {adapted_dir}")
    print()

    for index, doc in selected:
        source = built[index]
        command = update_command(profile, source, doc)
        print(f"[{document_key(index, doc)}] {doc.get('title', '')}")
        print(f"    source: {doc.get('local_source', '')}")
        print(f"    adapted: {source.relative_to(ROOT)}")
        if not build_only:
            print(f"    {shell_join(command)}")
            if apply:
                subprocess.run(command, cwd=ROOT, check=True)

    if not apply and not build_only:
        print()
        print("Dry run only. Add --apply to write adapted XML to Feishu.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Feishu-safe XML and sync AI Cultivation docs to Feishu Wiki."
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to .local/feishu/manifest.json.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="lark-cli profile. Defaults to the profile in the manifest.",
    )
    parser.add_argument(
        "--adapted-dir",
        default=str(DEFAULT_ADAPTED_DIR),
        help="Directory for generated Feishu XML files.",
    )
    parser.add_argument(
        "--doc",
        action="append",
        default=[],
        help="Select docs by index, title, token, URL, or local source. Repeatable.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List fixed document IDs and exit.",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Generate adapted XML files without printing or running update commands.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update Feishu docs. Without this flag, commands are printed only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(Path(args.manifest).resolve())
    profile = args.profile or manifest.get("profile") or "main"

    if args.list:
        list_documents(manifest)
        return 0

    docs = manifest.get("documents", [])
    selected = select_documents(docs, args.doc)
    sync_documents(
        manifest,
        selected,
        apply=args.apply,
        profile=profile,
        adapted_dir=Path(args.adapted_dir).resolve(),
        build_only=args.build_only,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
