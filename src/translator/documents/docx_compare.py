from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from translator.documents.adapters import load_docx
from translator.documents.model import DocumentBlock


TEXT_BLOCK_TYPES = {
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
AUXILIARY_BLOCK_TYPES = {"footnote", "endnote", "comment", "header", "footer"}


@dataclass
class ParserComparison:
    source_path: str
    our_parser: dict[str, Any]
    docx2python: dict[str, Any]
    deltas: dict[str, int]
    table_shape_differences: list[dict[str, Any]]
    likely_misses: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_docx_parsers(path: Path) -> ParserComparison:
    if path.suffix.lower() != ".docx":
        raise ValueError("Parser comparison only supports .docx files.")

    parsed = load_docx(path)
    our_summary = summarize_our_parser(parsed.blocks)
    docx2python_summary = summarize_docx2python(path)
    deltas = {
        "body_text_blocks": (
            our_summary["body_text_blocks"]
            - docx2python_summary["body_text_paragraphs"]
        ),
        "tables": our_summary["tables"] - docx2python_summary["tables"],
        "images": our_summary["images"] - docx2python_summary["images"],
        "footnotes": our_summary["footnotes"] - docx2python_summary["footnotes"],
        "endnotes": our_summary["endnotes"] - docx2python_summary["endnotes"],
        "comments": our_summary["comments"] - docx2python_summary["comments"],
        "headers": our_summary["headers"] - docx2python_summary["headers"],
        "footers": our_summary["footers"] - docx2python_summary["footers"],
        "inline_placeholders": (
            our_summary["inline_placeholders"]
            - docx2python_summary["superscript_runs"]
            - docx2python_summary["subscript_runs"]
        ),
    }

    likely_misses = likely_parser_misses(our_summary, docx2python_summary)
    table_shape_differences = compare_table_shapes(
        our_summary["table_shapes"],
        docx2python_summary["table_shapes"],
    )
    notes = [
        "docx2python is used here as an independent extraction reference, not as "
        "the source of truth.",
        "Paragraph counts compare non-table body text only; table cells are compared "
        "through table counts and shapes.",
        "Inline placeholder deltas compare our protected sup/sub placeholders "
        "against docx2python sup/sub runs, so a nonzero delta is a review signal "
        "rather than an automatic failure.",
    ]
    return ParserComparison(
        source_path=str(path),
        our_parser=our_summary,
        docx2python=docx2python_summary,
        deltas=deltas,
        table_shape_differences=table_shape_differences,
        likely_misses=likely_misses,
        notes=notes,
    )


def summarize_our_parser(blocks: list[DocumentBlock]) -> dict[str, Any]:
    block_type_counts = Counter(block.type for block in blocks)
    body_text_blocks = [
        block
        for block in blocks
        if block.translate
        and block.text.strip()
        and block.type in TEXT_BLOCK_TYPES
        and not block.metadata.get("source_part")
    ]
    table_blocks = [block for block in blocks if block.type == "table"]

    return {
        "total_blocks": len(blocks),
        "translatable_blocks": sum(
            1 for block in blocks if block.translate and block.text.strip()
        ),
        "block_type_counts": dict(sorted(block_type_counts.items())),
        "body_text_blocks": len(body_text_blocks),
        "body_text_samples": [block.text[:120] for block in body_text_blocks[:5]],
        "tables": len(table_blocks),
        "table_shapes": [_our_table_shape(block) for block in table_blocks],
        "images": block_type_counts.get("image", 0),
        "footnotes": block_type_counts.get("footnote", 0),
        "endnotes": block_type_counts.get("endnote", 0),
        "comments": block_type_counts.get("comment", 0),
        "headers": block_type_counts.get("header", 0),
        "footers": block_type_counts.get("footer", 0),
        "inline_placeholders": sum(count_inline_placeholders(block) for block in blocks),
    }


def summarize_docx2python(path: Path) -> dict[str, Any]:
    try:
        from docx2python import docx2python
    except ImportError as exc:
        raise RuntimeError(
            "docx2python is required for this diagnostic. "
            "Install project requirements first."
        ) from exc

    with docx2python(path, html=True) as doc:
        body_structures = list(safe_docx2python_attr(doc, "body_pars", []))
        body_text_paragraphs = [
            par
            for par in flatten_nested(body_structures)
            if is_docx2python_paragraph(par)
            and docx2python_paragraph_text(par).strip()
            and not docx2python_paragraph_is_in_table(par)
            and not is_docx2python_media_placeholder(docx2python_paragraph_text(par))
        ]
        table_structures = [
            structure
            for structure in body_structures
            if any(
                docx2python_paragraph_is_in_table(par)
                for par in flatten_nested(structure)
                if is_docx2python_paragraph(par)
            )
        ]
        footnote_paragraphs = nonempty_docx2python_paragraphs(
            safe_docx2python_attr(doc, "footnotes_pars", [])
        )
        endnote_paragraphs = nonempty_docx2python_paragraphs(
            safe_docx2python_attr(doc, "endnotes_pars", [])
        )
        comment_paragraphs = nonempty_docx2python_paragraphs(
            safe_docx2python_attr(doc, "comments", [])
        )
        header_paragraphs = nonempty_docx2python_paragraphs(
            safe_docx2python_attr(doc, "header_pars", [])
        )
        footer_paragraphs = nonempty_docx2python_paragraphs(
            safe_docx2python_attr(doc, "footer_pars", [])
        )
        run_style_counts = docx2python_run_style_counts(
            safe_docx2python_attr(doc, "document_pars", [])
        )

        return {
            "body_text_paragraphs": len(body_text_paragraphs),
            "body_text_samples": [
                docx2python_paragraph_text(par)[:120]
                for par in body_text_paragraphs[:5]
            ],
            "tables": len(table_structures),
            "table_shapes": [
                docx2python_table_shape(structure)
                for structure in table_structures
            ],
            "images": len(safe_docx2python_attr(doc, "images", {}) or {}),
            "footnotes": len(footnote_paragraphs),
            "endnotes": len(endnote_paragraphs),
            "comments": len(comment_paragraphs),
            "headers": len(header_paragraphs),
            "footers": len(footer_paragraphs),
            "run_style_counts": dict(sorted(run_style_counts.items())),
            "superscript_runs": run_style_counts.get("sup", 0),
            "subscript_runs": run_style_counts.get("sub", 0),
        }


def safe_docx2python_attr(doc: Any, attr_name: str, default: Any) -> Any:
    try:
        return getattr(doc, attr_name)
    except Exception:
        return default


def likely_parser_misses(
    our_summary: dict[str, Any],
    docx2python_summary: dict[str, Any],
) -> list[str]:
    checks = [
        ("body text paragraph", "body_text_blocks", "body_text_paragraphs"),
        ("table", "tables", "tables"),
        ("image", "images", "images"),
        ("footnote", "footnotes", "footnotes"),
        ("endnote", "endnotes", "endnotes"),
        ("comment", "comments", "comments"),
        ("header", "headers", "headers"),
        ("footer", "footers", "footers"),
    ]
    misses: list[str] = []
    for label, our_key, reference_key in checks:
        our_count = int(our_summary.get(our_key, 0))
        reference_count = int(docx2python_summary.get(reference_key, 0))
        if reference_count > our_count:
            misses.append(
                f"docx2python found {reference_count - our_count} more {label}(s)."
            )

    reference_inline = int(docx2python_summary.get("superscript_runs", 0)) + int(
        docx2python_summary.get("subscript_runs", 0)
    )
    our_inline = int(our_summary.get("inline_placeholders", 0))
    if reference_inline > our_inline:
        misses.append(
            "docx2python found more superscript/subscript runs than our protected "
            "inline placeholder layer."
        )
    return misses


def compare_table_shapes(
    our_shapes: list[dict[str, Any]],
    docx2python_shapes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for index, (our_shape, docx2python_shape) in enumerate(
        zip(our_shapes, docx2python_shapes, strict=False),
        start=1,
    ):
        if our_shape == docx2python_shape:
            continue
        differences.append(
            {
                "table_index": index,
                "our_shape": our_shape,
                "docx2python_shape": docx2python_shape,
                "note": (
                    "A difference can be normal when one parser reports physical "
                    "DOCX cells and another expands merged/grid-spanned cells into "
                    "visual columns."
                ),
            }
        )
    return differences


def _our_table_shape(block: DocumentBlock) -> dict[str, int | list[int]]:
    rows = block.metadata.get("rows")
    if not isinstance(rows, list):
        return {"rows": 0, "columns": 0, "row_shapes": []}
    row_shapes = [len(row) for row in rows if isinstance(row, list)]
    return {
        "rows": len(row_shapes),
        "columns": max(row_shapes, default=0),
        "row_shapes": row_shapes,
    }


def count_inline_placeholders(block: DocumentBlock) -> int:
    count = len(block.metadata.get("inline_placeholders", []) or [])
    row_metadata = block.metadata.get("row_metadata")
    if isinstance(row_metadata, list):
        for row in row_metadata:
            if not isinstance(row, dict):
                continue
            cells = row.get("cells")
            if not isinstance(cells, list):
                continue
            for cell in cells:
                if isinstance(cell, dict):
                    count += len(cell.get("inline_placeholders", []) or [])
    return count


def flatten_nested(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from flatten_nested(item)
    else:
        yield value


def is_docx2python_paragraph(value: Any) -> bool:
    return hasattr(value, "runs") and hasattr(value, "lineage")


def docx2python_paragraph_text(paragraph: Any) -> str:
    return "".join(str(run.text) for run in getattr(paragraph, "runs", [])).strip()


def docx2python_paragraph_is_in_table(paragraph: Any) -> bool:
    lineage = getattr(paragraph, "lineage", ())
    return len(lineage) > 1 and lineage[1] == "tbl"


def nonempty_docx2python_paragraphs(value: Any) -> list[Any]:
    return [
        par
        for par in flatten_nested(value)
        if is_docx2python_paragraph(par)
        and docx2python_paragraph_text(par)
        and not is_docx2python_note_separator(docx2python_paragraph_text(par))
    ]


def is_docx2python_media_placeholder(text: str) -> bool:
    stripped = text.strip()
    return bool(
        re.fullmatch(r"-{4}media/[^-]+-{4}", stripped)
        or re.fullmatch(r"-{4}Image alt text-{4}>.*<-{4}media/[^-]+-{4}", stripped)
    )


def is_docx2python_note_separator(text: str) -> bool:
    return bool(re.fullmatch(r"(?:footnote|endnote)-?\d+\)", text.strip(), re.IGNORECASE))


def docx2python_table_shape(table_structure: Any) -> dict[str, int | list[int]]:
    if not isinstance(table_structure, list):
        return {"rows": 0, "columns": 0, "row_shapes": []}
    row_shapes: list[int] = []
    for row in table_structure:
        if isinstance(row, list):
            row_shapes.append(len(row))
    return {
        "rows": len(row_shapes),
        "columns": max(row_shapes, default=0),
        "row_shapes": row_shapes,
    }


def docx2python_run_style_counts(value: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    for paragraph in flatten_nested(value):
        if not is_docx2python_paragraph(paragraph):
            continue
        for run in getattr(paragraph, "runs", []):
            for style in getattr(run, "html_style", []) or []:
                counts[str(style)] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare this project's DOCX parser against docx2python."
    )
    parser.add_argument("input", type=Path, help="DOCX file to inspect.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the full JSON comparison report.",
    )
    args = parser.parse_args()

    comparison = compare_docx_parsers(args.input)
    report = comparison.to_dict()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
