from __future__ import annotations

from dataclasses import replace
from html import unescape
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from bs4 import BeautifulSoup

from document_model import DocumentBlock, ParsedDocument, parsed_document_from_text


WORD_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "o": "urn:schemas-microsoft-com:office:office",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16cex": "http://schemas.microsoft.com/office/word/2018/wordml/cex",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}

EPUB_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
EPUB_OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
SUPERSCRIPT_CHARS = str.maketrans(
    {
        "0": "\u2070",
        "1": "\u00b9",
        "2": "\u00b2",
        "3": "\u00b3",
        "4": "\u2074",
        "5": "\u2075",
        "6": "\u2076",
        "7": "\u2077",
        "8": "\u2078",
        "9": "\u2079",
        "+": "\u207a",
        "-": "\u207b",
        "=": "\u207c",
        "(": "\u207d",
        ")": "\u207e",
    }
)
SUBSCRIPT_CHARS = str.maketrans(
    {
        "0": "\u2080",
        "1": "\u2081",
        "2": "\u2082",
        "3": "\u2083",
        "4": "\u2084",
        "5": "\u2085",
        "6": "\u2086",
        "7": "\u2087",
        "8": "\u2088",
        "9": "\u2089",
        "+": "\u208a",
        "-": "\u208b",
        "=": "\u208c",
        "(": "\u208d",
        ")": "\u208e",
    }
)
SUPERSCRIPT_DIGITS = "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"
TOC_TITLE_HINTS = (
    "contents",
    "table of contents",
    "chapter contents",
    "índice",
    "indice",
    "tabla de contenido",
    "sommaire",
    "table des matières",
    "inhaltsverzeichnis",
    "sommario",
    "inhoudsopgave",
    "विषय-सूची",
    "विषय सूची",
    "अनुक्रमणिका",
    "सामग्री",
    "目录",
    "目錄",
    "目次",
    "목차",
    "فهرست",
    "المحتويات",
)


def load_document(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return load_txt(path)
    if suffix == ".docx":
        return load_docx(path)
    if suffix == ".epub":
        return load_epub(path)

    raise ValueError(
        f"Unsupported input format '{suffix}'. Supported formats: .txt, .docx, .epub"
    )


def load_txt(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8").strip()
    return parsed_document_from_text(text, source_path=path, source_format="txt")


def load_docx(path: Path) -> ParsedDocument:
    with ZipFile(path) as docx:
        document_xml = docx.read("word/document.xml")
        relationships = _docx_relationships(docx)
        root = ET.fromstring(document_xml)
        body = root.find("w:body", WORD_NS)
        if body is None:
            return ParsedDocument(str(path), "docx", [])

        blocks = _docx_container_blocks(
            body,
            relationships,
            block_offset=0,
            context_path="body",
        )

        blocks.extend(_docx_note_blocks(docx, "word/footnotes.xml", "footnote", len(blocks)))
        blocks.extend(_docx_note_blocks(docx, "word/endnotes.xml", "endnote", len(blocks)))
        blocks.extend(_docx_comment_blocks(docx, len(blocks)))
        blocks.extend(_docx_header_footer_blocks(docx, relationships, "header", len(blocks)))
        blocks.extend(_docx_header_footer_blocks(docx, relationships, "footer", len(blocks)))

    return ParsedDocument(str(path), "docx", blocks)


def load_epub(path: Path) -> ParsedDocument:
    with ZipFile(path) as epub:
        rootfile = _epub_rootfile(epub)
        opf_xml = epub.read(rootfile)
        spine_items = _epub_spine_items(opf_xml, rootfile)
        manifest_items = _epub_manifest_items(opf_xml, rootfile)
        epub_metadata = _epub_package_metadata(opf_xml)
        cover_item_ids = _epub_cover_item_ids(opf_xml)
        blocks: list[DocumentBlock] = []

        blocks.extend(_epub_metadata_blocks(epub_metadata, len(blocks)))
        blocks.extend(_epub_cover_blocks(manifest_items, cover_item_ids, len(blocks)))

        extra_item_paths = _epub_extra_item_paths(
            manifest_items=manifest_items,
            spine_items=spine_items,
            required_properties={"nav"},
        )
        for item_path in extra_item_paths:
            try:
                content = epub.read(item_path)
            except KeyError:
                continue
            blocks.extend(_epub_content_blocks(content, item_path, len(blocks)))

        for item_path in spine_items:
            try:
                content = epub.read(item_path)
            except KeyError:
                continue
            blocks.extend(_epub_content_blocks(content, item_path, len(blocks)))

    return ParsedDocument(str(path), "epub", blocks)


def _docx_relationships(docx: ZipFile) -> dict[str, str]:
    rels_path = "word/_rels/document.xml.rels"
    if rels_path not in docx.namelist():
        return {}

    rels_root = ET.fromstring(docx.read(rels_path))
    relationships: dict[str, str] = {}
    for relationship in rels_root.findall("rel:Relationship", WORD_NS):
        relationship_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        if relationship_id and target:
            relationships[relationship_id] = target
    return relationships


def _docx_note_blocks(
    docx: ZipFile,
    part_path: str,
    block_type: str,
    block_offset: int,
) -> list[DocumentBlock]:
    if part_path not in docx.namelist():
        return []

    root = ET.fromstring(docx.read(part_path))
    blocks: list[DocumentBlock] = []
    note_tag = "footnote" if block_type == "footnote" else "endnote"
    for note in root.findall(f"w:{note_tag}", WORD_NS):
        note_id = note.attrib.get(f"{{{WORD_NS['w']}}}id")
        text = _docx_text(note).strip()
        inline_source_text, inline_placeholders = _docx_text_with_inline_placeholders(note)
        if not text:
            continue
        metadata: dict[str, object] = {"note_id": note_id, "source_part": part_path}
        if inline_placeholders:
            metadata["inline_source_text"] = inline_source_text
            metadata["inline_placeholders"] = inline_placeholders
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type=block_type,
                text=text,
                translate=True,
                metadata=metadata,
            )
        )
    return blocks


def _docx_comment_blocks(docx: ZipFile, block_offset: int) -> list[DocumentBlock]:
    part_path = "word/comments.xml"
    if part_path not in docx.namelist():
        return []

    root = ET.fromstring(docx.read(part_path))
    blocks: list[DocumentBlock] = []
    for comment in root.findall("w:comment", WORD_NS):
        comment_id = comment.attrib.get(f"{{{WORD_NS['w']}}}id")
        text = _docx_text(comment).strip()
        inline_source_text, inline_placeholders = _docx_text_with_inline_placeholders(comment)
        if not text:
            continue
        metadata: dict[str, object] = {"comment_id": comment_id, "source_part": part_path}
        if inline_placeholders:
            metadata["inline_source_text"] = inline_source_text
            metadata["inline_placeholders"] = inline_placeholders
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type="comment",
                text=text,
                translate=True,
                metadata=metadata,
            )
        )
    return blocks


