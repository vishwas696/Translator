from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
import posixpath
import re
from typing import Any
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from zipfile import ZipFile

from bs4 import BeautifulSoup

from document_adapters import (
    EPUB_OPF_NS,
    WORD_NS,
    _docx_direct_cell_paragraphs,
    _docx_direct_nested_tables,
    _docx_is_self_closing_inline_object,
    _docx_paired_inline_container_kind,
    _docx_relationships,
    _docx_table_cell_text,
    _docx_text,
    _docx_run_vertical_alignment,
    _docx_text_with_inline_placeholders,
    _should_protect_docx_inline_text,
    _epub_extra_item_paths,
    _epub_is_hidden,
    _epub_is_page_break,
    _epub_manifest_items,
    _epub_rootfile,
    _epub_semantic_block_type,
    _epub_spine_items,
    _has_block_parent,
    _html_text,
    _local_name,
)
from document_model import DocumentBlock, ParsedDocument


DOCX_TRANSLATABLE_TYPES = {
    "paragraph",
    "heading",
    "list_item",
    "caption",
    "quote",
    "toc_entry",
    "reference",
    "index_entry",
    "special",
}
EPUB_TRANSLATABLE_TYPES = {
    "paragraph",
    "heading",
    "list_item",
    "caption",
    "quote",
    "footnote",
    "endnote",
    "aside",
    "special",
}
INLINE_PLACEHOLDER_PATTERN = re.compile(r"\[\[INLINE_\d{4}\]\]")
BlockTranslation = str | Mapping[str, Any]


@dataclass(frozen=True)
class DocxInlineTokenItem:
    token: str
    kind: str
    element: ET.Element
    container_kind: str | None = None
    start_token: str | None = None
    end_token: str | None = None
    display_text: str = ""


def _blocks_by_tree_path(blocks: list[DocumentBlock]) -> dict[str, DocumentBlock]:
    blocks_by_path: dict[str, DocumentBlock] = {}
    duplicate_paths: set[str] = set()
    for block in blocks:
        tree_path = block.metadata.get("tree_path")
        if not isinstance(tree_path, str) or not tree_path.strip():
            continue
        if tree_path in blocks_by_path:
            duplicate_paths.add(tree_path)
            continue
        blocks_by_path[tree_path] = block

    for tree_path in duplicate_paths:
        blocks_by_path.pop(tree_path, None)
    return blocks_by_path


@dataclass
class ExportReport:
    output_path: str
    source_format: str
    translatable_block_count: int
    translated_unit_count: int
    applied_unit_count: int = 0
    files_modified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


class TranslationCursor:
    def __init__(
        self,
        blocks: list[DocumentBlock],
        translations_by_block_id: Mapping[str, BlockTranslation],
        report: ExportReport,
    ) -> None:
        self.blocks = blocks
        self.translations_by_block_id = translations_by_block_id
        self.report = report
        self.index = 0
        self.used_block_ids: set[str] = set()
        self.visited_block_ids: set[str] = set()
        self.blocks_by_tree_path = _blocks_by_tree_path(blocks)

    def consume(self, expected_types: set[str] | None = None) -> BlockTranslation | None:
        self._advance_past_visited_blocks()
        if self.index >= len(self.blocks):
            self.report.warn("More document text positions were found than parsed translatable blocks.")
            return None

        block = self.blocks[self.index]
        unit = self._consume_known_block(block, expected_types)
        self.index += 1
        self._advance_past_visited_blocks()
        return unit

    def consume_at_paths(
        self,
        tree_paths: list[str],
        expected_types: set[str] | None = None,
    ) -> BlockTranslation | None:
        block = self._block_for_tree_paths(tree_paths)
        if block is None:
            return self.consume(expected_types)

        unit = self._consume_known_block(block, expected_types)
        self._advance_past_visited_blocks()
        return unit

    def finish(self) -> None:
        unused_block_ids = [
            block_id
            for block_id in self.translations_by_block_id
            if block_id not in self.used_block_ids
        ]
        if unused_block_ids:
            self.report.warn(
                "Translated block ID(s) were not applied: "
                + ", ".join(sorted(unused_block_ids))
            )

    def consume_block(
        self,
        expected_types: set[str] | None = None,
    ) -> tuple[DocumentBlock | None, BlockTranslation | None]:
        self._advance_past_visited_blocks()
        if self.index >= len(self.blocks):
            self.report.warn("More document text positions were found than parsed translatable blocks.")
            return None, None

        block = self.blocks[self.index]
        unit = self._consume_known_block(block, expected_types)
        self.index += 1
        self._advance_past_visited_blocks()
        return block, unit

    def consume_block_at_paths(
        self,
        tree_paths: list[str],
        expected_types: set[str] | None = None,
    ) -> tuple[DocumentBlock | None, BlockTranslation | None]:
        block = self._block_for_tree_paths(tree_paths)
        if block is None:
            return self.consume_block(expected_types)

        unit = self._consume_known_block(block, expected_types)
        self._advance_past_visited_blocks()
        return block, unit

    def _consume_known_block(
        self,
        block: DocumentBlock,
        expected_types: set[str] | None = None,
    ) -> BlockTranslation | None:
        if expected_types and block.type not in expected_types:
            self.report.warn(
                f"Block alignment warning at {block.block_id}: expected one of "
                f"{sorted(expected_types)}, found {block.type}."
            )

        self.visited_block_ids.add(block.block_id)
        unit = None
        if block.block_id in self.translations_by_block_id:
            unit = self.translations_by_block_id[block.block_id]
            self.used_block_ids.add(block.block_id)
            self.report.applied_unit_count += 1
        else:
            self.report.warn(
                f"Missing translated text for {block.block_id}; preserved source text."
            )

        return unit

    def _block_for_tree_paths(self, tree_paths: list[str]) -> DocumentBlock | None:
        for tree_path in tree_paths:
            block = self.blocks_by_tree_path.get(tree_path)
            if block is not None and block.block_id not in self.visited_block_ids:
                return block
        return None

    def _advance_past_visited_blocks(self) -> None:
        while (
            self.index < len(self.blocks)
            and self.blocks[self.index].block_id in self.visited_block_ids
        ):
            self.index += 1


def default_translated_document_path(output_dir: Path, source_format: str) -> Path:
    suffix = {
        "docx": ".docx",
        "epub": ".epub",
        "txt": ".txt",
        "sample": ".txt",
    }.get(source_format, ".txt")
    return output_dir / f"translated_document{suffix}"


