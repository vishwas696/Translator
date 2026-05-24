from __future__ import annotations

import json
import re
from textwrap import dedent
from typing import Mapping

from translator.documents.model import DocumentBlock, ParsedDocument
from translator.translation.prompts import (
    CONTENT_FORM_GUIDANCE,
    DOCUMENT_TYPE_GUIDANCE,
    REVIEWER_CONTENT_FORM_CHECKLISTS,
    REVIEWER_DOCUMENT_TYPE_CHECKLISTS,
    STATIC_REVIEW_BRIEF,
    STATIC_TRANSLATION_BRIEF,
)
from translator.translation.chunker import TranslationChunk


INLINE_PLACEHOLDER_PATTERN = re.compile(r"\[\[INLINE_\d{4}\]\]")

TABLE_TRANSLATION_RULES = """
Table rules:
- Treat table_rows as the only source of truth for table blocks.
- Return table_rows for every table block.
- Return exactly the same number of row arrays as the source table_rows.
- In each row, return exactly the same number of cell strings as the source row.
- Translate text inside each cell only; do not move text between cells.
- If one source cell contains multiple labels, phrases, or subitems, keep them
  together in that same target cell.
- Translate human-readable labels, classifier words, and explanatory phrases
  inside tables even when the table is dense or technical.
- Preserve compact code-like identifiers, slash-delimited parser markers,
  placeholders, field names, file paths, URLs, and schema/tag names exactly
  unless the source clearly presents them as ordinary readable prose.
- Do not split one source row into multiple rows.
- Do not merge multiple source rows into one row.
- Do not add header rows, notes, bullets, Markdown tables, or explanatory text.
- Preserve numbers, units, formulas, symbols, ranges, abbreviations, and IDs
  unless normal target-language localization is clearly required.
- If a source cell is empty, return an empty string for that same cell.
Any table_rows shape mismatch will be rejected and the source table will be preserved.
""".strip()

CONTENTS_TABLE_RULES = """
Contents/layout table rules:
- This table is used for visual layout, contents, or navigation.
- Do not return table_rows for layout tables.
- Translate the visible text and rebuild it as clean, readable contents text.
- Preserve page numbers, anchors, numbering, and item order.
- Do not force the original row/cell grid if it makes the target text messy.
""".strip()

INLINE_PLACEHOLDER_RULES = """
Inline placeholder rules:
- Copy every inline placeholder token exactly once.
- Do not translate, rename, delete, reorder, duplicate, or explain placeholder tokens.
- Translate human-readable text before, after, and between placeholder tokens.
- Do not preserve ordinary words merely because they are wrapped by placeholder
  tokens; the tokens protect formatting/objects, not the source-language words.
- Preserve code-like identifiers, URLs, paths, field codes, and schema/tag names
  when they are the text between placeholder tokens.
- Keep placeholder tokens next to the same words they originally marked.
""".strip()


def build_static_translation_brief(
    target_language: str,
    source_language: str = "English",
    document_type: str = "general",
    content_form: str = "book",
) -> str:
    content_guidance = CONTENT_FORM_GUIDANCE.get(
        content_form,
        CONTENT_FORM_GUIDANCE["book"],
    )
    type_guidance = DOCUMENT_TYPE_GUIDANCE.get(
        document_type,
        DOCUMENT_TYPE_GUIDANCE["general"],
    )
    return dedent(
        f"""
        Source language: {source_language}
        Target language: {target_language}
        Translation direction: {source_language} to {target_language}
        Content form: {content_form}
        Content-form guidance: {content_guidance}
        Document type: {document_type}
        Document-type guidance: {type_guidance}

        Core translation rules:
        Treat {source_language} as the primary source language for this document.
        If a short phrase appears in another language, handle it according to
        context without changing the document's main source-language direction.
        {STATIC_TRANSLATION_BRIEF}
        """
    ).strip()


