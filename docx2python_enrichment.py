from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from compare_docx_parsers import (
    docx2python_paragraph_is_in_table,
    docx2python_paragraph_text,
    docx2python_table_shape,
    flatten_nested,
    is_docx2python_paragraph,
    is_docx2python_media_placeholder,
    safe_docx2python_attr,
)
from document_model import DocumentBlock, ParsedDocument


BODY_TEXT_BLOCK_TYPES = {
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


@dataclass(frozen=True)
class Docx2PythonEnrichmentResult:
    parsed_document: ParsedDocument
    report: dict[str, Any]


def enrich_docx_with_docx2python(
    parsed_document: ParsedDocument,
) -> Docx2PythonEnrichmentResult:
    if parsed_document.source_format != "docx":
        return Docx2PythonEnrichmentResult(
            parsed_document=parsed_document,
            report={
                "enabled": False,
                "status": "skipped",
                "reason": "docx2python enrichment only applies to DOCX inputs.",
            },
        )

    source_path = Path(parsed_document.source_path or "")
    if not source_path.exists():
        return Docx2PythonEnrichmentResult(
            parsed_document=parsed_document,
            report={
                "enabled": True,
                "status": "skipped",
                "reason": "Source DOCX path was not available.",
            },
        )

    try:
        units = docx2python_body_units(source_path)
    except Exception as exc:
        return Docx2PythonEnrichmentResult(
            parsed_document=parsed_document,
            report={
                "enabled": True,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )

    enriched_blocks: list[DocumentBlock] = []
    unit_index = 0
    matched_units = 0
    mismatches: list[dict[str, Any]] = []

    for block in parsed_document.blocks:
        if not is_body_translatable_unit(block):
            enriched_blocks.append(block)
            continue

        if unit_index >= len(units):
            mismatches.append(
                {
                    "block_id": block.block_id,
                    "block_type": block.type,
                    "reason": "No remaining docx2python unit to align.",
                }
            )
            enriched_blocks.append(block)
            continue

        unit = units[unit_index]
        unit_index += 1
        if block_matches_docx2python_unit(block, unit):
            enriched_blocks.append(
                replace(
                    block,
                    metadata={
                        **block.metadata,
                        "docx2python_enrichment": unit["metadata"],
                    },
                )
            )
            matched_units += 1
        else:
            mismatches.append(
                {
                    "block_id": block.block_id,
                    "block_type": block.type,
                    "docx2python_unit_kind": unit["kind"],
                    "reason": "Block type did not match the docx2python unit kind.",
                }
            )
            enriched_blocks.append(block)

    unused_units = max(0, len(units) - unit_index)
    status = "ok" if not mismatches and unused_units == 0 else "partial"
    return Docx2PythonEnrichmentResult(
        parsed_document=replace(parsed_document, blocks=enriched_blocks),
        report={
            "enabled": True,
            "status": status,
            "source_path": str(source_path),
            "docx2python_units": len(units),
            "matched_units": matched_units,
            "unused_docx2python_units": unused_units,
            "mismatches": mismatches,
            "notes": [
                "docx2python enrichment is metadata only; XML parsing and DOCX "
                "write-back remain controlled by this project's parser/writer.",
                "Use this data to inspect run styles and to guide future inline "
                "placeholder support.",
            ],
        },
    )


def is_body_translatable_unit(block: DocumentBlock) -> bool:
    if block.metadata.get("source_part"):
        return False
    return block.translate and bool(block.text.strip()) and (
        block.type in BODY_TEXT_BLOCK_TYPES or block.type == "table"
    )


def block_matches_docx2python_unit(
    block: DocumentBlock,
    unit: dict[str, Any],
) -> bool:
    if block.type == "table":
        return unit["kind"] == "table"
    return unit["kind"] == "text"


def docx2python_body_units(path: Path) -> list[dict[str, Any]]:
    try:
        from docx2python import docx2python
    except ImportError as exc:
        raise RuntimeError(
            "docx2python is required for DOCX enrichment. "
            "Install project requirements first."
        ) from exc

    units: list[dict[str, Any]] = []
    with docx2python(path, html=True) as doc:
        for structure in list(safe_docx2python_attr(doc, "body_pars", [])):
            paragraphs = [
                paragraph
                for paragraph in flatten_nested(structure)
                if is_docx2python_paragraph(paragraph)
            ]
            if any(docx2python_paragraph_is_in_table(paragraph) for paragraph in paragraphs):
                units.append(
                    {
                        "kind": "table",
                        "metadata": {
                            "table_shape": docx2python_table_shape(structure),
                            "run_style_counts": run_style_counts(paragraphs),
                            "nonempty_cell_paragraphs": sum(
                                1
                                for paragraph in paragraphs
                                if docx2python_paragraph_text(paragraph)
                            ),
                        },
                    }
                )
            else:
                for paragraph in paragraphs:
                    text = docx2python_paragraph_text(paragraph)
                    if not text or is_docx2python_media_placeholder(text):
                        continue
                    units.append(
                        {
                            "kind": "text",
                            "metadata": {
                                "paragraph_style": getattr(paragraph, "style", ""),
                                "html_style": list(getattr(paragraph, "html_style", []) or []),
                                "lineage": list(getattr(paragraph, "lineage", []) or []),
                                "list_position": normalize_list_position(
                                    getattr(paragraph, "list_position", None)
                                ),
                                "run_count": len(getattr(paragraph, "runs", []) or []),
                                "run_style_counts": run_style_counts([paragraph]),
                                "text_sample": text[:160],
                            },
                        }
                    )
    return units


def run_style_counts(paragraphs: list[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for paragraph in paragraphs:
        for run in getattr(paragraph, "runs", []) or []:
            for style in getattr(run, "html_style", []) or []:
                counts[str(style)] += 1
    return dict(sorted(counts.items()))


def normalize_list_position(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return [normalize_list_position(item) for item in value]
    if isinstance(value, list):
        return [normalize_list_position(item) for item in value]
    return value