def _docx_header_footer_blocks(
    docx: ZipFile,
    relationships: dict[str, str],
    block_type: str,
    block_offset: int,
) -> list[DocumentBlock]:
    prefix = f"{block_type}"
    targets = {
        target
        for target in relationships.values()
        if posixpath.basename(target).lower().startswith(prefix)
    }
    blocks: list[DocumentBlock] = []
    for target in sorted(targets):
        part_path = posixpath.normpath(posixpath.join("word", target))
        if part_path not in docx.namelist():
            continue
        root = ET.fromstring(docx.read(part_path))
        for paragraph in root.findall(".//w:p", WORD_NS):
            text = _docx_text(paragraph).strip()
            inline_source_text, inline_placeholders = _docx_text_with_inline_placeholders(paragraph)
            if not text:
                continue
            metadata: dict[str, object] = {
                "source_part": part_path,
                "style": _docx_paragraph_style(paragraph),
            }
            if inline_placeholders:
                metadata["inline_source_text"] = inline_source_text
                metadata["inline_placeholders"] = inline_placeholders
            blocks.append(
                DocumentBlock(
                    block_id=_block_id(block_offset + len(blocks)),
                    type=block_type,
                    text=text,
                    translate=True,
                    metadata=metadata,
                )
            )
    return blocks


def _docx_container_blocks(
    container: ET.Element,
    relationships: dict[str, str],
    block_offset: int,
    context_path: str,
    content_controls: tuple[dict[str, str | None], ...] = (),
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    child_counts: dict[str, int] = {}

    for child in list(container):
        tag_name = _local_name(child.tag)
        child_counts[tag_name] = child_counts.get(tag_name, 0) + 1
        child_path = f"{context_path}/{tag_name}[{child_counts[tag_name]}]"

        if tag_name == "p":
            new_blocks = _docx_paragraph_blocks(
                child,
                relationships,
                block_offset + len(blocks),
            )
            blocks.extend(
                _docx_blocks_with_context(
                    new_blocks,
                    tree_path=child_path,
                    content_controls=content_controls,
                )
            )
        elif tag_name == "tbl":
            new_blocks = _docx_table_blocks(
                child,
                block_offset + len(blocks),
                tree_path=child_path,
                content_controls=content_controls,
            )
            blocks.extend(new_blocks)
        elif tag_name == "sdt":
            content_control = _docx_content_control_metadata(child)
            sdt_content = child.find("w:sdtContent", WORD_NS)
            if sdt_content is None:
                continue
            blocks.extend(
                _docx_container_blocks(
                    sdt_content,
                    relationships,
                    block_offset + len(blocks),
                    context_path=f"{child_path}/sdtContent",
                    content_controls=(*content_controls, content_control),
                )
            )

    return blocks


def _docx_blocks_with_context(
    blocks: list[DocumentBlock],
    tree_path: str,
    content_controls: tuple[dict[str, str | None], ...] = (),
) -> list[DocumentBlock]:
    contextualized_blocks: list[DocumentBlock] = []
    for index, block in enumerate(blocks, start=1):
        metadata = {
            **block.metadata,
            "tree_path": tree_path if len(blocks) == 1 else f"{tree_path}/block[{index}]",
        }
        if content_controls:
            metadata["content_controls"] = list(content_controls)
            metadata["content_control"] = content_controls[-1]
        contextualized_blocks.append(replace(block, metadata=metadata))
    return contextualized_blocks


def _docx_paragraph_blocks(
    paragraph: ET.Element,
    relationships: dict[str, str],
    block_offset: int,
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    text = _docx_text(paragraph).strip()
    inline_source_text, inline_placeholders = _docx_text_with_inline_placeholders(paragraph)
    style = _docx_paragraph_style(paragraph)
    list_item = paragraph.find(".//w:numPr", WORD_NS) is not None
    has_equation = (
        paragraph.find(".//m:oMath", WORD_NS) is not None
        or paragraph.find(".//m:oMathPara", WORD_NS) is not None
    )
    image_infos = _docx_image_infos(paragraph, relationships)
    image_targets = [image_info["target"] for image_info in image_infos if image_info.get("target")]
    has_page_break = any(
        br.attrib.get(f"{{{WORD_NS['w']}}}type") == "page"
        for br in paragraph.findall(".//w:br", WORD_NS)
    )
    has_section_break = paragraph.find(".//w:sectPr", WORD_NS) is not None

    if text:
        block_type = "list_item" if list_item else "paragraph"
        heading_level = _heading_level(style)
        if heading_level is not None:
            block_type = "heading"
        style_block_type = _style_block_type(style)
        if style_block_type and block_type == "paragraph":
            block_type = style_block_type
        preserve_exact = _is_scene_break(text)
        if preserve_exact:
            block_type = "special"

        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type=block_type,
                text=text,
                translate=True,
                level=heading_level,
                metadata={
                    "style": style,
                    "preserve_exact": preserve_exact,
                    "list": _docx_list_metadata(paragraph),
                    "formatting": _docx_formatting_flags(paragraph),
                    "hyperlinks": _docx_hyperlinks(paragraph, relationships),
                    "contains_equation": has_equation,
                    "contains_images": bool(image_targets),
                    "image_targets": image_targets,
                    "image_info": image_infos,
                    "contains_text_box": paragraph.find(".//w:txbxContent", WORD_NS) is not None,
                    "contains_revisions": _docx_contains_revisions(paragraph),
                    "contains_hidden_text": paragraph.find(".//w:vanish", WORD_NS) is not None,
                    "contains_form_control": _docx_contains_form_control(paragraph),
                    "contains_checkbox": _docx_contains_checkbox(paragraph),
                    "comment_ids": _docx_comment_refs(paragraph),
                    "bookmarks": _docx_bookmarks(paragraph),
                    "field_codes": _docx_field_codes(paragraph),
                    "contains_section_break": has_section_break,
                    **(
                        {
                            "inline_source_text": inline_source_text,
                            "inline_placeholders": inline_placeholders,
                        }
                        if inline_placeholders
                        else {}
                    ),
                },
            )
        )
    elif has_equation:
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type="equation",
                text="",
                translate=False,
                metadata={"style": style},
            )
        )

    for image_info in image_infos:
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type="image",
                text="",
                translate=False,
                metadata={"style": style, **image_info},
            )
        )

    if has_page_break:
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type="page_break",
                text="",
                translate=False,
                metadata={"style": style},
            )
        )

    if has_section_break:
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type="section_break",
                text="",
                translate=False,
                metadata={"style": style},
            )
        )

    return blocks


def _docx_sdt_blocks(
    sdt: ET.Element,
    relationships: dict[str, str],
    block_offset: int,
) -> list[DocumentBlock]:
    content_control = _docx_content_control_metadata(sdt)
    sdt_content = sdt.find("w:sdtContent", WORD_NS)
    if sdt_content is None:
        return []

    return _docx_container_blocks(
        sdt_content,
        relationships,
        block_offset=block_offset,
        context_path="sdt/sdtContent",
        content_controls=(content_control,),
    )