def build_static_review_brief(
    target_language: str,
    source_language: str = "English",
    document_type: str = "general",
    content_form: str = "book",
) -> str:
    content_checklist = REVIEWER_CONTENT_FORM_CHECKLISTS.get(
        content_form,
        REVIEWER_CONTENT_FORM_CHECKLISTS["book"],
    )
    type_checklist = REVIEWER_DOCUMENT_TYPE_CHECKLISTS.get(
        document_type,
        REVIEWER_DOCUMENT_TYPE_CHECKLISTS["general"],
    )
    return dedent(
        f"""
        Review source language: {source_language}
        Review target language: {target_language}
        Review translation direction: {source_language} to {target_language}
        Content form: {content_form}
        Content-form review focus: {content_checklist}
        Document type: {document_type}
        Document-type review focus: {type_checklist}

        Core review rules:
        {STATIC_REVIEW_BRIEF}
        """
    ).strip()


def build_translation_prompt(
    target_language: str,
    brief: str,
    chunk: TranslationChunk,
    chunk_blocks: list[dict[str, object]],
    previous_chunks: list[dict[str, object]],
    glossary_entries: list[dict[str, object]],
    source_language: str = "English",
) -> str:
    has_data_table = any(
        block.get("block_type") == "table" and not is_layout_table_block(block)
        for block in chunk_blocks
    )
    has_layout_table = any(
        block.get("block_type") == "table" and is_layout_table_block(block)
        for block in chunk_blocks
    )
    has_inline_tokens = any(inline_tokens_in_block(block) for block in chunk_blocks)
    extra_rules = []
    if has_data_table:
        extra_rules.append(TABLE_TRANSLATION_RULES)
    if has_layout_table:
        extra_rules.append(CONTENTS_TABLE_RULES)
    if has_inline_tokens:
        extra_rules.append(INLINE_PLACEHOLDER_RULES)
    table_output_rule = ""
    if has_data_table:
        table_output_rule = (
            "For data table blocks, also include table_rows with the exact same "
            "shape as the source table_rows."
        )
    elif has_layout_table:
        table_output_rule = "For layout tables, do not include table_rows."

    return dedent(
        f"""
        Translate the current chunk from {source_language} into {target_language}.

        System instructions / translation rules:
        {brief}

        {chr(10).join(extra_rules)}

        Last translated context chunks:
        {json.dumps(compact_previous_chunks(previous_chunks), ensure_ascii=False, indent=2)}

        Glossary / fixed terms:
        {json.dumps(glossary_entries, ensure_ascii=False, indent=2)}

        Current chunk metadata:
        {json.dumps(chunk.to_dict(), ensure_ascii=False, indent=2)}

        Current document blocks:
        {json.dumps(chunk_blocks, ensure_ascii=False, indent=2)}

        Current chunk text:
        {chunk.text}

        Output rules:
        Return only valid JSON with this shape:
        {{
          "chunk_id": "{chunk.chunk_id}",
          "paragraph_ids": {json.dumps(chunk.paragraph_ids, ensure_ascii=False)},
          "translated_text": "...",
          "block_translations": [
            {{
              "block_id": "...",
              "paragraph_id": "...",
              "translated_text": "..."
            }}
          ],
          "notes": []
        }}
        Return one block_translations item for every current document block.
        {table_output_rule}
        Do not add Markdown fences, explanations, or prose outside the JSON.
        """
    ).strip()


def build_glossary_prompt(
    target_language: str,
    brief: str,
    chunk: TranslationChunk,
    translated_text: str,
    source_language: str = "English",
) -> str:
    return dedent(
        f"""
        Extract glossary entries from this source/translation pair.
        Source language: {source_language}
        Target language: {target_language}

        Keep only terms where consistency matters: names, places, organizations,
        invented terms, recurring phrases, technical terms, UI labels, legal terms,
        symbols with domain meaning, or terms whose translation should stay stable.
        Prefer keeping place names and named entities. Reject ordinary vocabulary.

        Return only JSON:
        {{
          "glossary": [
            {{
              "source_terms": ["..."],
              "target_terms": ["..."],
              "preferred_target": "...",
              "category": "person|place|organization|term|phrase|technical|other",
              "priority": "low|medium|high",
              "reason": "..."
            }}
          ],
          "rejected_terms": [
            {{"source_term": "...", "reason": "..."}}
          ]
        }}

        Brief:
        {brief}

        Source chunk:
        {chunk.text}

        Translation:
        {translated_text}
        """
    ).strip()