def write_translated_document(
    parsed_document: ParsedDocument,
    output_path: Path,
    translated_text: str | None = None,
    translations_by_block_id: Mapping[str, BlockTranslation] | None = None,
) -> ExportReport:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = _translatable_blocks(parsed_document)
    units = _split_translated_text_units(translated_text or "")
    block_translations = (
        _normalize_block_translation_map(translations_by_block_id)
        if translations_by_block_id is not None
        else _align_text_units_to_blocks(blocks, units)
    )
    report = ExportReport(
        output_path=str(output_path),
        source_format=parsed_document.source_format,
        translatable_block_count=len(blocks),
        translated_unit_count=len(block_translations) if translations_by_block_id is not None else len(units),
    )

    if translations_by_block_id is None and len(blocks) != len(units):
        report.warn(
            "Translated text unit count does not match translatable block count. "
            "Export will apply translations sequentially and preserve unmatched source text."
        )

    if parsed_document.source_format in {"txt", "sample"}:
        text = (
            render_translated_blocks(parsed_document, block_translations, report)
            if translations_by_block_id is not None
            else (translated_text or "")
        )
        output_path.write_text(text, encoding="utf-8")
        report.applied_unit_count = min(len(blocks), len(block_translations))
        report.files_modified.append(str(output_path))
        return report

    source_path = Path(parsed_document.source_path or "")
    if not source_path.exists():
        raise FileNotFoundError(
            f"Cannot export {parsed_document.source_format}: source package is missing."
        )

    cursor = TranslationCursor(
        blocks=blocks,
        translations_by_block_id=block_translations,
        report=report,
    )
    if parsed_document.source_format == "docx":
        _write_translated_docx(source_path, output_path, cursor, report)
    elif parsed_document.source_format == "epub":
        _write_translated_epub(source_path, output_path, cursor, report)
    else:
        raise ValueError(f"Unsupported export format: {parsed_document.source_format}")

    cursor.finish()
    return report


def _translatable_blocks(parsed_document: ParsedDocument) -> list[DocumentBlock]:
    return [
        block
        for block in parsed_document.blocks
        if block.translate and block.text.strip()
    ]


def _split_translated_text_units(translated_text: str) -> list[str]:
    normalized = translated_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [unit.strip() for unit in re.split(r"\n\s*\n", normalized) if unit.strip()]


def _align_text_units_to_blocks(
    blocks: list[DocumentBlock],
    translated_units: list[str],
) -> dict[str, str]:
    return {
        block.block_id: translated_units[index]
        for index, block in enumerate(blocks)
        if index < len(translated_units)
    }


def _normalize_block_translation_map(
    translations_by_block_id: Mapping[str, BlockTranslation] | None,
) -> dict[str, BlockTranslation]:
    if not translations_by_block_id:
        return {}
    normalized: dict[str, BlockTranslation] = {}
    for block_id, translated_value in translations_by_block_id.items():
        normalized_block_id = str(block_id).strip()
        if not normalized_block_id:
            continue
        normalized_value = _normalize_block_translation_value(translated_value)
        if normalized_value is not None:
            normalized[normalized_block_id] = normalized_value
    return normalized


def _normalize_block_translation_value(
    translated_value: BlockTranslation,
) -> BlockTranslation | None:
    if isinstance(translated_value, Mapping):
        translated_text = str(translated_value.get("translated_text", "")).strip()
        table_rows = _normalize_structured_table_rows(translated_value.get("table_rows"))
        if not translated_text and table_rows:
            translated_text = _render_plain_table_rows(table_rows)
        if not translated_text:
            return None

        normalized: dict[str, object] = {"translated_text": translated_text}
        if table_rows:
            normalized["table_rows"] = table_rows
        return normalized

    translated_text = str(translated_value).strip()
    return translated_text or None


def _normalize_structured_table_rows(raw_rows: object) -> list[list[str]]:
    if not isinstance(raw_rows, list):
        return []
    rows: list[list[str]] = []
    for row in raw_rows:
        if not isinstance(row, list):
            return []
        rows.append([str(cell).strip() for cell in row])
    return rows


def _translation_text(translated: BlockTranslation) -> str:
    if isinstance(translated, Mapping):
        return str(translated.get("translated_text", "")).strip()
    return str(translated).strip()


def _translation_table_rows(translated: BlockTranslation) -> list[list[str]] | None:
    if not isinstance(translated, Mapping):
        return None
    rows = _normalize_structured_table_rows(translated.get("table_rows"))
    return rows or None


def _render_plain_table_rows(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in rows).strip()


def render_translated_blocks(
    parsed_document: ParsedDocument,
    translations_by_block_id: Mapping[str, BlockTranslation],
    report: ExportReport | None = None,
) -> str:
    parts: list[str] = []
    for block in _translatable_blocks(parsed_document):
        text = translations_by_block_id.get(block.block_id)
        if text is None:
            rendered_text = block.text
            if report is not None:
                report.warn(
                    f"Missing translated text for {block.block_id}; preserved source text."
                )
        else:
            rendered_text = _translation_text(text)
        if rendered_text.strip():
            parts.append(rendered_text.strip())
    return "\n\n".join(parts)


def _write_translated_docx(
    source_path: Path,
    output_path: Path,
    cursor: TranslationCursor,
    report: ExportReport,
) -> None:
    _register_xml_namespaces()
    replacements: dict[str, bytes] = {}

    with ZipFile(source_path) as docx:
        names = set(docx.namelist())
        if "word/document.xml" in names:
            replacements["word/document.xml"] = _translate_docx_document_xml(
                docx.read("word/document.xml"),
                cursor,
                report,
            )

        for part_path, block_type in (
            ("word/footnotes.xml", "footnote"),
            ("word/endnotes.xml", "endnote"),
        ):
            if part_path in names:
                replacements[part_path] = _translate_docx_notes_xml(
                    docx.read(part_path),
                    block_type,
                    cursor,
                    report,
                )

        if "word/comments.xml" in names:
            replacements["word/comments.xml"] = _translate_docx_comments_xml(
                docx.read("word/comments.xml"),
                cursor,
                report,
            )

        relationships = _docx_relationships(docx)
        for block_type in ("header", "footer"):
            for target in _docx_related_parts(relationships, block_type):
                if target in names:
                    replacements[target] = _translate_docx_paragraph_part_xml(
                        docx.read(target),
                        block_type,
                        cursor,
                        report,
                    )

        _copy_zip_with_replacements(docx, output_path, replacements, report)


def _translate_docx_document_xml(
    xml_bytes: bytes,
    cursor: TranslationCursor,
    report: ExportReport,
) -> bytes:
    root = ET.fromstring(xml_bytes)
    body = root.find("w:body", WORD_NS)
    if body is None:
        return xml_bytes

    _translate_docx_container_element(body, cursor, report, context_path="body")

    return _serialize_docx_xml(root)


def _translate_docx_container_element(
    container: ET.Element,
    cursor: TranslationCursor,
    report: ExportReport,
    context_path: str,
) -> None:
    child_counts: dict[str, int] = {}
    for child in list(container):
        tag_name = _local_name(child.tag)
        child_counts[tag_name] = child_counts.get(tag_name, 0) + 1
        child_path = f"{context_path}/{tag_name}[{child_counts[tag_name]}]"
        if tag_name == "p" and _docx_text(child).strip():
            translated = cursor.consume_at_paths(
                _docx_paragraph_tree_path_candidates(child_path),
                DOCX_TRANSLATABLE_TYPES,
            )
            if translated is not None:
                _replace_docx_text(child, _translation_text(translated), report)
        elif tag_name == "tbl":
            _translate_docx_table_element(child, cursor, report, tree_path=child_path)
        elif tag_name == "sdt":
            _translate_docx_sdt_element(child, cursor, report, tree_path=child_path)