def _docx_content_control_metadata(sdt: ET.Element) -> dict[str, str | None]:
    properties = sdt.find("w:sdtPr", WORD_NS)
    if properties is None:
        return {"alias": None, "tag": None, "id": None}

    alias = properties.find("w:alias", WORD_NS)
    tag = properties.find("w:tag", WORD_NS)
    control_id = properties.find("w:id", WORD_NS)
    return {
        "alias": alias.attrib.get(f"{{{WORD_NS['w']}}}val") if alias is not None else None,
        "tag": tag.attrib.get(f"{{{WORD_NS['w']}}}val") if tag is not None else None,
        "id": control_id.attrib.get(f"{{{WORD_NS['w']}}}val") if control_id is not None else None,
    }


def _docx_table_blocks(
    table: ET.Element,
    block_offset: int,
    nesting_level: int = 0,
    table_path: str = "0",
    tree_path: str | None = None,
    content_controls: tuple[dict[str, str | None], ...] = (),
) -> list[DocumentBlock]:
    blocks = [
        _docx_table_block(
            table=table,
            block_offset=block_offset,
            nesting_level=nesting_level,
            table_path=table_path,
            tree_path=tree_path,
            content_controls=content_controls,
        )
    ]
    for nested_index, nested_table in enumerate(_docx_nested_tables_in_table(table), start=1):
        blocks.extend(
            _docx_table_blocks(
                table=nested_table,
                block_offset=block_offset + len(blocks),
                nesting_level=nesting_level + 1,
                table_path=f"{table_path}.{nested_index}",
                tree_path=f"{tree_path}/nestedTbl[{nested_index}]"
                if tree_path
                else None,
                content_controls=content_controls,
            )
        )
    return blocks


def _docx_table_block(
    table: ET.Element,
    block_offset: int,
    nesting_level: int = 0,
    table_path: str = "0",
    tree_path: str | None = None,
    content_controls: tuple[dict[str, str | None], ...] = (),
) -> DocumentBlock:
    rows: list[list[str]] = []
    translation_rows: list[list[str]] = []
    row_metadata: list[dict[str, object]] = []
    has_merged_cells = False
    has_header_rows = False
    contains_equations = False
    nested_table_count = 0
    for row in table.findall("w:tr", WORD_NS):
        cells: list[str] = []
        translation_cells: list[str] = []
        cell_metadata: list[dict[str, object]] = []
        row_is_header = row.find("w:trPr/w:tblHeader", WORD_NS) is not None
        has_header_rows = has_header_rows or row_is_header
        for cell in row.findall("w:tc", WORD_NS):
            cell_text = _docx_table_cell_text(cell)
            cell_translation_text, inline_placeholders = _docx_table_cell_translation_text(cell)
            cell_nested_tables = _docx_direct_nested_tables(cell)
            cell_content_controls = _docx_direct_content_controls(cell)
            nested_table_count += len(cell_nested_tables)
            grid_span = cell.find("w:tcPr/w:gridSpan", WORD_NS)
            vertical_merge = cell.find("w:tcPr/w:vMerge", WORD_NS)
            cell_has_equation = (
                cell.find(".//m:oMath", WORD_NS) is not None
                or cell.find(".//m:oMathPara", WORD_NS) is not None
            )
            contains_equations = contains_equations or cell_has_equation
            has_merged_cells = has_merged_cells or grid_span is not None or vertical_merge is not None
            cells.append(cell_text)
            translation_cells.append(cell_translation_text)
            cell_metadata.append(
                {
                    "grid_span": grid_span.attrib.get(f"{{{WORD_NS['w']}}}val")
                    if grid_span is not None
                    else None,
                    "vertical_merge": vertical_merge.attrib.get(f"{{{WORD_NS['w']}}}val", "continue")
                    if vertical_merge is not None
                    else None,
                    "contains_equation": cell_has_equation,
                    "contains_form_control": _docx_contains_form_control(cell),
                    "contains_checkbox": _docx_contains_checkbox(cell),
                    "nested_table_count": len(cell_nested_tables),
                    **(
                        {"content_controls": cell_content_controls}
                        if cell_content_controls
                        else {}
                    ),
                    "formatting": _docx_formatting_flags(cell),
                    **(
                        {"inline_placeholders": inline_placeholders}
                        if inline_placeholders
                        else {}
                    ),
                }
            )
        rows.append(cells)
        translation_rows.append(translation_cells)
        row_metadata.append({"is_header": row_is_header, "cells": cell_metadata})

    rendered_rows = ["\t".join(cell for cell in row) for row in rows]
    role_analysis = _docx_table_role_analysis(
        table=table,
        rows=rows,
        block_offset=block_offset,
    )
    return DocumentBlock(
        block_id=_block_id(block_offset),
        type="table",
        text="\n".join(rendered_rows).strip(),
        translate=True,
        metadata={
            "rows": rows,
            **({"translation_rows": translation_rows} if translation_rows != rows else {}),
            "row_metadata": row_metadata,
            "has_merged_cells": has_merged_cells,
            "has_header_rows": has_header_rows,
            "contains_equations": contains_equations,
            "has_nested_tables": nested_table_count > 0,
            "nested_table_count": nested_table_count,
            "nesting_level": nesting_level,
            "table_path": table_path,
            **({"tree_path": tree_path} if tree_path else {}),
            **(
                {
                    "content_controls": list(content_controls),
                    "content_control": content_controls[-1],
                }
                if content_controls
                else {}
            ),
            **role_analysis,
        },
    )


def _docx_table_role_analysis(
    table: ET.Element,
    rows: list[list[str]],
    block_offset: int,
) -> dict[str, object]:
    flattened = " ".join(cell for row in rows for cell in row).strip()
    normalized = _normalize_inline_text(flattened).casefold()
    first_cell = rows[0][0] if rows and rows[0] else ""
    row_count = len(rows)
    max_cell_count = max((len(row) for row in rows), default=0)
    page_ref_count = _docx_toc_page_ref_count(flattened)
    semicolon_count = len(re.findall(r"[;\uff1b\u061b]", flattened))
    field_instructions = _docx_field_instruction_texts(table)
    toc_field_count = sum(
        1
        for instruction in field_instructions
        if re.search(r"\b(?:TOC|TC)\b", instruction, re.IGNORECASE)
    )
    toc_style_count = sum(
        1
        for paragraph in table.findall(".//w:p", WORD_NS)
        if _docx_is_toc_style(_docx_paragraph_style(paragraph))
    )
    toc_hyperlink_count = sum(
        1
        for hyperlink in table.findall(".//w:hyperlink", WORD_NS)
        if _docx_is_toc_hyperlink(hyperlink)
    )
    dense_page_ref_cells = [
        cell
        for row in rows
        for cell in row
        if _docx_toc_page_ref_count(cell) >= 3
    ]
    title_hint = _docx_has_toc_title_hint(first_cell) or _docx_has_toc_title_hint(flattened)
    near_beginning = block_offset <= 10
    compact_layout = row_count <= 4 and max_cell_count <= 3
    numeric_cell_ratio = _numeric_cell_ratio(rows)

    score = 0
    signals: list[str] = []

    if toc_field_count:
        score += 7
        signals.append("toc_field")
    if toc_style_count:
        score += 6
        signals.append("toc_paragraph_style")
    if toc_hyperlink_count >= 3:
        score += 3
        signals.append("toc_hyperlinks")
    if title_hint:
        score += 3
        signals.append("toc_title_hint")
    if near_beginning:
        score += 2
        signals.append("near_document_start")
    if compact_layout:
        score += 2
        signals.append("compact_layout_table")
    if page_ref_count >= 3:
        score += 3
        signals.append("page_reference_entries")
    if page_ref_count >= 8:
        score += 2
        signals.append("dense_page_reference_entries")
    if semicolon_count >= 3:
        score += 2
        signals.append("semicolon_separated_entries")
    if dense_page_ref_cells:
        score += 2
        signals.append("dense_page_reference_cells")
    if numeric_cell_ratio >= 0.6 and page_ref_count < 3:
        score -= 4
        signals.append("numeric_data_grid")
    if row_count >= 5 and max_cell_count >= 3 and page_ref_count < 3:
        score -= 3
        signals.append("regular_data_grid_shape")

    semantic_toc = bool(toc_field_count or toc_style_count)
    if semantic_toc or score >= 10:
        role = "toc_layout"
        confidence = "high"
    elif score >= 7 and page_ref_count >= 3:
        role = "possible_toc_layout"
        confidence = "medium"
    else:
        role = "data"
        confidence = "high" if score <= 2 else "low"

    return {
        "table_role": role,
        "table_role_confidence": confidence,
        "table_role_score": score,
        "table_role_signals": signals,
        "is_layout_table": role == "toc_layout",
    }