def build_review_prompt(
    target_language: str,
    review_brief: str | None = None,
    manuscript: str = "",
    translation: str = "",
    brief: str | None = None,
) -> str:
    checklist = review_brief if review_brief is not None else brief
    checklist = checklist or ""
    return dedent(
        f"""
        Review this translation into {target_language}.

        Reviewer checklist brief:
        {checklist}

        Source manuscript:
        {manuscript}

        Translation:
        {translation}
        """
    ).strip()


def build_revision_prompt(
    target_language: str,
    brief: str,
    chunk: TranslationChunk,
    chunk_blocks: list[dict[str, object]],
    first_translation: dict[str, object],
    reviewer_feedback: str,
    previous_revised_chunks: list[dict[str, object]],
    glossary_entries: list[dict[str, object]],
) -> str:
    has_data_table = any(
        block.get("block_type") == "table" and not is_layout_table_block(block)
        for block in chunk_blocks
    )
    has_layout_table = any(
        block.get("block_type") == "table" and is_layout_table_block(block)
        for block in chunk_blocks
    )
    has_inline_tokens = any(inline_tokens_in_block(block) for block in chunk_blocks)
    extra_rules = []
    if has_data_table:
        extra_rules.append(TABLE_TRANSLATION_RULES)
    if has_layout_table:
        extra_rules.append(CONTENTS_TABLE_RULES)
    if has_inline_tokens:
        extra_rules.append(INLINE_PLACEHOLDER_RULES)

    return dedent(
        f"""
        Revise this translated chunk into {target_language}.

        Apply only fixes supported by the original chunk and Reviewer feedback.
        Do not rewrite the whole translation unless needed for naturalness,
        terminology, formatting, or a concrete reviewer issue.
        For narrative or literary chunks, prefer natural target-language prose over
        literal source-language phrasing when reviewer feedback identifies weakened emotional force,
        symbolism, callbacks, implication, or character voice.

        Translation brief:
        {brief}

        {chr(10).join(extra_rules)}

        Reviewer feedback:
        {reviewer_feedback}

        Previous revised context chunks:
        {json.dumps(compact_previous_chunks(previous_revised_chunks), ensure_ascii=False, indent=2)}

        Glossary / fixed terms:
        {json.dumps(glossary_entries, ensure_ascii=False, indent=2)}

        Original chunk metadata:
        {json.dumps(chunk.to_dict(), ensure_ascii=False, indent=2)}

        Current document blocks:
        {json.dumps(chunk_blocks, ensure_ascii=False, indent=2)}

        Original chunk:
        {chunk.text}

        First-pass translation JSON:
        {json.dumps(first_translation, ensure_ascii=False, indent=2)}

        Output rules:
        Return only valid JSON with the same schema as the first-pass translation.
        Return one block_translations item for every current document block.
        Preserve block_id and paragraph_id values exactly.
        For data tables, preserve the exact table_rows shape. For layout tables,
        return readable translated text and do not return table_rows.
        """
    ).strip()


def paragraph_id_to_block_map(parsed_document: ParsedDocument) -> dict[str, DocumentBlock]:
    paragraph_blocks: dict[str, DocumentBlock] = {}
    index = 1
    for block in parsed_document.blocks:
        if not block.translate or not block.text.strip():
            continue
        paragraph_blocks[f"p{index:04d}"] = block
        index += 1
    return paragraph_blocks