def _translate_docx_sdt_element(
    sdt: ET.Element,
    cursor: TranslationCursor,
    report: ExportReport,
    tree_path: str,
) -> None:
    sdt_content = sdt.find("w:sdtContent", WORD_NS)
    if sdt_content is None:
        return

    _translate_docx_container_element(
        sdt_content,
        cursor,
        report,
        context_path=f"{tree_path}/sdtContent",
    )


def _translate_docx_table_element(
    table: ET.Element,
    cursor: TranslationCursor,
    report: ExportReport,
    tree_path: str,
) -> None:
    if _docx_table_has_direct_text(table):
        block, translated = cursor.consume_block_at_paths([tree_path], {"table"})
        if translated is not None:
            translated_text = _translation_text(translated)
            if (
                block is not None
                and str(block.metadata.get("table_role")) == "toc_layout"
            ):
                _replace_docx_contents_layout_table_text(table, translated_text)
            else:
                _replace_docx_table_text(
                    table,
                    translated_text,
                    report,
                    structured_rows=_translation_table_rows(translated),
                )

    for nested_index, nested_table in enumerate(
        _docx_nested_tables_for_writer(table),
        start=1,
    ):
        _translate_docx_table_element(
            nested_table,
            cursor,
            report,
            tree_path=f"{tree_path}/nestedTbl[{nested_index}]",
        )


def _docx_paragraph_tree_path_candidates(tree_path: str) -> list[str]:
    return [tree_path, f"{tree_path}/block[1]"]


def _translate_docx_notes_xml(
    xml_bytes: bytes,
    block_type: str,
    cursor: TranslationCursor,
    report: ExportReport,
) -> bytes:
    root = ET.fromstring(xml_bytes)
    note_tag = "footnote" if block_type == "footnote" else "endnote"
    for note in root.findall(f"w:{note_tag}", WORD_NS):
        if not _docx_text(note).strip():
            continue
        translated = cursor.consume({block_type})
        if translated is not None:
            _replace_docx_text(note, _translation_text(translated), report)
    return _serialize_docx_xml(root)


def _translate_docx_comments_xml(
    xml_bytes: bytes,
    cursor: TranslationCursor,
    report: ExportReport,
) -> bytes:
    root = ET.fromstring(xml_bytes)
    for comment in root.findall("w:comment", WORD_NS):
        if not _docx_text(comment).strip():
            continue
        translated = cursor.consume({"comment"})
        if translated is not None:
            _replace_docx_text(comment, _translation_text(translated), report)
    return _serialize_docx_xml(root)


def _translate_docx_paragraph_part_xml(
    xml_bytes: bytes,
    block_type: str,
    cursor: TranslationCursor,
    report: ExportReport,
) -> bytes:
    root = ET.fromstring(xml_bytes)
    for paragraph in root.findall(".//w:p", WORD_NS):
        if not _docx_text(paragraph).strip():
            continue
        translated = cursor.consume({block_type})
        if translated is not None:
            _replace_docx_text(paragraph, _translation_text(translated), report)
    return _serialize_docx_xml(root)


def _replace_docx_text(element: ET.Element, text: str, report: ExportReport) -> None:
    text_nodes = element.findall(".//w:t", WORD_NS)
    if not text_nodes:
        return

    repaired_text = _repair_docx_inline_placeholder_text(element, text)
    if repaired_text != text:
        text = repaired_text

    if INLINE_PLACEHOLDER_PATTERN.search(text):
        if _replace_docx_text_with_inline_placeholders(element, text, report):
            if len(text_nodes) > 1:
                report.warn_once(
                    "Some DOCX run-level formatting may be flattened inside translated text blocks."
                )
            return
        text = _docx_inline_placeholders_to_display_text(element, text)

    if _replace_docx_text_preserving_hidden_runs(element, text):
        return

    if _replace_docx_text_distributed_across_styled_runs(element, text):
        return

    if len(text_nodes) > 1:
        report.warn_once(
            "Some DOCX run-level formatting may be flattened inside translated text blocks."
        )

    replacement_node = _docx_replacement_text_node(element, text_nodes)
    replacement_node.text = text
    replacement_node.attrib[f"{{http://www.w3.org/XML/1998/namespace}}space"] = "preserve"
    for node in text_nodes[1:]:
        if node is not replacement_node:
            node.text = ""
    if replacement_node is not text_nodes[0]:
        text_nodes[0].text = ""


def _docx_replacement_text_node(
    element: ET.Element,
    text_nodes: list[ET.Element],
) -> ET.Element:
    run_text_nodes: list[tuple[ET.Element, ET.Element, str]] = []
    for run in element.findall(".//w:r", WORD_NS):
        run_text = "".join(node.text or "" for node in run.findall(".//w:t", WORD_NS))
        if run_text.strip():
            first_text_node = run.find(".//w:t", WORD_NS)
            if first_text_node is not None:
                run_text_nodes.append((run, first_text_node, run_text))

    if not run_text_nodes:
        return text_nodes[0]

    first_run, first_node, first_text = run_text_nodes[0]
    first_vert_align = _docx_run_vert_align(first_run)
    if (
        first_vert_align in {"superscript", "subscript"}
        and _docx_is_symbol_marker(first_text)
    ):
        for run, node, _run_text in run_text_nodes[1:]:
            if _docx_run_vert_align(run) is None:
                return node

    return first_node


def _docx_run_vert_align(run: ET.Element) -> str | None:
    vert_align = run.find("w:rPr/w:vertAlign", WORD_NS)
    if vert_align is None:
        return None
    return vert_align.attrib.get(_w_attr("val"))


def _docx_is_symbol_marker(text: str) -> bool:
    marker = text.strip()
    return bool(marker) and not any(char.isalnum() for char in marker)


def _replace_docx_text_with_inline_placeholders(
    element: ET.Element,
    text: str,
    report: ExportReport,
) -> bool:
    paragraph = _single_docx_paragraph_for_inline_rebuild(element)
    if paragraph is None or not _docx_paragraph_can_be_rebuilt(paragraph):
        return False

    token_items = _docx_inline_placeholder_item_map(element)
    if not token_items:
        return False

    segments = _split_docx_inline_placeholder_segments(text)
    tokens_in_text = [
        segment
        for segment in segments
        if INLINE_PLACEHOLDER_PATTERN.fullmatch(segment)
    ]
    if not segments or not any(token in token_items for token in tokens_in_text):
        return False
    if any(
        token not in token_items
        for token in tokens_in_text
    ):
        return False

    protected_run_ids = {
        id(item.element)
        for item in token_items.values()
        if _local_name(item.element.tag) == "r"
    }
    base_run_properties = _docx_base_run_properties(paragraph, protected_run_ids)
    preserved_paragraph_properties = paragraph.find("w:pPr", WORD_NS)
    rendered_nodes = _docx_render_inline_segments(
        segments=segments,
        token_items=token_items,
        base_run_properties=base_run_properties,
        report=report,
    )
    if rendered_nodes is None:
        return False

    for child in list(paragraph):
        paragraph.remove(child)
    if preserved_paragraph_properties is not None:
        paragraph.append(deepcopy(preserved_paragraph_properties))

    for node in rendered_nodes:
        paragraph.append(node)

    return True