def _docx_field_instruction_texts(element: ET.Element) -> list[str]:
    instructions = [
        node.text.strip()
        for node in element.findall(".//w:instrText", WORD_NS)
        if node.text and node.text.strip()
    ]
    instructions.extend(
        node.attrib.get(f"{{{WORD_NS['w']}}}instr", "").strip()
        for node in element.findall(".//w:fldSimple", WORD_NS)
        if node.attrib.get(f"{{{WORD_NS['w']}}}instr", "").strip()
    )
    return instructions


def _docx_is_toc_style(style: str | None) -> bool:
    if not style:
        return False
    normalized = style.replace(" ", "").replace("_", "").replace("-", "").casefold()
    return bool(re.fullmatch(r"toc\d+", normalized))


def _docx_is_toc_hyperlink(hyperlink: ET.Element) -> bool:
    anchor = hyperlink.attrib.get(f"{{{WORD_NS['w']}}}anchor", "")
    return anchor.casefold().startswith("_toc")


def _docx_has_toc_title_hint(text: str) -> bool:
    normalized = _normalize_inline_text(text).casefold()
    compact = re.sub(r"[\s\-_]+", "", normalized)
    for hint in TOC_TITLE_HINTS:
        hint_normalized = hint.casefold()
        hint_compact = re.sub(r"[\s\-_]+", "", hint_normalized)
        if hint_normalized in normalized or hint_compact in compact:
            return True
    return False


def _numeric_cell_ratio(rows: list[list[str]]) -> float:
    cells = [cell.strip() for row in rows for cell in row if cell.strip()]
    if not cells:
        return 0.0
    numeric_cells = [cell for cell in cells if _looks_numeric(cell)]
    return len(numeric_cells) / len(cells)


def _docx_toc_page_ref_count(text: str) -> int:
    return len(
        re.findall(
            r"[,;\uff0c\uff1b\u060c\u061b]\s*\d{1,4}[A-Za-z]?\b",
            text,
        )
    )


def _docx_nested_tables_in_table(table: ET.Element) -> list[ET.Element]:
    nested_tables: list[ET.Element] = []
    for row in table.findall("w:tr", WORD_NS):
        for cell in row.findall("w:tc", WORD_NS):
            nested_tables.extend(_docx_direct_nested_tables(cell))
    return nested_tables


def _docx_direct_nested_tables(cell: ET.Element) -> list[ET.Element]:
    tables: list[ET.Element] = []
    _collect_docx_direct_nested_tables(cell, tables)
    return tables


def _docx_direct_content_controls(cell: ET.Element) -> list[dict[str, str | None]]:
    controls: list[dict[str, str | None]] = []
    for child in list(cell):
        if _local_name(child.tag) == "sdt":
            controls.append(_docx_content_control_metadata(child))
    return controls


def _collect_docx_direct_nested_tables(
    element: ET.Element,
    tables: list[ET.Element],
) -> None:
    for child in list(element):
        tag_name = _local_name(child.tag)
        if tag_name == "tbl":
            tables.append(child)
        elif tag_name == "sdt":
            sdt_content = child.find("w:sdtContent", WORD_NS)
            if sdt_content is not None:
                _collect_docx_direct_nested_tables(sdt_content, tables)


def _docx_direct_cell_paragraphs(cell: ET.Element) -> list[ET.Element]:
    paragraphs: list[ET.Element] = []
    _collect_docx_direct_cell_paragraphs(cell, paragraphs)
    return paragraphs


def _collect_docx_direct_cell_paragraphs(
    element: ET.Element,
    paragraphs: list[ET.Element],
) -> None:
    for child in list(element):
        tag_name = _local_name(child.tag)
        if tag_name == "p":
            paragraphs.append(child)
        elif tag_name == "tbl":
            continue
        elif tag_name == "sdt":
            sdt_content = child.find("w:sdtContent", WORD_NS)
            if sdt_content is not None:
                _collect_docx_direct_cell_paragraphs(sdt_content, paragraphs)


def _docx_text(element: ET.Element) -> str:
    parts: list[str] = []
    _append_docx_text_parts(element, parts)
    return _normalize_inline_text("".join(parts))


def _docx_text_with_inline_placeholders(
    element: ET.Element,
) -> tuple[str, list[dict[str, str]]]:
    parts: list[str] = []
    placeholders: list[dict[str, str]] = []
    _append_docx_translation_text_parts(
        element=element,
        parts=parts,
        placeholders=placeholders,
        token_index=0,
    )
    return _normalize_inline_text("".join(parts)), placeholders


def _append_docx_translation_text_parts(
    element: ET.Element,
    parts: list[str],
    placeholders: list[dict[str, str]],
    token_index: int,
    vertical_alignment: str | None = None,
) -> int:
    tag_name = _local_name(element.tag)
    current_alignment = vertical_alignment
    if tag_name == "r":
        current_alignment = _docx_run_vertical_alignment(element) or vertical_alignment

    if _docx_is_self_closing_inline_object(element):
        token_index += 1
        token = f"[[INLINE_{token_index:04d}]]"
        parts.append(token)
        placeholders.append(
            {
                "token": token,
                "text": _docx_text(element),
                "display_text": "",
                "kind": _docx_self_closing_inline_kind(element),
            }
        )
        return token_index

    container_kind = _docx_paired_inline_container_kind(element)
    if container_kind:
        token_index += 1
        start_token = f"[[INLINE_{token_index:04d}]]"
        token_index += 1
        end_token = f"[[INLINE_{token_index:04d}]]"
        parts.append(start_token)
        placeholders.append(
            {
                "token": start_token,
                "text": "",
                "display_text": "",
                "kind": "container_start",
                "container_kind": container_kind,
                "end_token": end_token,
            }
        )
        for child in list(element):
            token_index = _append_docx_translation_text_parts(
                element=child,
                parts=parts,
                placeholders=placeholders,
                token_index=token_index,
                vertical_alignment=current_alignment,
            )
        parts.append(end_token)
        placeholders.append(
            {
                "token": end_token,
                "text": "",
                "display_text": "",
                "kind": "container_end",
                "container_kind": container_kind,
                "start_token": start_token,
            }
        )
        return token_index

    if tag_name == "t" and element.text:
        if _should_protect_docx_inline_text(element.text, current_alignment):
            token_index += 1
            token = f"[[INLINE_{token_index:04d}]]"
            display_text = _apply_docx_vertical_alignment(element.text, current_alignment)
            parts.append(token)
            placeholders.append(
                {
                    "token": token,
                    "text": element.text,
                    "display_text": display_text,
                    "kind": current_alignment or "inline",
                }
            )
        else:
            parts.append(_apply_docx_vertical_alignment(element.text, current_alignment))
    elif tag_name == "drawing":
        token_index += 1
        token = f"[[INLINE_{token_index:04d}]]"
        parts.append(token)
        placeholders.append(
            {
                "token": token,
                "text": "",
                "display_text": "",
                "kind": "inline_image",
            }
        )
    elif tag_name == "tab":
        parts.append("\t")
    elif tag_name == "br":
        parts.append("\n")

    for child in list(element):
        token_index = _append_docx_translation_text_parts(
            element=child,
            parts=parts,
            placeholders=placeholders,
            token_index=token_index,
            vertical_alignment=current_alignment,
        )
    return token_index


def _docx_is_self_closing_inline_object(element: ET.Element) -> bool:
    tag_name = _local_name(element.tag)
    return tag_name in {
        "drawing",
        "oMath",
        "oMathPara",
        "footnoteReference",
        "endnoteReference",
        "commentReference",
        "fldSimple",
        "instrText",
    }


def _docx_self_closing_inline_kind(element: ET.Element) -> str:
    tag_name = _local_name(element.tag)
    if tag_name == "drawing":
        return "inline_image"
    if tag_name in {"oMath", "oMathPara"}:
        return "equation"
    if tag_name in {"footnoteReference", "endnoteReference", "commentReference"}:
        return tag_name
    if tag_name in {"fldSimple", "instrText"}:
        return "field"
    return "inline"


def _docx_paired_inline_container_kind(element: ET.Element) -> str | None:
    tag_name = _local_name(element.tag)
    if tag_name == "hyperlink":
        return "hyperlink"
    if tag_name in {"ins", "del", "moveFrom", "moveTo"}:
        return f"revision_{tag_name}"
    if tag_name == "r":
        return _docx_styled_run_container_kind(element)
    return None


def _docx_styled_run_container_kind(run: ET.Element) -> str | None:
    run_properties = run.find("w:rPr", WORD_NS)
    if run_properties is None:
        return None

    if not any((node.text or "").strip() for node in run.findall(".//w:t", WORD_NS)):
        return None

    vertical_alignment = _docx_run_vertical_alignment(run)
    if vertical_alignment in {"superscript", "subscript"}:
        text = "".join(node.text or "" for node in run.findall(".//w:t", WORD_NS))
        if _should_protect_docx_inline_text(text, vertical_alignment):
            return None

    styled_flags = {
        "bold": run_properties.find("w:b", WORD_NS) is not None,
        "italic": run_properties.find("w:i", WORD_NS) is not None,
        "underline": run_properties.find("w:u", WORD_NS) is not None,
        "color": run_properties.find("w:color", WORD_NS) is not None,
        "hidden": run_properties.find("w:vanish", WORD_NS) is not None,
        "noProof": run_properties.find("w:noProof", WORD_NS) is not None,
        "smallCaps": run_properties.find("w:smallCaps", WORD_NS) is not None,
    }
    active_styles = [name for name, active in styled_flags.items() if active]
    if not active_styles:
        return None
    return "styled_run:" + ",".join(active_styles)


def _should_protect_docx_inline_text(
    text: str,
    vertical_alignment: str | None,
) -> bool:
    if vertical_alignment not in {"superscript", "subscript"}:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return len(stripped) <= 8 and not any(char.isspace() for char in stripped)


def _append_docx_text_parts(
    element: ET.Element,
    parts: list[str],
    vertical_alignment: str | None = None,
) -> None:
    tag_name = _local_name(element.tag)
    current_alignment = vertical_alignment
    if tag_name == "r":
        current_alignment = _docx_run_vertical_alignment(element) or vertical_alignment

    if tag_name == "t" and element.text:
        parts.append(_apply_docx_vertical_alignment(element.text, current_alignment))
    elif tag_name == "tab":
        parts.append("\t")
    elif tag_name == "br":
        parts.append("\n")

    for child in list(element):
        _append_docx_text_parts(child, parts, current_alignment)


def _docx_run_vertical_alignment(run: ET.Element) -> str | None:
    vertical_align = run.find("w:rPr/w:vertAlign", WORD_NS)
    if vertical_align is None:
        return None
    return vertical_align.attrib.get(f"{{{WORD_NS['w']}}}val")


def _apply_docx_vertical_alignment(text: str, vertical_alignment: str | None) -> str:
    if vertical_alignment == "superscript":
        return text.translate(SUPERSCRIPT_CHARS)
    if vertical_alignment == "subscript":
        return text.translate(SUBSCRIPT_CHARS)
    return text


def _docx_table_cell_text(cell: ET.Element) -> str:
    paragraph_texts = [
        _docx_text(paragraph)
        for paragraph in _docx_direct_cell_paragraphs(cell)
    ]
    paragraph_texts = [text for text in paragraph_texts if text]
    if paragraph_texts:
        return _normalize_table_cell_text("; ".join(paragraph_texts))
    return ""


def _docx_table_cell_translation_text(cell: ET.Element) -> tuple[str, list[dict[str, str]]]:
    paragraph_texts: list[str] = []
    inline_placeholders: list[dict[str, str]] = []
    token_count = 0
    for paragraph in _docx_direct_cell_paragraphs(cell):
        paragraph_text, paragraph_placeholders = _docx_text_with_inline_placeholders(paragraph)
        for placeholder in paragraph_placeholders:
            token_count += 1
            old_token = placeholder["token"]
            new_token = f"[[INLINE_{token_count:04d}]]"
            paragraph_text = paragraph_text.replace(old_token, new_token)
            placeholder = {**placeholder, "token": new_token}
            inline_placeholders.append(placeholder)
        if paragraph_text:
            paragraph_texts.append(paragraph_text)

    if paragraph_texts:
        return _normalize_table_cell_text("; ".join(paragraph_texts)), inline_placeholders

    return "", inline_placeholders


def _normalize_table_cell_text(text: str) -> str:
    text = _normalize_inline_text(text)
    text = re.sub(r"\s*\n+\s*", "; ", text)
    text = re.sub(r"\s*;\s*", "; ", text)
    text = _normalize_scientific_unit_ocr_artifacts(text)
    return text.strip(" ;")


def _normalize_scientific_unit_ocr_artifacts(text: str) -> str:
    return re.sub(
        rf"\b([1-9])10([{SUPERSCRIPT_DIGITS}]+)(?=\s*/)",
        lambda match: f"{match.group(1)}\u00d710{match.group(2)}",
        text,
    )


def _docx_paragraph_style(paragraph: ET.Element) -> str | None:
    style = paragraph.find("w:pPr/w:pStyle", WORD_NS)
    if style is None:
        return None
    return style.attrib.get(f"{{{WORD_NS['w']}}}val")


def _heading_level(style: str | None) -> int | None:
    if not style:
        return None
    match = re.search(r"heading\s*(\d+)$|Heading(\d+)$", style, re.IGNORECASE)
    if not match:
        return None
    level = match.group(1) or match.group(2)
    return int(level)


def _style_block_type(style: str | None) -> str | None:
    if not style:
        return None

    normalized = style.replace(" ", "").replace("_", "").replace("-", "").casefold()
    if "caption" in normalized:
        return "caption"
    if "quote" in normalized or "blockquote" in normalized or "epigraph" in normalized:
        return "quote"
    if "toc" in normalized:
        return "toc_entry"
    if "bibliography" in normalized or "reference" in normalized:
        return "reference"
    if "index" in normalized:
        return "index_entry"
    return None


def _docx_list_metadata(paragraph: ET.Element) -> dict[str, str | None] | None:
    num_pr = paragraph.find(".//w:numPr", WORD_NS)
    if num_pr is None:
        return None

    num_id = num_pr.find("w:numId", WORD_NS)
    level = num_pr.find("w:ilvl", WORD_NS)
    return {
        "num_id": num_id.attrib.get(f"{{{WORD_NS['w']}}}val") if num_id is not None else None,
        "level": level.attrib.get(f"{{{WORD_NS['w']}}}val") if level is not None else None,
    }


def _docx_formatting_flags(element: ET.Element) -> dict[str, bool]:
    return {
        "bold": element.find(".//w:b", WORD_NS) is not None,
        "italic": element.find(".//w:i", WORD_NS) is not None,
        "underline": element.find(".//w:u", WORD_NS) is not None,
        "small_caps": element.find(".//w:smallCaps", WORD_NS) is not None,
        "superscript": _docx_has_vertical_alignment(element, "superscript"),
        "subscript": _docx_has_vertical_alignment(element, "subscript"),
        "hidden": element.find(".//w:vanish", WORD_NS) is not None,
    }


def _docx_has_vertical_alignment(element: ET.Element, value: str) -> bool:
    return any(
        node.attrib.get(f"{{{WORD_NS['w']}}}val") == value
        for node in element.findall(".//w:vertAlign", WORD_NS)
    )


def _docx_hyperlinks(
    paragraph: ET.Element,
    relationships: dict[str, str],
) -> list[dict[str, str | None]]:
    hyperlinks: list[dict[str, str | None]] = []
    for hyperlink in paragraph.findall(".//w:hyperlink", WORD_NS):
        relationship_id = hyperlink.attrib.get(f"{{{WORD_NS['r']}}}id")
        anchor = hyperlink.attrib.get(f"{{{WORD_NS['w']}}}anchor")
        hyperlinks.append(
            {
                "relationship_id": relationship_id,
                "target": relationships.get(relationship_id or ""),
                "anchor": anchor,
                "text": _docx_text(hyperlink),
            }
        )
    return hyperlinks


def _docx_contains_revisions(element: ET.Element) -> bool:
    revision_tags = {"ins", "del", "moveFrom", "moveTo"}
    return any(_local_name(node.tag) in revision_tags for node in element.iter())


def _docx_contains_form_control(element: ET.Element) -> bool:
    control_tags = {"sdt", "ffData", "checkBox", "checkbox", "dropDownList", "comboBox"}
    return any(_local_name(node.tag) in control_tags for node in element.iter())


def _docx_contains_checkbox(element: ET.Element) -> bool:
    checkbox_tags = {"checkBox", "checkbox", "checked"}
    return any(_local_name(node.tag) in checkbox_tags for node in element.iter())


def _docx_comment_refs(paragraph: ET.Element) -> list[str]:
    comment_ids: list[str] = []
    for node in paragraph.iter():
        if _local_name(node.tag) not in {"commentRangeStart", "commentReference"}:
            continue
        comment_id = node.attrib.get(f"{{{WORD_NS['w']}}}id")
        if comment_id and comment_id not in comment_ids:
            comment_ids.append(comment_id)
    return comment_ids


def _docx_bookmarks(paragraph: ET.Element) -> list[dict[str, str | None]]:
    bookmarks: list[dict[str, str | None]] = []
    for bookmark in paragraph.findall(".//w:bookmarkStart", WORD_NS):
        bookmarks.append(
            {
                "id": bookmark.attrib.get(f"{{{WORD_NS['w']}}}id"),
                "name": bookmark.attrib.get(f"{{{WORD_NS['w']}}}name"),
            }
        )
    return bookmarks


def _docx_field_codes(paragraph: ET.Element) -> list[str]:
    return [
        node.text.strip()
        for node in paragraph.findall(".//w:instrText", WORD_NS)
        if node.text and node.text.strip()
    ]


def _docx_image_infos(
    paragraph: ET.Element,
    relationships: dict[str, str],
) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    drawing_containers = [
        *paragraph.findall(".//wp:inline", WORD_NS),
        *paragraph.findall(".//wp:anchor", WORD_NS),
    ]
    if drawing_containers:
        for container in drawing_containers:
            doc_pr = container.find(".//wp:docPr", WORD_NS)
            for blip in container.findall(".//a:blip", WORD_NS):
                image_info = _docx_image_info_from_blip(blip, doc_pr, relationships)
                if image_info:
                    images.append(image_info)
        return images

    for blip in paragraph.findall(".//a:blip", WORD_NS):
        doc_pr = _first_ancestor_child(paragraph, blip, "wp:docPr")
        image_info = _docx_image_info_from_blip(blip, doc_pr, relationships)
        if image_info:
            images.append(image_info)
    return images


def _docx_image_info_from_blip(
    blip: ET.Element,
    doc_pr: ET.Element | None,
    relationships: dict[str, str],
) -> dict[str, str] | None:
    relationship_id = blip.attrib.get(f"{{{WORD_NS['r']}}}embed")
    link_relationship_id = blip.attrib.get(f"{{{WORD_NS['r']}}}link")
    target = relationships.get(relationship_id or "")
    linked_target = relationships.get(link_relationship_id or "")
    if not target and not linked_target:
        return None
    return {
        "relationship_id": relationship_id or "",
        "link_relationship_id": link_relationship_id or "",
        "target": target or linked_target,
        "linked_target": linked_target,
        "title": doc_pr.attrib.get("title", "") if doc_pr is not None else "",
        "description": doc_pr.attrib.get("descr", "") if doc_pr is not None else "",
    }


def _first_ancestor_child(
    root: ET.Element,
    descendant: ET.Element,
    child_path: str,
) -> ET.Element | None:
    for node in root.iter():
        if descendant in list(node.iter()):
            found = node.find(f".//{child_path}", WORD_NS)
            if found is not None:
                return found
    return None


def _epub_rootfile(epub: ZipFile) -> str:
    container = ET.fromstring(epub.read("META-INF/container.xml"))
    rootfile = container.find(".//c:rootfile", EPUB_CONTAINER_NS)
    if rootfile is None:
        raise ValueError("EPUB container.xml does not declare a rootfile")
    full_path = rootfile.attrib.get("full-path")
    if not full_path:
        raise ValueError("EPUB rootfile is missing full-path")
    return full_path


def _epub_spine_items(opf_xml: bytes, rootfile: str) -> list[str]:
    root = ET.fromstring(opf_xml)
    manifest: dict[str, str] = {}
    for item in root.findall(".//opf:manifest/opf:item", EPUB_OPF_NS):
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")
        if item_id and href and "html" in media_type:
            manifest[item_id] = href

    base_dir = posixpath.dirname(rootfile)
    spine_paths: list[str] = []
    for itemref in root.findall(".//opf:spine/opf:itemref", EPUB_OPF_NS):
        idref = itemref.attrib.get("idref")
        href = manifest.get(idref or "")
        if href:
            spine_paths.append(posixpath.normpath(posixpath.join(base_dir, href)))
    return spine_paths


def _epub_manifest_items(opf_xml: bytes, rootfile: str) -> list[dict[str, str]]:
    root = ET.fromstring(opf_xml)
    base_dir = posixpath.dirname(rootfile)
    items: list[dict[str, str]] = []
    for item in root.findall(".//opf:manifest/opf:item", EPUB_OPF_NS):
        href = item.attrib.get("href", "")
        if not href:
            continue
        items.append(
            {
                "id": item.attrib.get("id", ""),
                "href": href,
                "path": posixpath.normpath(posixpath.join(base_dir, href)),
                "media_type": item.attrib.get("media-type", ""),
                "properties": item.attrib.get("properties", ""),
            }
        )
    return items


def _epub_cover_item_ids(opf_xml: bytes) -> set[str]:
    root = ET.fromstring(opf_xml)
    cover_item_ids: set[str] = set()
    for meta in root.findall(".//opf:metadata/opf:meta", EPUB_OPF_NS):
        if meta.attrib.get("name") == "cover" and meta.attrib.get("content"):
            cover_item_ids.add(meta.attrib["content"])
    return cover_item_ids


def _epub_cover_blocks(
    manifest_items: list[dict[str, str]],
    cover_item_ids: set[str],
    block_offset: int,
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    for item in manifest_items:
        is_image = item["media_type"].startswith("image/")
        is_cover = "cover-image" in item["properties"].split() or item["id"] in cover_item_ids
        if not is_image or not is_cover:
            continue
        blocks.append(
            DocumentBlock(
                block_id=_block_id(block_offset + len(blocks)),
                type="image",
                text="",
                translate=False,
                metadata={
                    "src": item["path"],
                    "manifest_id": item["id"],
                    "media_type": item["media_type"],
                    "is_cover": True,
                },
            )
        )
    return blocks


def _epub_extra_item_paths(
    manifest_items: list[dict[str, str]],
    spine_items: list[str],
    required_properties: set[str],
) -> list[str]:
    spine_item_set = set(spine_items)
    extra_paths: list[str] = []
    for item in manifest_items:
        properties = set(item["properties"].split())
        if not properties.intersection(required_properties):
            continue
        if item["path"] in spine_item_set:
            continue
        if "html" not in item["media_type"]:
            continue
        extra_paths.append(item["path"])
    return extra_paths


def _epub_package_metadata(opf_xml: bytes) -> dict[str, list[str]]:
    root = ET.fromstring(opf_xml)
    metadata: dict[str, list[str]] = {}
    fields = {
        "title": ".//opf:metadata/dc:title",
        "creator": ".//opf:metadata/dc:creator",
        "language": ".//opf:metadata/dc:language",
        "publisher": ".//opf:metadata/dc:publisher",
        "description": ".//opf:metadata/dc:description",
    }
    for field_name, xpath in fields.items():
        values = [
            (node.text or "").strip()
            for node in root.findall(xpath, EPUB_OPF_NS)
            if (node.text or "").strip()
        ]
        if values:
            metadata[field_name] = values
    return metadata


def _epub_metadata_blocks(
    metadata: dict[str, list[str]],
    block_offset: int,
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    for field_name in ("title", "creator", "language", "publisher", "description"):
        for value in metadata.get(field_name, []):
            blocks.append(
                DocumentBlock(
                    block_id=_block_id(block_offset + len(blocks)),
                    type=f"metadata_{field_name}",
                    text=value,
                    translate=field_name in {"title", "description"},
                    metadata={"metadata_field": field_name},
                )
            )
    return blocks


def _epub_content_blocks(
    content: bytes,
    item_path: str,
    block_offset: int,
) -> list[DocumentBlock]:
    soup = BeautifulSoup(content, "html.parser")
    body = soup.body or soup
    blocks: list[DocumentBlock] = []
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

        common_metadata = _epub_common_metadata(tag, item_path)
        if tag_name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            blocks.append(
                DocumentBlock(
                    block_id=_block_id(block_offset + len(blocks)),
                    type="heading",
                    text=_html_text(tag),
                    translate=True,
                    level=int(tag_name[1]),
                    metadata=common_metadata,
                )
            )
        elif tag_name == "p":
            text = _html_text(tag)
            if text:
                preserve_exact = _is_scene_break(text)
                blocks.append(
                    DocumentBlock(
                        block_id=_block_id(block_offset + len(blocks)),
                        type="special" if preserve_exact else "paragraph",
                        text=text,
                        translate=True,
                        metadata={**common_metadata, "preserve_exact": preserve_exact},
                    )
                )
        elif tag_name == "li":
            text = _html_text(tag)
            if text:
                blocks.append(
                    DocumentBlock(
                        block_id=_block_id(block_offset + len(blocks)),
                        type="list_item",
                        text=text,
                        translate=True,
                        metadata={
                            **common_metadata,
                            "list_type": _epub_list_type(tag),
                            "list_level": _epub_list_level(tag),
                        },
                    )
                )
        elif tag_name == "blockquote":
            text = _html_text(tag)
            if text:
                blocks.append(
                    DocumentBlock(
                        block_id=_block_id(block_offset + len(blocks)),
                        type="quote",
                        text=text,
                        translate=True,
                        metadata=common_metadata,
                    )
                )
        elif tag_name == "figcaption":
            text = _html_text(tag)
            if text:
                blocks.append(
                    DocumentBlock(
                        block_id=_block_id(block_offset + len(blocks)),
                        type="caption",
                        text=text,
                        translate=True,
                        metadata=common_metadata,
                    )
                )
        elif tag_name == "table":
            blocks.append(_epub_table_block(tag, item_path, block_offset + len(blocks)))
        elif tag_name in {"pre", "code"}:
            text = _html_text(tag)
            if text:
                blocks.append(
                    DocumentBlock(
                        block_id=_block_id(block_offset + len(blocks)),
                        type="code",
                        text=text,
                        translate=False,
                        metadata=common_metadata,
                    )
                )
        elif tag_name in {"aside", "nav"}:
            text = _html_text(tag)
            if text:
                semantic_type = _epub_semantic_block_type(tag, default=tag_name)
                blocks.append(
                    DocumentBlock(
                        block_id=_block_id(block_offset + len(blocks)),
                        type=semantic_type,
                        text=text,
                        translate=semantic_type != "toc",
                        metadata=common_metadata,
                    )
                )
        elif tag_name == "span":
            blocks.append(
                DocumentBlock(
                    block_id=_block_id(block_offset + len(blocks)),
                    type="page_break",
                    text=_html_text(tag),
                    translate=False,
                    metadata=common_metadata,
                )
            )
        elif tag_name == "img":
            blocks.append(
                DocumentBlock(
                    block_id=_block_id(block_offset + len(blocks)),
                    type="image",
                    text="",
                    translate=False,
                    metadata={
                        **common_metadata,
                        "src": tag.get("src", ""),
                        "alt": tag.get("alt", ""),
                        "title": tag.get("title", ""),
                    },
                )
            )

    return blocks


def _epub_table_block(tag, item_path: str, block_offset: int) -> DocumentBlock:
    rows: list[list[str]] = []
    row_metadata: list[dict[str, object]] = []
    has_merged_cells = False
    has_header_cells = False
    for row in tag.find_all("tr"):
        cells = []
        cell_metadata = []
        for cell in row.find_all(["th", "td"]):
            colspan = cell.get("colspan")
            rowspan = cell.get("rowspan")
            is_header = (cell.name or "").lower() == "th"
            has_header_cells = has_header_cells or is_header
            has_merged_cells = has_merged_cells or bool(colspan) or bool(rowspan)
            cells.append(_html_text(cell))
            cell_metadata.append(
                {
                    "tag": cell.name,
                    "colspan": colspan,
                    "rowspan": rowspan,
                    "scope": cell.get("scope"),
                    "is_header": is_header,
                    "looks_formula_like": _looks_formula_like(_html_text(cell)),
                    "looks_numeric": _looks_numeric(_html_text(cell)),
                }
            )
        if cells:
            rows.append(cells)
            row_metadata.append({"cells": cell_metadata})
    rendered_rows = ["\t".join(cell for cell in row) for row in rows]
    return DocumentBlock(
        block_id=_block_id(block_offset),
        type="table",
        text="\n".join(rendered_rows).strip(),
        translate=True,
        metadata={
            **_epub_common_metadata(tag, item_path),
            "rows": rows,
            "row_metadata": row_metadata,
            "has_merged_cells": has_merged_cells,
            "has_header_cells": has_header_cells,
        },
    )


def _epub_common_metadata(tag, item_path: str) -> dict[str, object]:
    return {
        "source_item": item_path,
        "html_tag": tag.name,
        "id": tag.get("id", ""),
        "class": tag.get("class", []),
        "epub_type": tag.get("epub:type", ""),
        "role": tag.get("role", ""),
        "lang": tag.get("lang") or tag.get("xml:lang") or "",
        "hrefs": [link.get("href", "") for link in tag.find_all("a") if link.get("href")],
        "formatting": _epub_formatting_flags(tag),
        "contains_code": tag.find("code") is not None or (tag.name or "").lower() == "code",
        "contains_math": tag.find("math") is not None,
        "contains_form_control": tag.find(["input", "select", "textarea", "button"]) is not None,
        "contains_checkbox": bool(tag.find("input", attrs={"type": "checkbox"})),
        "contains_page_break": _epub_contains_page_break(tag),
        "contains_hidden_text": tag.find(_epub_is_hidden) is not None,
    }


def _epub_formatting_flags(tag) -> dict[str, bool]:
    html_tag = (tag.name or "").lower()
    class_values = " ".join(tag.get("class", [])).casefold()
    style = tag.get("style", "").casefold()
    return {
        "bold": html_tag in {"b", "strong"} or tag.find(["b", "strong"]) is not None,
        "italic": html_tag in {"i", "em"} or tag.find(["i", "em"]) is not None,
        "underline": "underline" in style or tag.find("u") is not None,
        "small_caps": "small-caps" in style or "smallcaps" in class_values,
        "superscript": html_tag == "sup" or tag.find("sup") is not None,
        "subscript": html_tag == "sub" or tag.find("sub") is not None,
    }


def _epub_contains_page_break(tag) -> bool:
    if _epub_is_page_break(tag):
        return True
    for node in tag.find_all(True):
        if _epub_is_page_break(node):
            return True
    return False


def _epub_is_page_break(tag) -> bool:
    values = " ".join(
        str(value)
        for value in [
            tag.get("epub:type", ""),
            tag.get("role", ""),
            " ".join(tag.get("class", [])),
            tag.get("id", ""),
        ]
    ).casefold()
    return "pagebreak" in values or "page-break" in values


def _epub_semantic_block_type(tag, default: str) -> str:
    values = " ".join(
        str(value)
        for value in [
            tag.get("epub:type", ""),
            tag.get("role", ""),
            " ".join(tag.get("class", [])),
            tag.get("id", ""),
        ]
    ).casefold()
    if "footnote" in values:
        return "footnote"
    if "endnote" in values or "rearnote" in values:
        return "endnote"
    if "toc" in values or default == "nav":
        return "toc"
    if "sidebar" in values or "note" in values:
        return "special"
    return default


def _epub_list_type(tag) -> str | None:
    parent = tag.parent
    while parent is not None and getattr(parent, "name", None):
        parent_name = parent.name.lower()
        if parent_name in {"ol", "ul"}:
            return parent_name
        parent = parent.parent
    return None


def _epub_list_level(tag) -> int:
    level = 0
    parent = tag.parent
    while parent is not None and getattr(parent, "name", None):
        if parent.name.lower() in {"ol", "ul"}:
            level += 1
        parent = parent.parent
    return level


def _epub_is_hidden(tag) -> bool:
    if tag.has_attr("hidden") or tag.get("aria-hidden") == "true":
        return True
    style = tag.get("style", "")
    normalized_style = re.sub(r"\s+", "", style.casefold())
    return "display:none" in normalized_style or "visibility:hidden" in normalized_style


def _looks_formula_like(text: str) -> bool:
    return bool(re.search(r"[=∑∫√±×÷≤≥]|\\frac|\\sum|\\int", text))


def _looks_numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\s\d.,%$€£¥+\-()/:]+", text.strip()))


def _is_scene_break(text: str) -> bool:
    normalized = text.strip()
    return bool(re.fullmatch(r"([*\-_=#]\s*){3,}", normalized))


def _has_block_parent(tag, block_tags: set[str]) -> bool:
    parent = tag.parent
    while parent is not None and getattr(parent, "name", None):
        parent_name = parent.name.lower()
        if parent_name in block_tags:
            return True
        if parent_name == "body":
            return False
        parent = parent.parent
    return False


def _html_text(tag) -> str:
    return _normalize_inline_text(unescape(tag.get_text(" ", strip=True)))


def _normalize_inline_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _block_id(index: int) -> str:
    return f"b{index + 1:04d}"