def block_items_for_chunk(
    chunk: TranslationChunk,
    paragraph_blocks: dict[str, DocumentBlock],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for paragraph_id in chunk.paragraph_ids:
        block = paragraph_blocks.get(paragraph_id)
        if block is None:
            continue
        items.append(block_item_for_prompt(block, paragraph_id))
    return items


def block_item_for_prompt(block: DocumentBlock, paragraph_id: str) -> dict[str, object]:
    metadata = block.metadata or {}
    source_text = str(metadata.get("inline_source_text") or block.text)
    item: dict[str, object] = {
        "paragraph_id": paragraph_id,
        "block_id": block.block_id,
        "block_type": block.type,
        "source_text": source_text,
    }
    if block.level is not None:
        item["level"] = block.level

    inline_placeholders = metadata.get("inline_placeholders")
    if isinstance(inline_placeholders, list) and inline_placeholders:
        item["inline_placeholders"] = inline_placeholders

    if block.type == "table":
        raw_rows = metadata.get("translation_rows") or metadata.get("rows")
        rows = normalize_raw_rows(raw_rows)
        if rows:
            item["table_rows"] = rows
            item["table_shape"] = [len(row) for row in rows]
            item["source_text"] = render_table_rows(rows)
        table_role = metadata.get("table_role")
        if isinstance(table_role, str) and table_role:
            item["table_role"] = table_role
        if metadata.get("is_layout_table") is True:
            item["is_layout_table"] = True

    return item


def compact_previous_chunks(
    previous_chunks: list[dict[str, object]],
) -> list[dict[str, object]]:
    compact_chunks: list[dict[str, object]] = []
    for previous_chunk in previous_chunks:
        chunk = previous_chunk.get("chunk", {})
        translation = previous_chunk.get("translation", {})
        if not isinstance(chunk, dict) or not isinstance(translation, dict):
            continue
        compact_chunks.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "paragraph_ids": chunk.get("paragraph_ids"),
                "continues_paragraph": chunk.get("continues_paragraph"),
                "ends_paragraph": chunk.get("ends_paragraph"),
                "source_text": chunk.get("text"),
                "translated_text": translation.get("translated_text"),
            }
        )
    return compact_chunks


def parse_translation_output(
    output: str,
    chunk: TranslationChunk,
    chunk_blocks: list[dict[str, object]] | None = None,
    fallback_translations_by_block_id: Mapping[str, object] | None = None,
) -> dict[str, object]:
    chunk_blocks = chunk_blocks or []
    cleaned_output = output.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned_output, re.DOTALL)
    if fence_match:
        cleaned_output = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned_output)
    except json.JSONDecodeError:
        block_translations, notes = fallback_block_translations(
            output.strip(),
            chunk_blocks,
            fallback_translations_by_block_id=fallback_translations_by_block_id,
        )
        return {
            "chunk_id": chunk.chunk_id,
            "paragraph_ids": chunk.paragraph_ids,
            "translated_text": output.strip(),
            "block_translations": block_translations,
            "notes": [
                "Model output was not valid JSON; stored raw output as translation.",
                *notes,
            ],
        }

    if not isinstance(parsed, dict):
        block_translations, notes = fallback_block_translations(
            output.strip(),
            chunk_blocks,
            fallback_translations_by_block_id=fallback_translations_by_block_id,
        )
        return {
            "chunk_id": chunk.chunk_id,
            "paragraph_ids": chunk.paragraph_ids,
            "translated_text": output.strip(),
            "block_translations": block_translations,
            "notes": ["Model output JSON was not an object; stored raw output.", *notes],
        }

    parsed.setdefault("chunk_id", chunk.chunk_id)
    parsed.setdefault("paragraph_ids", chunk.paragraph_ids)
    parsed.setdefault("translated_text", "")
    parsed.setdefault("notes", [])
    if not isinstance(parsed["notes"], list):
        parsed["notes"] = [str(parsed["notes"])]
    parsed["block_translations"] = normalize_block_translations(
        parsed.get("block_translations"),
        translated_text=str(parsed.get("translated_text", "")),
        chunk_blocks=chunk_blocks,
        notes=parsed["notes"],
        fallback_translations_by_block_id=fallback_translations_by_block_id,
    )
    block_text = render_block_translations_text(parsed["block_translations"])
    if block_text:
        parsed["translated_text"] = block_text
    return parsed