def _single_docx_paragraph_for_inline_rebuild(element: ET.Element) -> ET.Element | None:
    if _local_name(element.tag) == "p":
        return element
    paragraphs = element.findall(".//w:p", WORD_NS)
    if len(paragraphs) == 1:
        return paragraphs[0]
    return None


def _docx_paragraph_can_be_rebuilt(paragraph: ET.Element) -> bool:
    for child in list(paragraph):
        child_name = _local_name(child.tag)
        if child_name not in {
            "pPr",
            "r",
            "hyperlink",
            "ins",
            "del",
            "moveFrom",
            "moveTo",
            "oMath",
            "oMathPara",
            "fldSimple",
        }:
            return False
    blocked_paths = (
        ".//w:bookmarkStart",
        ".//w:bookmarkEnd",
        ".//w:commentReference",
        ".//w:footnoteReference",
        ".//w:endnoteReference",
        ".//w:sectPr",
    )
    return not any(paragraph.find(path, WORD_NS) is not None for path in blocked_paths)


def _split_docx_inline_placeholder_segments(text: str) -> list[str]:
    return re.split(f"({INLINE_PLACEHOLDER_PATTERN.pattern})", text)


def _docx_base_run_properties(
    paragraph: ET.Element,
    protected_run_ids: set[int],
) -> ET.Element | None:
    for run in paragraph.findall("w:r", WORD_NS):
        if id(run) in protected_run_ids:
            continue
        if _docx_run_vert_align(run) is not None:
            continue
        run_properties = run.find("w:rPr", WORD_NS)
        return deepcopy(run_properties) if run_properties is not None else None

    first_run = paragraph.find("w:r", WORD_NS)
    if first_run is None:
        return None
    run_properties = first_run.find("w:rPr", WORD_NS)
    return deepcopy(run_properties) if run_properties is not None else None


def _new_docx_text_run(text: str, run_properties: ET.Element | None) -> ET.Element:
    run = ET.Element(_w_tag("r"))
    if run_properties is not None:
        run.append(deepcopy(run_properties))
    text_node = ET.SubElement(run, _w_tag("t"))
    text_node.text = text
    text_node.attrib["{http://www.w3.org/XML/1998/namespace}space"] = "preserve"
    return run


def _repair_docx_inline_placeholder_text(element: ET.Element, text: str) -> str:
    if INLINE_PLACEHOLDER_PATTERN.search(text):
        return text

    token_items = _docx_inline_placeholder_item_map(element)
    if not token_items:
        return text

    text = _repair_self_closing_inline_tokens(text, token_items)
    text = _wrap_single_container_translation(element, text, token_items)
    return text


def _repair_self_closing_inline_tokens(
    text: str,
    token_items: dict[str, DocxInlineTokenItem],
) -> str:
    repaired = text
    for item in token_items.values():
        if item.kind != "self" or not item.display_text.strip():
            continue
        if item.token in repaired:
            continue
        escaped_display = re.escape(item.display_text.strip())
        repaired, count = re.subn(escaped_display, item.token, repaired, count=1)
        if count:
            continue
    return repaired


def _wrap_single_container_translation(
    element: ET.Element,
    text: str,
    token_items: dict[str, DocxInlineTokenItem],
) -> str:
    if INLINE_PLACEHOLDER_PATTERN.search(text):
        return text

    paragraph = _single_docx_paragraph_for_inline_rebuild(element)
    if paragraph is None:
        return text

    non_property_children = [
        child
        for child in list(paragraph)
        if _local_name(child.tag) != "pPr"
    ]
    if len(non_property_children) != 1:
        return text

    only_child = non_property_children[0]
    only_child_id = id(only_child)
    starts = [
        item
        for item in token_items.values()
        if item.kind == "container_start" and id(item.element) == only_child_id
    ]
    if len(starts) != 1 or not starts[0].end_token:
        return text
    return f"{starts[0].token}{text}{starts[0].end_token}"


def _replace_docx_text_preserving_hidden_runs(
    element: ET.Element,
    text: str,
) -> bool:
    paragraph = _single_docx_paragraph_for_inline_rebuild(element)
    if paragraph is None:
        return False

    protected_runs = [
        run
        for run in paragraph.findall("w:r", WORD_NS)
        if _docx_run_should_remain_untouched(run)
    ]
    if not protected_runs:
        return False

    text_nodes = [
        node
        for run in paragraph.findall("w:r", WORD_NS)
        if run not in protected_runs
        for node in run.findall(".//w:t", WORD_NS)
    ]
    if not text_nodes:
        return False

    text_nodes[0].text = text
    text_nodes[0].attrib[f"{{http://www.w3.org/XML/1998/namespace}}space"] = "preserve"
    for node in text_nodes[1:]:
        node.text = ""
    return True


def _docx_run_should_remain_untouched(run: ET.Element) -> bool:
    return (
        run.find("w:rPr/w:vanish", WORD_NS) is not None
        or run.find("w:rPr/w:noProof", WORD_NS) is not None
    )


def _replace_docx_text_distributed_across_styled_runs(
    element: ET.Element,
    text: str,
) -> bool:
    paragraph = _single_docx_paragraph_for_inline_rebuild(element)
    if paragraph is None:
        return False

    styled_runs = [
        run
        for run in paragraph.findall("w:r", WORD_NS)
        if _docx_run_has_visible_character_style(run)
    ]
    if len(styled_runs) < 2:
        return False

    words = text.split()
    if len(words) < len(styled_runs):
        return False

    word_groups = _split_words_for_styled_runs(words, len(styled_runs))
    styled_run_ids = {id(run) for run in styled_runs}
    group_index = 0
    for run in paragraph.findall("w:r", WORD_NS):
        text_node = run.find(".//w:t", WORD_NS)
        if text_node is None:
            continue
        if id(run) in styled_run_ids:
            text_node.text = word_groups[group_index]
            text_node.attrib[f"{{http://www.w3.org/XML/1998/namespace}}space"] = "preserve"
            group_index += 1
        else:
            text_node.text = " " if group_index and group_index < len(styled_runs) else ""
    return True


def _docx_run_has_visible_character_style(run: ET.Element) -> bool:
    if _docx_run_should_remain_untouched(run):
        return False
    if not "".join(node.text or "" for node in run.findall(".//w:t", WORD_NS)).strip():
        return False
    run_properties = run.find("w:rPr", WORD_NS)
    if run_properties is None:
        return False
    return any(
        run_properties.find(path, WORD_NS) is not None
        for path in ("w:b", "w:i", "w:u", "w:color", "w:smallCaps")
    )


def _split_words_for_styled_runs(words: list[str], run_count: int) -> list[str]:
    groups: list[str] = []
    remaining_words = list(words)
    for index in range(run_count):
        remaining_slots = run_count - index
        group_size = max(1, len(remaining_words) // remaining_slots)
        groups.append(" ".join(remaining_words[:group_size]))
        remaining_words = remaining_words[group_size:]
    return groups


def _docx_render_inline_segments(
    segments: list[str],
    token_items: dict[str, DocxInlineTokenItem],
    base_run_properties: ET.Element | None,
    report: ExportReport,
) -> list[ET.Element] | None:
    rendered, next_index = _docx_render_inline_segments_until(
        segments=segments,
        token_items=token_items,
        base_run_properties=base_run_properties,
        report=report,
        start_index=0,
        stop_token=None,
    )
    if rendered is None or next_index != len(segments):
        return None
    return rendered


def _docx_render_inline_segments_until(
    segments: list[str],
    token_items: dict[str, DocxInlineTokenItem],
    base_run_properties: ET.Element | None,
    report: ExportReport,
    start_index: int,
    stop_token: str | None,
) -> tuple[list[ET.Element] | None, int]:
    rendered: list[ET.Element] = []
    index = start_index
    while index < len(segments):
        segment = segments[index]
        if not segment:
            index += 1
            continue

        if not INLINE_PLACEHOLDER_PATTERN.fullmatch(segment):
            rendered.append(_new_docx_text_run(segment, base_run_properties))
            index += 1
            continue

        if segment == stop_token:
            return rendered, index + 1

        item = token_items.get(segment)
        if item is None:
            return None, index
        if item.kind == "container_end":
            return None, index
        if item.kind == "self":
            rendered.append(deepcopy(item.element))
            index += 1
            continue
        if item.kind == "container_start" and item.end_token:
            inner_segments, next_index = _collect_inner_inline_segments(
                segments=segments,
                token_items=token_items,
                start_index=index + 1,
                stop_token=item.end_token,
            )
            if inner_segments is None:
                return None, index
            container = _docx_translated_container_copy(
                item=item,
                inner_text="".join(inner_segments),
                token_items=token_items,
                report=report,
            )
            rendered.append(container)
            index = next_index
            continue
        return None, index

    if stop_token is not None:
        return None, index
    return rendered, index


def _collect_inner_inline_segments(
    segments: list[str],
    token_items: dict[str, DocxInlineTokenItem],
    start_index: int,
    stop_token: str,
) -> tuple[list[str] | None, int]:
    inner: list[str] = []
    depth = 0
    index = start_index
    while index < len(segments):
        segment = segments[index]
        if INLINE_PLACEHOLDER_PATTERN.fullmatch(segment):
            item = token_items.get(segment)
            if item is None:
                return None, index
            if segment == stop_token and depth == 0:
                return inner, index + 1
            if item.kind == "container_start":
                depth += 1
            elif item.kind == "container_end":
                depth -= 1
                if depth < 0:
                    return None, index
        inner.append(segment)
        index += 1
    return None, index


def _docx_translated_container_copy(
    item: DocxInlineTokenItem,
    inner_text: str,
    token_items: dict[str, DocxInlineTokenItem],
    report: ExportReport,
) -> ET.Element:
    container = deepcopy(item.element)
    if INLINE_PLACEHOLDER_PATTERN.search(inner_text):
        inner_text = _docx_inline_placeholders_to_display_text_with_items(
            inner_text,
            token_items,
        )
    _replace_docx_container_text_nodes(container, inner_text)
    return container


def _replace_docx_container_text_nodes(element: ET.Element, text: str) -> None:
    text_nodes = element.findall(".//w:t", WORD_NS)
    if not text_nodes:
        return
    text_nodes[0].text = text
    text_nodes[0].attrib[f"{{http://www.w3.org/XML/1998/namespace}}space"] = "preserve"
    for node in text_nodes[1:]:
        node.text = ""
    _remove_empty_text_only_runs(element)


def _remove_empty_text_only_runs(element: ET.Element) -> None:
    for parent in list(element.iter()):
        for child in list(parent):
            if _local_name(child.tag) != "r":
                continue
            if _docx_run_has_content(child):
                continue
            parent.remove(child)


def _docx_run_has_content(run: ET.Element) -> bool:
    if any((node.text or "").strip() for node in run.findall(".//w:t", WORD_NS)):
        return True
    meaningful_children = {
        "drawing",
        "footnoteReference",
        "endnoteReference",
        "commentReference",
        "tab",
        "br",
        "oMath",
        "oMathPara",
    }
    return any(_local_name(node.tag) in meaningful_children for node in run.iter())


def _docx_inline_placeholder_item_map(element: ET.Element) -> dict[str, DocxInlineTokenItem]:
    token_items: dict[str, DocxInlineTokenItem] = {}
    _collect_docx_inline_placeholder_items(
        element=element,
        token_items=token_items,
        token_index=0,
    )
    return token_items


def _collect_docx_inline_placeholder_items(
    element: ET.Element,
    token_items: dict[str, DocxInlineTokenItem],
    token_index: int,
    vertical_alignment: str | None = None,
    current_run: ET.Element | None = None,
) -> int:
    tag_name = _local_name(element.tag)
    current_alignment = vertical_alignment
    if tag_name == "r":
        current_run = element
        current_alignment = _docx_run_vertical_alignment(element) or vertical_alignment

    if _docx_is_self_closing_inline_object(element):
        token_index += 1
        token = f"[[INLINE_{token_index:04d}]]"
        token_items[token] = DocxInlineTokenItem(
            token=token,
            kind="self",
            element=_docx_self_closing_placeholder_element(element, current_run),
            container_kind=_local_name(element.tag),
            display_text=_docx_text(element),
        )
        return token_index

    container_kind = _docx_paired_inline_container_kind(element)
    if container_kind:
        token_index += 1
        start_token = f"[[INLINE_{token_index:04d}]]"
        token_index += 1
        end_token = f"[[INLINE_{token_index:04d}]]"
        token_items[start_token] = DocxInlineTokenItem(
            token=start_token,
            kind="container_start",
            element=element,
            container_kind=container_kind,
            end_token=end_token,
        )
        for child in list(element):
            token_index = _collect_docx_inline_placeholder_items(
                element=child,
                token_items=token_items,
                token_index=token_index,
                vertical_alignment=current_alignment,
                current_run=current_run,
            )
        token_items[end_token] = DocxInlineTokenItem(
            token=end_token,
            kind="container_end",
            element=element,
            container_kind=container_kind,
            start_token=start_token,
        )
        return token_index

    if (
        tag_name == "t"
        and element.text
        and _should_protect_docx_inline_text(element.text, current_alignment)
    ):
        token_index += 1
        if current_run is not None:
            token = f"[[INLINE_{token_index:04d}]]"
            token_items[token] = DocxInlineTokenItem(
                token=token,
                kind="self",
                element=current_run,
                container_kind=current_alignment or "inline",
                display_text=element.text,
            )

    for child in list(element):
        token_index = _collect_docx_inline_placeholder_items(
            element=child,
            token_items=token_items,
            token_index=token_index,
            vertical_alignment=current_alignment,
            current_run=current_run,
        )
    return token_index


def _docx_self_closing_placeholder_element(
    element: ET.Element,
    current_run: ET.Element | None,
) -> ET.Element:
    tag_name = _local_name(element.tag)
    if tag_name in {
        "drawing",
        "footnoteReference",
        "endnoteReference",
        "commentReference",
        "instrText",
    } and current_run is not None:
        return current_run
    return element


def _docx_inline_placeholder_run_map(element: ET.Element) -> dict[str, ET.Element]:
    token_run_map: dict[str, ET.Element] = {}
    _collect_docx_inline_placeholder_runs(
        element=element,
        token_run_map=token_run_map,
        token_index=0,
    )
    return token_run_map


def _collect_docx_inline_placeholder_runs(
    element: ET.Element,
    token_run_map: dict[str, ET.Element],
    token_index: int,
    vertical_alignment: str | None = None,
    current_run: ET.Element | None = None,
) -> int:
    tag_name = _local_name(element.tag)
    current_alignment = vertical_alignment
    if tag_name == "r":
        current_run = element
        current_alignment = _docx_run_vertical_alignment(element) or vertical_alignment

    if (
        tag_name == "t"
        and element.text
        and _should_protect_docx_inline_text(element.text, current_alignment)
    ):
        token_index += 1
        if current_run is not None:
            token_run_map[f"[[INLINE_{token_index:04d}]]"] = current_run
    elif tag_name == "drawing":
        token_index += 1
        if current_run is not None:
            token_run_map[f"[[INLINE_{token_index:04d}]]"] = current_run

    for child in list(element):
        token_index = _collect_docx_inline_placeholder_runs(
            element=child,
            token_run_map=token_run_map,
            token_index=token_index,
            vertical_alignment=current_alignment,
            current_run=current_run,
        )
    return token_index


def _docx_inline_placeholders_to_display_text(element: ET.Element, text: str) -> str:
    _source_text, placeholders = _docx_text_with_inline_placeholders(element)
    token_items = {
        placeholder["token"]: DocxInlineTokenItem(
            token=placeholder["token"],
            kind=placeholder["kind"],
            element=element,
            display_text=placeholder.get("display_text", ""),
        )
        for placeholder in placeholders
    }
    return _docx_inline_placeholders_to_display_text_with_items(text, token_items)


def _docx_inline_placeholders_to_display_text_with_items(
    text: str,
    token_items: dict[str, DocxInlineTokenItem],
) -> str:
    rendered = text
    for token, item in token_items.items():
        replacement = item.display_text if item.kind == "self" else ""
        rendered = rendered.replace(token, replacement)
    return rendered


def _replace_docx_table_text(
    table: ET.Element,
    translated: str,
    report: ExportReport,
    structured_rows: list[list[str]] | None = None,
) -> None:
    translated_rows = (
        structured_rows
        if structured_rows is not None
        else [
            [cell.strip() for cell in row.split("\t")]
            for row in translated.splitlines()
            if row.strip()
        ]
    )
    table_rows = table.findall("w:tr", WORD_NS)
    if not translated_rows:
        return
    translated_rows = _pad_missing_blank_docx_rows(translated_rows, table_rows)
    translated_rows = _pad_omitted_blank_docx_cells(translated_rows, table_rows)

    row_shape_matches = len(translated_rows) == len(table_rows)
    if row_shape_matches:
        for row_index, table_row in enumerate(table_rows):
            table_cells = table_row.findall("w:tc", WORD_NS)
            if len(translated_rows[row_index]) != len(table_cells):
                row_shape_matches = False
                break

    if not row_shape_matches:
        report.warn(
            "A translated DOCX table did not preserve row/cell shape; "
            "placing the full translated table text in the first cell."
        )
        first_cell = _first_direct_docx_table_cell(table)
        if first_cell is not None:
            _replace_docx_table_cell_direct_text(first_cell, translated, report)
        return

    for row_index, table_row in enumerate(table_rows):
        for cell_index, table_cell in enumerate(table_row.findall("w:tc", WORD_NS)):
            _replace_docx_table_cell_direct_text(
                table_cell,
                translated_rows[row_index][cell_index],
                report,
            )


def _docx_table_has_direct_text(table: ET.Element) -> bool:
    return any(
        _docx_table_cell_text(cell).strip()
        for row in table.findall("w:tr", WORD_NS)
        for cell in row.findall("w:tc", WORD_NS)
    )


def _docx_nested_tables_for_writer(table: ET.Element) -> list[ET.Element]:
    nested_tables: list[ET.Element] = []
    for row in table.findall("w:tr", WORD_NS):
        for cell in row.findall("w:tc", WORD_NS):
            nested_tables.extend(_docx_direct_nested_tables(cell))
    return nested_tables


def _first_direct_docx_table_cell(table: ET.Element) -> ET.Element | None:
    for row in table.findall("w:tr", WORD_NS):
        cell = row.find("w:tc", WORD_NS)
        if cell is not None:
            return cell
    return None


def _replace_docx_table_cell_direct_text(
    cell: ET.Element,
    text: str,
    report: ExportReport,
) -> None:
    direct_paragraphs = _docx_direct_cell_paragraphs(cell)
    if not direct_paragraphs:
        if text.strip():
            paragraph = ET.Element(_w_tag("p"))
            cell.insert(0, paragraph)
            paragraph.append(_new_docx_text_run(text, None))
        return

    paragraph_texts = _split_docx_table_cell_text_for_direct_paragraphs(
        text,
        direct_paragraphs,
    )
    for paragraph, paragraph_text in zip(direct_paragraphs, paragraph_texts):
        _replace_docx_text(paragraph, paragraph_text, report)
    for paragraph in direct_paragraphs[len(paragraph_texts) :]:
        _replace_docx_text(paragraph, "", report)


def _split_docx_table_cell_text_for_direct_paragraphs(
    text: str,
    direct_paragraphs: list[ET.Element],
) -> list[str]:
    if len(direct_paragraphs) <= 1:
        return [text]

    parts = _split_translated_docx_cell_paragraph_text(text)
    if len(parts) != len(direct_paragraphs):
        return [text]

    return [
        _rebase_docx_cell_paragraph_inline_tokens(
            paragraph_text=part,
            paragraph_index=index,
            direct_paragraphs=direct_paragraphs,
        )
        for index, part in enumerate(parts)
    ]


def _split_translated_docx_cell_paragraph_text(text: str) -> list[str]:
    if "\n" in text:
        return [part.strip() for part in text.splitlines()]
    if "; " in text:
        return [part.strip() for part in text.split("; ")]
    return [text]


def _rebase_docx_cell_paragraph_inline_tokens(
    paragraph_text: str,
    paragraph_index: int,
    direct_paragraphs: list[ET.Element],
) -> str:
    global_token_index = 1
    for index, paragraph in enumerate(direct_paragraphs):
        local_tokens = list(_docx_inline_placeholder_item_map(paragraph).keys())
        if index != paragraph_index:
            global_token_index += len(local_tokens)
            continue
        rebased = paragraph_text
        for local_token in local_tokens:
            global_token = f"[[INLINE_{global_token_index:04d}]]"
            rebased = rebased.replace(global_token, local_token)
            global_token_index += 1
        return rebased
    return paragraph_text


def _replace_docx_contents_layout_table_text(table: ET.Element, translated: str) -> None:
    rows = _contents_layout_rows_from_text(translated)
    if not rows:
        return

    preserved_table_pr = table.find("w:tblPr", WORD_NS)
    table.clear()
    if preserved_table_pr is not None:
        table.append(deepcopy(preserved_table_pr))

    table_grid = ET.SubElement(table, _w_tag("tblGrid"))
    ET.SubElement(table_grid, _w_tag("gridCol"), {_w_attr("w"): "7200"})
    ET.SubElement(table_grid, _w_tag("gridCol"), {_w_attr("w"): "900"})

    for row_cells in rows:
        table_row = ET.SubElement(table, _w_tag("tr"))
        for cell_index, cell_text in enumerate(row_cells):
            table_cell = ET.SubElement(table_row, _w_tag("tc"))
            table_cell_pr = ET.SubElement(table_cell, _w_tag("tcPr"))
            width = "900" if cell_index == 1 else "7200"
            ET.SubElement(
                table_cell_pr,
                _w_tag("tcW"),
                {_w_attr("w"): width, _w_attr("type"): "dxa"},
            )
            paragraph = ET.SubElement(table_cell, _w_tag("p"))
            run = ET.SubElement(paragraph, _w_tag("r"))
            text_node = ET.SubElement(run, _w_tag("t"))
            text_node.text = cell_text
            text_node.attrib["{http://www.w3.org/XML/1998/namespace}space"] = "preserve"


def _contents_layout_rows_from_text(text: str) -> list[list[str]]:
    segments: list[str] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.splitlines():
        line = line.strip()
        if not line:
            continue
        for tab_part in line.split("\t"):
            tab_part = tab_part.strip()
            if not tab_part:
                continue
            if ";" in tab_part and _contents_entry_page_ref_count(tab_part) > 1:
                segments.extend(part.strip() for part in tab_part.split(";") if part.strip())
            else:
                segments.append(tab_part)

    rows: list[list[str]] = []
    for segment in segments:
        segment = re.sub(r"\s+", " ", segment).strip(" ;")
        if not segment:
            continue
        match = re.match(r"^(.*?)[,\s]+(\d{1,4}[A-Za-z]?)$", segment)
        if match:
            rows.append([match.group(1).strip(" ,"), match.group(2)])
        else:
            rows.append([segment, ""])
    return rows


def _contents_entry_page_ref_count(text: str) -> int:
    return len(re.findall(r"[,;\s]\s*\d{1,4}[A-Za-z]?\b", text))


def _serialize_docx_xml(root: ET.Element) -> bytes:
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml_bytes = _ensure_docx_root_namespace_declarations(xml_bytes, ("r",))
    return _ensure_mc_ignorable_namespace_declarations(xml_bytes)


def _ensure_docx_root_namespace_declarations(
    xml_bytes: bytes,
    prefixes: tuple[str, ...],
) -> bytes:
    xml_text = xml_bytes.decode("utf-8")
    match = re.search(r"<(?P<tag>[A-Za-z_][\w:.-]*)(?P<attrs>[^<>]*)>", xml_text)
    if not match:
        return xml_bytes

    root_tag = match.group(0)
    missing_declarations: list[str] = []
    for prefix in prefixes:
        if prefix not in WORD_NS:
            continue
        if f"xmlns:{prefix}=" in root_tag:
            continue
        missing_declarations.append(f' xmlns:{prefix}="{WORD_NS[prefix]}"')

    if not missing_declarations:
        return xml_bytes

    patched_root_tag = root_tag[:-1] + "".join(missing_declarations) + ">"
    return xml_text.replace(root_tag, patched_root_tag, 1).encode("utf-8")


def _ensure_mc_ignorable_namespace_declarations(xml_bytes: bytes) -> bytes:
    xml_text = xml_bytes.decode("utf-8")
    match = re.search(r"<(?P<tag>[\w:]+)(?P<attrs>[^<>]*\bmc:Ignorable=\"(?P<prefixes>[^\"]+)\"[^<>]*)>", xml_text)
    if not match:
        return xml_bytes

    root_tag = match.group(0)
    missing_declarations: list[str] = []
    for prefix in match.group("prefixes").split():
        if prefix not in WORD_NS:
            continue
        if f"xmlns:{prefix}=" in root_tag:
            continue
        missing_declarations.append(f' xmlns:{prefix}="{WORD_NS[prefix]}"')

    if not missing_declarations:
        return xml_bytes

    patched_root_tag = root_tag[:-1] + "".join(missing_declarations) + ">"
    return xml_text.replace(root_tag, patched_root_tag, 1).encode("utf-8")


def _w_tag(name: str) -> str:
    return f"{{{WORD_NS['w']}}}{name}"


def _w_attr(name: str) -> str:
    return f"{{{WORD_NS['w']}}}{name}"


def _pad_missing_blank_docx_rows(
    translated_rows: list[list[str]],
    table_rows: list[ET.Element],
) -> list[list[str]]:
    if len(translated_rows) >= len(table_rows):
        return translated_rows

    padded_rows = [*translated_rows]
    for table_row in table_rows[len(translated_rows):]:
        table_cells = table_row.findall("w:tc", WORD_NS)
        if any(_docx_table_cell_text(cell).strip() for cell in table_cells):
            return translated_rows
        padded_rows.append(["" for _ in table_cells])
    return padded_rows


def _pad_omitted_blank_docx_cells(
    translated_rows: list[list[str]],
    table_rows: list[ET.Element],
) -> list[list[str]]:
    if len(translated_rows) != len(table_rows):
        return translated_rows

    padded_rows: list[list[str]] = []
    changed = False
    for translated_row, table_row in zip(translated_rows, table_rows):
        table_cells = table_row.findall("w:tc", WORD_NS)
        expected_count = len(table_cells)
        if len(translated_row) == expected_count:
            padded_rows.append(translated_row)
            continue

        if len(translated_row) + 1 != expected_count:
            return translated_rows

        if table_cells and not _docx_table_cell_text(table_cells[0]).strip():
            padded_rows.append(["", *translated_row])
            changed = True
            continue

        if table_cells and not _docx_table_cell_text(table_cells[-1]).strip():
            padded_rows.append([*translated_row, ""])
            changed = True
            continue

        return translated_rows

    return padded_rows if changed else translated_rows


def _docx_related_parts(relationships: dict[str, str], block_type: str) -> list[str]:
    targets = {
        target
        for target in relationships.values()
        if posixpath.basename(target).lower().startswith(block_type)
    }
    return [
        posixpath.normpath(posixpath.join("word", target))
        for target in sorted(targets)
    ]


def _write_translated_epub(
    source_path: Path,
    output_path: Path,
    cursor: TranslationCursor,
    report: ExportReport,
) -> None:
    _register_xml_namespaces()
    replacements: dict[str, bytes] = {}

    with ZipFile(source_path) as epub:
        rootfile = _epub_rootfile(epub)
        opf_xml = epub.read(rootfile)
        spine_items = _epub_spine_items(opf_xml, rootfile)
        manifest_items = _epub_manifest_items(opf_xml, rootfile)
        replacements[rootfile] = _translate_epub_opf(opf_xml, cursor, report)

        item_paths = [
            *_epub_extra_item_paths(
                manifest_items=manifest_items,
                spine_items=spine_items,
                required_properties={"nav"},
            ),
            *spine_items,
        ]
        for item_path in item_paths:
            try:
                content = epub.read(item_path)
            except KeyError:
                continue
            replacements[item_path] = _translate_epub_content(content, cursor, report)

        _copy_zip_with_replacements(epub, output_path, replacements, report)


def _translate_epub_opf(
    xml_bytes: bytes,
    cursor: TranslationCursor,
    report: ExportReport,
) -> bytes:
    root = ET.fromstring(xml_bytes)
    for field_name in ("title", "description"):
        for node in root.findall(f".//opf:metadata/dc:{field_name}", EPUB_OPF_NS):
            if not (node.text or "").strip():
                continue
            translated = cursor.consume({f"metadata_{field_name}"})
            if translated is not None:
                node.text = _translation_text(translated)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _translate_epub_content(
    content: bytes,
    cursor: TranslationCursor,
    report: ExportReport,
) -> bytes:
    soup = BeautifulSoup(content, "html.parser")
    body = soup.body or soup
    block_tags = {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "blockquote",
        "figcaption",
        "table",
        "img",
        "aside",
        "pre",
        "code",
        "nav",
        "span",
    }

    for tag in body.find_all(block_tags):
        if _epub_is_hidden(tag):
            continue

        tag_name = (tag.name or "").lower()
        if tag_name == "span" and not _epub_is_page_break(tag):
            continue
        if _has_block_parent(tag, block_tags) and tag_name not in {"img", "span"}:
            continue

        if tag_name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            _consume_and_replace_epub_tag(tag, cursor, {"heading"}, report)
        elif tag_name == "p" and _html_text(tag):
            _consume_and_replace_epub_tag(tag, cursor, {"paragraph", "special"}, report)
        elif tag_name == "li" and _html_text(tag):
            _consume_and_replace_epub_tag(tag, cursor, {"list_item"}, report)
        elif tag_name == "blockquote" and _html_text(tag):
            _consume_and_replace_epub_tag(tag, cursor, {"quote"}, report)
        elif tag_name == "figcaption" and _html_text(tag):
            _consume_and_replace_epub_tag(tag, cursor, {"caption"}, report)
        elif tag_name == "table" and _html_text(tag):
            translated = cursor.consume({"table"})
            if translated is not None:
                _replace_epub_table_text(
                    tag,
                    _translation_text(translated),
                    report,
                    structured_rows=_translation_table_rows(translated),
                )
        elif tag_name in {"aside", "nav"} and _html_text(tag):
            semantic_type = _epub_semantic_block_type(tag, default=tag_name)
            if semantic_type != "toc":
                _consume_and_replace_epub_tag(tag, cursor, EPUB_TRANSLATABLE_TYPES, report)

    return str(soup).encode("utf-8")


def _consume_and_replace_epub_tag(
    tag,
    cursor: TranslationCursor,
    expected_types: set[str],
    report: ExportReport,
) -> None:
    translated = cursor.consume(expected_types)
    if translated is None:
        return
    _replace_epub_tag_text(tag, _translation_text(translated), report)


def _replace_epub_tag_text(tag, text: str, report: ExportReport) -> None:
    protected_math_nodes = [math.extract() for math in tag.find_all("math")]
    if protected_math_nodes and _epub_tag_was_formula_only(tag):
        report.warn_once(
            "EPUB MathML formulas were preserved while replacing surrounding text."
        )
        tag.clear()
        for math_node in protected_math_nodes:
            tag.append(math_node)
        return

    if tag.find(True) is not None:
        report.warn_once(
            "Some EPUB inline markup may be flattened inside translated text blocks."
        )
    tag.clear()
    tag.append(text)
    if protected_math_nodes:
        report.warn_once(
            "EPUB MathML formulas were preserved while replacing surrounding text."
        )
        for math_node in protected_math_nodes:
            tag.append(" ")
            tag.append(math_node)


def _replace_epub_table_text(
    tag,
    translated: str,
    report: ExportReport,
    structured_rows: list[list[str]] | None = None,
) -> None:
    translated_rows = (
        structured_rows
        if structured_rows is not None
        else [
            [cell.strip() for cell in row.split("\t")]
            for row in translated.splitlines()
            if row.strip()
        ]
    )
    table_rows = tag.find_all("tr")
    if not translated_rows:
        return
    translated_rows = _pad_missing_blank_epub_rows(translated_rows, table_rows)

    row_shape_matches = len(translated_rows) == len(table_rows)
    if row_shape_matches:
        for row_index, table_row in enumerate(table_rows):
            cells = table_row.find_all(["th", "td"])
            if len(translated_rows[row_index]) != len(cells):
                row_shape_matches = False
                break

    if not row_shape_matches:
        report.warn(
            "A translated EPUB table did not preserve row/cell shape; "
            "placing the full translated table text in the first cell."
        )
        first_cell = tag.find(["th", "td"])
        if first_cell is not None:
            _replace_epub_tag_text(first_cell, translated, report)
        return

    for row_index, table_row in enumerate(table_rows):
        for cell_index, cell in enumerate(table_row.find_all(["th", "td"])):
            _replace_epub_tag_text(cell, translated_rows[row_index][cell_index], report)


def _pad_missing_blank_epub_rows(
    translated_rows: list[list[str]],
    table_rows: list,
) -> list[list[str]]:
    if len(translated_rows) >= len(table_rows):
        return translated_rows

    padded_rows = [*translated_rows]
    for table_row in table_rows[len(translated_rows):]:
        cells = table_row.find_all(["th", "td"])
        if any(_html_text(cell).strip() for cell in cells):
            return translated_rows
        padded_rows.append(["" for _ in cells])
    return padded_rows


def _epub_tag_was_formula_only(tag) -> bool:
    text_without_math = tag.get_text(" ", strip=True)
    return not text_without_math


def _copy_zip_with_replacements(
    source_zip: ZipFile,
    output_path: Path,
    replacements: dict[str, bytes],
    report: ExportReport,
) -> None:
    with ZipFile(output_path, "w") as output_zip:
        for item in source_zip.infolist():
            data = replacements.get(item.filename)
            if data is None:
                data = source_zip.read(item.filename)
            else:
                report.files_modified.append(item.filename)
            output_zip.writestr(item, data)


def _register_xml_namespaces() -> None:
    for prefix in (
        "w",
        "r",
        "a",
        "wp",
        "pic",
        "mc",
        "m",
        "o",
        "v",
        "w10",
        "w14",
        "w15",
        "w16cex",
        "wp14",
        "wpg",
        "wps",
    ):
        if prefix in WORD_NS:
            ET.register_namespace(prefix, WORD_NS[prefix])
    ET.register_namespace("", EPUB_OPF_NS["opf"])
    ET.register_namespace("dc", EPUB_OPF_NS["dc"])