def render_block_translations_text(block_translations: object) -> str:
    if not isinstance(block_translations, list):
        return ""
    parts: list[str] = []
    for item in block_translations:
        if not isinstance(item, dict):
            continue
        text = str(item.get("translated_text", "")).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def normalize_block_translations(
    raw_block_translations: object,
    translated_text: str,
    chunk_blocks: list[dict[str, object]],
    notes: list[object],
    fallback_translations_by_block_id: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    expected_blocks = [
        block for block in chunk_blocks if str(block.get("block_id", "")).strip()
    ]
    if not expected_blocks:
        return []

    raw_items = raw_block_translations if isinstance(raw_block_translations, list) else []
    block_by_id = {str(block["block_id"]): block for block in expected_blocks}
    translations_by_block_id: dict[str, dict[str, object]] = {}
    unknown_block_ids: list[str] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id", "")).strip()
        if not block_id:
            continue
        expected_block = block_by_id.get(block_id)
        if expected_block is None:
            unknown_block_ids.append(block_id)
            continue

        item_text = str(item.get("translated_text", "")).strip()
        table_rows = None
        if str(expected_block.get("block_type")) == "table":
            if is_layout_table_block(expected_block):
                if not item_text and isinstance(item.get("table_rows"), list):
                    item_text = render_table_rows_from_raw(item.get("table_rows"))
            else:
                table_rows = normalize_table_rows(
                    raw_rows=item.get("table_rows"),
                    expected_block=expected_block,
                    block_id=block_id,
                    notes=notes,
                )
                if table_rows is not None:
                    item_text = render_table_rows(table_rows)
                elif not table_text_matches_shape(
                    item_text,
                    expected_table_shape(expected_block),
                ):
                    fallback_text, fallback_rows = fallback_block_translation(
                        fallback_translations_by_block_id=fallback_translations_by_block_id,
                        block_id=block_id,
                        expected_block=expected_block,
                    )
                    if fallback_text:
                        item_text = fallback_text
                        table_rows = fallback_rows
                        notes.append(
                            f"Table block {block_id} used previous valid translation "
                            "because the revised table was not shape-safe."
                        )
                    else:
                        item_text = render_source_table(expected_block)
                        notes.append(
                            f"Table block {block_id} did not provide a shape-safe "
                            "translation; preserved the source table to avoid DOCX table "
                            "reconstruction damage."
                        )

        expected_tokens = inline_tokens_in_block(expected_block)
        if expected_tokens:
            missing_tokens = [token for token in expected_tokens if token not in item_text]
            unexpected_tokens = [
                token
                for token in INLINE_PLACEHOLDER_PATTERN.findall(item_text)
                if token not in expected_tokens
            ]
            if missing_tokens or unexpected_tokens:
                fallback_text, fallback_rows = fallback_block_translation(
                    fallback_translations_by_block_id=fallback_translations_by_block_id,
                    block_id=block_id,
                    expected_block=expected_block,
                )
                if missing_tokens:
                    notes.append(
                        f"Block {block_id} did not preserve inline placeholder token(s): "
                        + ", ".join(missing_tokens)
                    )
                if unexpected_tokens:
                    notes.append(
                        f"Block {block_id} returned unknown inline placeholder token(s): "
                        + ", ".join(unexpected_tokens)
                    )
                if fallback_text:
                    item_text = fallback_text
                    if fallback_rows is not None:
                        table_rows = fallback_rows
                    notes.append(
                        f"Block {block_id} used previous valid translation to avoid "
                        "reverting to source text."
                    )
                else:
                    item_text = render_source_block_text(expected_block)
                    notes.append(
                        f"Block {block_id} preserved source block text to avoid "
                        "damaging DOCX inline structure."
                    )

        if not item_text:
            continue
        normalized_item: dict[str, object] = {
            "block_id": block_id,
            "paragraph_id": str(item.get("paragraph_id", "")).strip()
            or str(expected_block.get("paragraph_id", "")),
            "translated_text": item_text,
        }
        if table_rows is not None:
            normalized_item["table_rows"] = table_rows
        translations_by_block_id[block_id] = normalized_item

    normalized: list[dict[str, object]] = []
    missing_blocks: list[dict[str, object]] = []
    for block in expected_blocks:
        block_id = str(block["block_id"])
        item = translations_by_block_id.get(block_id)
        if item is None:
            missing_blocks.append(block)
            continue
        normalized.append(item)

    if unknown_block_ids:
        notes.append(
            "Ignored block_translations with unknown block_id values: "
            + ", ".join(sorted(unknown_block_ids))
        )

    if missing_blocks:
        fallback_items, fallback_notes = fallback_missing_block_translations(
            translated_text=translated_text,
            expected_blocks=expected_blocks,
            missing_blocks=missing_blocks,
            fallback_translations_by_block_id=fallback_translations_by_block_id,
        )
        normalized.extend(fallback_items)
        notes.extend(fallback_notes)

    order = {str(block["block_id"]): index for index, block in enumerate(expected_blocks)}
    normalized.sort(key=lambda item: order.get(str(item["block_id"]), len(order)))
    return normalized


def fallback_block_translation(
    fallback_translations_by_block_id: Mapping[str, object] | None,
    block_id: str,
    expected_block: dict[str, object],
) -> tuple[str, list[list[str]] | None]:
    if not fallback_translations_by_block_id:
        return "", None
    fallback_value = fallback_translations_by_block_id.get(block_id)
    fallback_rows = None
    if isinstance(fallback_value, Mapping):
        fallback_rows = normalize_raw_rows(fallback_value.get("table_rows"))
        fallback_text = str(fallback_value.get("translated_text", "")).strip()
        if fallback_rows and not fallback_text:
            fallback_text = render_table_rows(fallback_rows)
    else:
        fallback_text = str(fallback_value or "").strip()

    if not fallback_text:
        return "", None
    if str(expected_block.get("block_type")) == "table" and not is_layout_table_block(
        expected_block
    ):
        expected_shape = expected_table_shape(expected_block)
        if fallback_rows:
            if [len(row) for row in fallback_rows] != expected_shape:
                return "", None
            fallback_text = render_table_rows(fallback_rows)
        elif not table_text_matches_shape(fallback_text, expected_shape):
            return "", None

    expected_tokens = inline_tokens_in_block(expected_block)
    missing_tokens = [token for token in expected_tokens if token not in fallback_text]
    unexpected_tokens = [
        token
        for token in INLINE_PLACEHOLDER_PATTERN.findall(fallback_text)
        if token not in expected_tokens
    ]
    if missing_tokens or unexpected_tokens:
        return "", None
    return fallback_text, fallback_rows or None


def normalize_table_rows(
    raw_rows: object,
    expected_block: dict[str, object],
    block_id: str,
    notes: list[object],
) -> list[list[str]] | None:
    rows = normalize_raw_rows(raw_rows)
    if not rows and raw_rows != []:
        notes.append(
            f"Table block {block_id} did not include table_rows; using translated_text."
        )
        return None
    expected_shape = expected_table_shape(expected_block)
    actual_shape = [len(row) for row in rows]
    if actual_shape != expected_shape:
        notes.append(
            f"Table block {block_id} returned table_rows shape {actual_shape}; "
            f"expected {expected_shape}. Using translated_text."
        )
        return None
    return rows


def normalize_raw_rows(raw_rows: object) -> list[list[str]]:
    if not isinstance(raw_rows, list):
        return []
    rows: list[list[str]] = []
    for row in raw_rows:
        if not isinstance(row, list):
            return []
        rows.append([str(cell).strip() for cell in row])
    return rows


def expected_table_shape(block: dict[str, object]) -> list[int]:
    raw_shape = block.get("table_shape")
    if isinstance(raw_shape, list):
        return [int(value) for value in raw_shape if isinstance(value, int)]
    rows = normalize_raw_rows(block.get("table_rows"))
    return [len(row) for row in rows]


def render_table_rows(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in rows).strip()


def render_table_rows_from_raw(raw_rows: object) -> str:
    return render_table_rows(normalize_raw_rows(raw_rows))


def table_text_matches_shape(text: str, expected_shape: list[int]) -> bool:
    if not text.strip() or not expected_shape:
        return False
    rows = [
        row
        for row in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if row.strip()
    ]
    return [len(row.split("\t")) for row in rows] == expected_shape


def render_source_table(block: dict[str, object]) -> str:
    rows = normalize_raw_rows(block.get("table_rows"))
    if rows:
        return render_table_rows(rows)
    return str(block.get("source_text", "")).strip()


def render_source_block_text(block: dict[str, object]) -> str:
    if str(block.get("block_type")) == "table":
        return render_source_table(block)
    return str(block.get("source_text", "")).strip()


def fallback_missing_block_translations(
    translated_text: str,
    expected_blocks: list[dict[str, object]],
    missing_blocks: list[dict[str, object]],
    fallback_translations_by_block_id: Mapping[str, object] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    units = [
        unit.strip()
        for unit in re.split(r"\n\s*\n", translated_text.strip())
        if unit.strip()
    ]
    if len(expected_blocks) == 1 and translated_text.strip():
        units = [translated_text.strip()]

    notes = [
        "Some block_translations were missing; filled missing blocks from "
        "translated_text by blank-line order where possible."
    ]
    expected_index_by_block_id = {
        str(block["block_id"]): index
        for index, block in enumerate(expected_blocks)
        if str(block.get("block_id", "")).strip()
    }
    fallback_items: list[dict[str, object]] = []
    for block in missing_blocks:
        block_id = str(block["block_id"])
        unit_index = expected_index_by_block_id.get(block_id)
        fallback_text, fallback_rows = fallback_block_translation(
            fallback_translations_by_block_id=fallback_translations_by_block_id,
            block_id=block_id,
            expected_block=block,
        )
        unit = ""
        table_rows = None
        if unit_index is not None and unit_index < len(units):
            unit = units[unit_index]
        elif fallback_text:
            unit = fallback_text
            table_rows = fallback_rows
            notes.append(
                f"Block {block_id} was missing in revised output; used previous "
                "valid translation."
            )
        else:
            continue
        if str(block.get("block_type")) == "table":
            if is_layout_table_block(block):
                pass
            elif not table_text_matches_shape(unit, expected_table_shape(block)):
                if fallback_text:
                    unit = fallback_text
                    table_rows = fallback_rows
                    notes.append(
                        f"Table block {block_id} used previous valid translation "
                        "because fallback text was not shape-safe."
                    )
                else:
                    unit = render_source_table(block)
                    notes.append(
                        f"Table block {block_id} fallback text was not shape-safe; "
                        "preserved the source table."
                    )
        fallback_item: dict[str, object] = {
            "block_id": block_id,
            "paragraph_id": str(block.get("paragraph_id", "")),
            "translated_text": unit,
        }
        if table_rows is not None:
            fallback_item["table_rows"] = table_rows
        fallback_items.append(fallback_item)
    return fallback_items, notes


def fallback_block_translations(
    translated_text: str,
    chunk_blocks: list[dict[str, object]],
    fallback_translations_by_block_id: Mapping[str, object] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    chunk_blocks = [
        block for block in chunk_blocks if str(block.get("block_id", "")).strip()
    ]
    if not chunk_blocks:
        return [], ["No current document blocks were available for block-level alignment."]

    units = [
        unit.strip()
        for unit in re.split(r"\n\s*\n", translated_text.strip())
        if unit.strip()
    ]
    notes: list[str] = []
    if len(chunk_blocks) == 1:
        units = [translated_text.strip()] if translated_text.strip() else []
    elif len(units) != len(chunk_blocks):
        notes.append(
            "Model did not return usable block_translations; aligned translated_text "
            "to document blocks by blank-line order where possible."
        )

    block_translations: list[dict[str, object]] = []
    for index, block in enumerate(chunk_blocks):
        block_id = str(block["block_id"])
        fallback_text, fallback_rows = fallback_block_translation(
            fallback_translations_by_block_id=fallback_translations_by_block_id,
            block_id=block_id,
            expected_block=block,
        )
        if index >= len(units):
            if fallback_text:
                unit = fallback_text
                table_rows = fallback_rows
                notes.append(
                    f"Block {block_id} used previous valid translation because "
                    "fallback output did not contain a matching text unit."
                )
            else:
                break
        else:
            unit = units[index]
            table_rows = None
        if str(block.get("block_type")) == "table":
            if is_layout_table_block(block):
                pass
            elif not table_text_matches_shape(unit, expected_table_shape(block)):
                if fallback_text:
                    unit = fallback_text
                    table_rows = fallback_rows
                    notes.append(
                        f"Table block {block_id} used previous valid translation "
                        "because fallback text was not shape-safe."
                    )
                else:
                    unit = render_source_table(block)
                    notes.append(
                        f"Table block {block['block_id']} fallback text was not "
                        "shape-safe; preserved the source table."
                    )
        fallback_item: dict[str, object] = {
            "block_id": block_id,
            "paragraph_id": str(block.get("paragraph_id", "")),
            "translated_text": unit,
        }
        if table_rows is not None:
            fallback_item["table_rows"] = table_rows
        block_translations.append(fallback_item)
    return block_translations, notes


def collect_block_translations(
    translated_chunks: list[dict[str, object]],
) -> dict[str, str]:
    translations_by_block_id: dict[str, str] = {}
    for translated_chunk in translated_chunks:
        translation = translated_chunk.get("translation", {})
        if not isinstance(translation, dict):
            continue
        block_translations = translation.get("block_translations", [])
        if not isinstance(block_translations, list):
            continue
        for item in block_translations:
            if not isinstance(item, dict):
                continue
            block_id = str(item.get("block_id", "")).strip()
            translated_text = str(item.get("translated_text", "")).strip()
            if not block_id or not translated_text:
                continue
            if block_id in translations_by_block_id:
                translations_by_block_id[block_id] = (
                    f"{translations_by_block_id[block_id].rstrip()} "
                    f"{translated_text.lstrip()}"
                )
            else:
                translations_by_block_id[block_id] = translated_text
    return translations_by_block_id


def collect_block_translation_payloads(
    translated_chunks: list[dict[str, object]],
) -> dict[str, object]:
    translations_by_block_id: dict[str, object] = {}
    for translated_chunk in translated_chunks:
        translation = translated_chunk.get("translation", {})
        if not isinstance(translation, dict):
            continue
        block_translations = translation.get("block_translations", [])
        if not isinstance(block_translations, list):
            continue
        for item in block_translations:
            if not isinstance(item, dict):
                continue
            block_id = str(item.get("block_id", "")).strip()
            translated_text = str(item.get("translated_text", "")).strip()
            if not block_id or not translated_text:
                continue

            table_rows = item.get("table_rows")
            if isinstance(table_rows, list):
                translations_by_block_id[block_id] = {
                    "translated_text": translated_text,
                    "table_rows": table_rows,
                }
                continue

            if block_id in translations_by_block_id:
                existing = translations_by_block_id[block_id]
                existing_text = (
                    str(existing.get("translated_text", "")).strip()
                    if isinstance(existing, dict)
                    else str(existing).strip()
                )
                translations_by_block_id[block_id] = (
                    f"{existing_text.rstrip()} {translated_text.lstrip()}"
                )
            else:
                translations_by_block_id[block_id] = translated_text
    return translations_by_block_id


def reconstruct_translation(translated_chunks: list[dict[str, object]]) -> str:
    translated_texts: list[str] = []
    for translated_chunk in translated_chunks:
        chunk = translated_chunk.get("chunk", {})
        translation = translated_chunk.get("translation", {})
        text = ""
        if isinstance(translation, dict):
            text = str(translation.get("translated_text", "")).strip()
        if not text:
            continue
        continues_paragraph = (
            isinstance(chunk, dict)
            and bool(chunk.get("continues_paragraph"))
            and not bool(chunk.get("starts_paragraph"))
        )
        if translated_texts and continues_paragraph:
            translated_texts[-1] = f"{translated_texts[-1].rstrip()} {text.lstrip()}"
        else:
            translated_texts.append(text)
    return "\n\n".join(translated_texts)


def is_layout_table_block(block: dict[str, object]) -> bool:
    return (
        str(block.get("table_role", "")).lower() in {"toc_layout", "layout", "contents"}
        or bool(block.get("is_layout_table"))
    )


def inline_tokens_in_block(block: dict[str, object]) -> list[str]:
    tokens: list[str] = []
    placeholders = block.get("inline_placeholders")
    if isinstance(placeholders, list):
        for placeholder in placeholders:
            if isinstance(placeholder, dict):
                token = str(placeholder.get("token", "")).strip()
                if token:
                    tokens.append(token)
    for key in ("source_text", "translated_text"):
        value = block.get(key)
        if isinstance(value, str):
            tokens.extend(INLINE_PLACEHOLDER_PATTERN.findall(value))
    rows = block.get("table_rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                for cell in row:
                    tokens.extend(INLINE_PLACEHOLDER_PATTERN.findall(str(cell)))
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique
