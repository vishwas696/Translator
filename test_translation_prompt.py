import unittest

import book_translation_agents as workflow
from translation_chunker import TranslationChunk

from book_translation_agents import (
    build_review_prompt,
    build_static_review_brief,
    build_translation_prompt,
    build_revision_prompt,
    build_static_translation_brief,
    collect_block_translations,
    compact_error_message,
    is_retryable_model_error,
    parse_translation_output,
)
from prompt_guidance import (
    CONTENT_FORM_GUIDANCE,
    DOCUMENT_TYPE_GUIDANCE,
    REVIEWER_CONTENT_FORM_CHECKLISTS,
    REVIEWER_DOCUMENT_TYPE_CHECKLISTS,
)


class TranslationPromptTests(unittest.TestCase):
    def test_static_brief_includes_document_type_guidance(self) -> None:
        brief = build_static_translation_brief(
            target_language="English",
            document_type="technical",
        )

        self.assertIn("Target language: English", brief)
        self.assertIn("Source language: English", brief)
        self.assertIn("Translation direction: English to English", brief)
        self.assertIn("Content form: book", brief)
        self.assertIn("Document type: technical", brief)
        self.assertIn("Treat this as a book or manuscript", brief)
        self.assertIn("Prioritize precision", brief)
        self.assertIn("Do not translate word-by-word", brief)
        self.assertIn("exact meaning, tone, emphasis, facts, and formatting", brief)
        self.assertIn("Soft hyphens", brief)
        self.assertIn("layout artifacts", brief)
        self.assertIn("Do not add parenthetical translations", brief)
        self.assertIn("book", CONTENT_FORM_GUIDANCE)
        self.assertIn("technical", DOCUMENT_TYPE_GUIDANCE)

    def test_static_brief_includes_selected_content_form_guidance(self) -> None:
        brief = build_static_translation_brief(
            target_language="Hindi",
            content_form="report",
            document_type="business_report",
        )

        self.assertIn("Content form: report", brief)
        self.assertIn("Treat this as a formal report", brief)
        self.assertIn("Document type: business_report", brief)
        self.assertIn("executive-summary logic", brief)

    def test_static_brief_includes_short_story_emotional_guidance(self) -> None:
        brief = build_static_translation_brief(
            target_language="German",
            content_form="book",
            document_type="short_story",
        )

        self.assertIn("recurring motifs", brief)
        self.assertIn("object symbolism", brief)
        self.assertIn("emotional callbacks", brief)
        self.assertIn("emotional progression", brief)
        self.assertIn("recurring symbols", brief)
        self.assertIn("context-sensitive callbacks", brief)
        self.assertIn("idiomatic literary target-language prose", brief)
        self.assertIn("literal phrasing", brief)

    def test_static_review_brief_includes_category_specific_checklist(self) -> None:
        brief = build_static_review_brief(
            target_language="German",
            content_form="book",
            document_type="short_story",
        )

        self.assertIn("Review target language: German", brief)
        self.assertIn("Review source language: English", brief)
        self.assertIn("Content-form review focus", brief)
        self.assertIn("Document-type review focus", brief)
        self.assertIn("long-form continuity", brief)
        self.assertIn("emotional continuity", brief)
        self.assertIn("recurring symbols", brief)
        self.assertIn("literal or calque phrasing", brief)
        self.assertIn("sounds translated", brief)
        self.assertIn("Core review rules", brief)
        self.assertIn("[[INLINE_0001]]", brief)
        self.assertIn("protected internal DOCX/EPUB structure", brief)
        self.assertIn("Do not request that they be", brief)
        self.assertIn("soft hyphens", brief)
        self.assertIn("book", REVIEWER_CONTENT_FORM_CHECKLISTS)
        self.assertIn("short_story", REVIEWER_DOCUMENT_TYPE_CHECKLISTS)

    def test_static_review_brief_is_separate_from_translation_brief(self) -> None:
        brief = build_static_review_brief(
            target_language="German",
            content_form="legal_or_policy",
            document_type="contract",
        )

        self.assertIn("Review target language: German", brief)
        self.assertIn("defined terms", brief)
        self.assertIn("legal force", brief)
        self.assertIn("defined-term boilerplate", brief)
        self.assertIn("signature/execution headings", brief)
        self.assertNotIn("Core translation rules", brief)
        self.assertNotIn("Do not translate word-by-word", brief)

    def test_unknown_content_form_falls_back_to_book_guidance(self) -> None:
        brief = build_static_translation_brief(
            target_language="English",
            content_form="unknown",
            document_type="general",
        )

        self.assertIn("Content form: unknown", brief)
        self.assertIn("Treat this as a book or manuscript", brief)

    def test_unknown_document_type_falls_back_to_general_guidance(self) -> None:
        brief = build_static_translation_brief(
            target_language="English",
            document_type="unknown",
        )

        self.assertIn("Document type: unknown", brief)
        self.assertIn("natural, faithful translation style", brief)

    def test_retryable_model_error_detection(self) -> None:
        self.assertTrue(
            is_retryable_model_error(
                RuntimeError(
                    "APIConnectionError: getaddrinfo failed for "
                    "generativelanguage.googleapis.com"
                )
            )
        )
        self.assertTrue(
            is_retryable_model_error(RuntimeError("503 service unavailable"))
        )
        self.assertFalse(
            is_retryable_model_error(RuntimeError("404 model not found"))
        )
        self.assertFalse(
            is_retryable_model_error(RuntimeError("quota exceeded for this API key"))
        )

    def test_compact_error_message_shortens_long_errors(self) -> None:
        message = compact_error_message(RuntimeError("x" * 400), max_length=80)

        self.assertLessEqual(len(message), 80)
        self.assertTrue(message.endswith("..."))

    def test_revision_prompt_includes_review_feedback_and_first_translation(self) -> None:
        chunk = _chunk()
        prompt = build_revision_prompt(
            target_language="Hindi",
            brief="Preserve formulas.",
            chunk=chunk,
            chunk_blocks=[
                {"paragraph_id": "p0001", "block_id": "b0001", "block_type": "paragraph"},
                {"paragraph_id": "p0002", "block_id": "b0002", "block_type": "paragraph"},
            ],
            first_translation={
                "chunk_id": "chunk_0001",
                "translated_text": "एक.\n\nदो.",
                "block_translations": [
                    {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "एक."},
                    {"block_id": "b0002", "paragraph_id": "p0002", "translated_text": "दो."},
                ],
            },
            reviewer_feedback="Use a more formal technical verb.",
            previous_revised_chunks=[],
            glossary_entries=[],
        )

        self.assertIn("Reviewer feedback", prompt)
        self.assertIn("Use a more formal technical verb.", prompt)
        self.assertIn("First-pass translation JSON", prompt)
        self.assertIn("एक.", prompt)
        self.assertIn("one block_translations item", prompt)
        self.assertIn('"chunk_id": "chunk_0001"', prompt)
        self.assertIn("natural target-language prose", prompt)
        self.assertIn("weakened emotional force", prompt)
        self.assertIn("symbolism", prompt)
        self.assertIn("callbacks", prompt)
        self.assertIn("character voice", prompt)

    def test_review_prompt_checks_untranslated_soft_hyphen_words(self) -> None:
        review_brief = build_static_review_brief(
            target_language="Japanese",
            content_form="manual_or_documentation",
            document_type="technical",
        )
        prompt = build_review_prompt(
            target_language="Japanese",
            review_brief=review_brief,
            manuscript="Column paragraph contains soft hyphen micro\u00adservice.",
            translation="列の段落には soft hyphen micro\u00adservice が含まれます。",
        )

        self.assertIn("Reviewer checklist brief", prompt)
        self.assertNotIn("Translation brief:", prompt)
        self.assertIn("protected internal DOCX/EPUB structure", prompt)
        self.assertIn("Do not request that they be", prompt)
        self.assertIn("visible header/footer labels", prompt)
        self.assertIn("repeated running heads", prompt)
        self.assertIn("translatable content", prompt)
        self.assertIn("Do not mark a footer as acceptable merely because", prompt)
        self.assertIn("surrounding visible words must also be translated", prompt)
        self.assertIn("page-number footers", prompt)
        self.assertIn("ordinary source-language words", prompt)
        self.assertIn("Normalize layout artifacts inside words", prompt)
        self.assertIn("soft hyphens", prompt)
        self.assertIn("zero-width characters", prompt)
        self.assertIn("tables, headers, footers", prompt)
        self.assertIn("audit every visible cell", prompt)
        self.assertIn("short labels", prompt)
        self.assertIn("dense technical tables", prompt)
        self.assertIn("human-readable classifier words", prompt)
        self.assertIn("identifiers, but flag readable source-language labels", prompt)
        self.assertIn("column", prompt)
        self.assertIn("keep", prompt)
        self.assertIn("quoted, borrowed, or foreign-language text", prompt)
        self.assertIn("translate the explanatory phrase", prompt)
        self.assertIn("should not keep the source-language explanation", prompt)
        self.assertIn("flag added parenthetical target-language explanations", prompt)
        self.assertIn("UI labels and form labels", prompt)
        self.assertIn("List each missed word or phrase separately", prompt)
        self.assertIn("suggested target-language replacement", prompt)
        self.assertNotIn("micro\u00adservice should be reviewed as microservice", prompt)
        self.assertNotIn("Status, Description", prompt)
        self.assertNotIn("Je t'aime", prompt)

    def test_translation_prompt_has_strict_table_shape_rules(self) -> None:
        prompt = build_translation_prompt(
            target_language="Hindi",
            brief="Translate naturally.",
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "source_text": "Cell Type\tRange\nWhite cells\t4.8-10.8",
                    "table_rows": [["Cell Type", "Range"], ["White cells", "4.8-10.8"]],
                    "table_shape": [2, 2],
                }
            ],
            previous_chunks=[],
            glossary_entries=[],
        )

        self.assertIn("Return exactly the same number of row arrays", prompt)
        self.assertIn("from English into Hindi", prompt)
        self.assertIn("return exactly the same number of cell strings", prompt)
        self.assertIn("If one source cell contains multiple labels", prompt)
        self.assertIn("together in that same target cell", prompt)
        self.assertIn("Translate human-readable labels", prompt)
        self.assertIn("Preserve compact code-like identifiers", prompt)
        self.assertIn("Any table_rows shape mismatch will be rejected", prompt)

    def test_translation_prompt_omits_table_rules_when_chunk_has_no_table(self) -> None:
        prompt = build_translation_prompt(
            target_language="Hindi",
            brief="Translate naturally.",
            chunk=_chunk(),
            chunk_blocks=[
                {"paragraph_id": "p0001", "block_id": "b0001", "block_type": "paragraph"},
                {"paragraph_id": "p0002", "block_id": "b0002", "block_type": "paragraph"},
            ],
            previous_chunks=[],
            glossary_entries=[],
        )

        self.assertNotIn("Table rules:", prompt)
        self.assertNotIn("table_rows", prompt)
        self.assertNotIn("Any table_rows shape mismatch will be rejected", prompt)

    def test_translation_prompt_preserves_inline_placeholder_tokens(self) -> None:
        prompt = build_translation_prompt(
            target_language="German",
            brief="Translate naturally.",
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "paragraph",
                    "source_text": "Overall Adjusted R[[INLINE_0001]] = 0.45",
                    "inline_placeholders": [
                        {
                            "token": "[[INLINE_0001]]",
                            "text": "2",
                            "display_text": "²",
                            "kind": "superscript",
                        }
                    ],
                }
            ],
            previous_chunks=[],
            glossary_entries=[],
        )

        self.assertIn("Inline placeholder rules", prompt)
        self.assertIn("[[INLINE_0001]]", prompt)
        self.assertIn("Copy every inline placeholder token exactly once", prompt)
        self.assertIn("Do not translate, rename, delete, reorder", prompt)
        self.assertIn("Translate human-readable text before, after, and between", prompt)
        self.assertIn("not preserve ordinary words merely because", prompt)

    def test_translation_prompt_allows_contents_layout_table_rebuild(self) -> None:
        prompt = build_translation_prompt(
            target_language="Hindi",
            brief="Translate naturally.",
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "table_role": "toc_layout",
                    "source_text": "CHAPTER CONTENTS\t\nNormal, 1; Cells, 2",
                    "table_rows": [["CHAPTER CONTENTS", ""], ["Normal, 1", "Cells, 2"]],
                    "table_shape": [2, 2],
                }
            ],
            previous_chunks=[],
            glossary_entries=[],
        )

        self.assertIn("Contents/layout table rules", prompt)
        self.assertIn("rebuild it as clean, readable contents text", prompt)
        self.assertIn("Do not return table_rows for layout tables", prompt)
        self.assertNotIn("Any table_rows shape mismatch will be rejected", prompt)

    def test_parse_translation_output_accepts_rebuilt_contents_layout_table(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "अध्याय सामग्री\\nसामान्य, 1\\nकोशिकाएं, 2",
              "block_translations": [
                {
                  "block_id": "b0001",
                  "paragraph_id": "p0001",
                  "translated_text": "अध्याय सामग्री\\nसामान्य, 1\\nकोशिकाएं, 2"
                }
              ]
            }
            """,
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "table_role": "toc_layout",
                    "source_text": "CHAPTER CONTENTS\t\nNormal, 1; Cells, 2",
                    "table_rows": [["CHAPTER CONTENTS", ""], ["Normal, 1", "Cells, 2"]],
                    "table_shape": [2, 2],
                }
            ],
        )

        self.assertEqual(
            parsed["block_translations"][0]["translated_text"],
            "अध्याय सामग्री\nसामान्य, 1\nकोशिकाएं, 2",
        )
        self.assertEqual(parsed["notes"], [])

    def test_parse_translation_output_normalizes_block_translations(self) -> None:
        chunk = _chunk()
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "paragraph_ids": ["p0001", "p0002"],
              "translated_text": "Uno.\\n\\nDos.",
              "block_translations": [
                {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "Uno."},
                {"block_id": "unknown", "translated_text": "Ignored"}
              ]
            }
            """,
            chunk=chunk,
            chunk_blocks=[
                {"paragraph_id": "p0001", "block_id": "b0001", "block_type": "paragraph"},
                {"paragraph_id": "p0002", "block_id": "b0002", "block_type": "paragraph"},
            ],
        )

        self.assertEqual(
            parsed["block_translations"],
            [
                {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "Uno."},
                {"block_id": "b0002", "paragraph_id": "p0002", "translated_text": "Dos."},
            ],
        )
        self.assertTrue(
            any("unknown block_id" in note for note in parsed["notes"])
        )
        self.assertEqual(parsed["translated_text"], "Uno.\n\nDos.")

    def test_parse_translation_output_rebuilds_text_from_block_translations(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "Incomplete summary only.",
              "block_translations": [
                {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "First full block."},
                {"block_id": "b0002", "paragraph_id": "p0002", "translated_text": "Second full block."}
              ]
            }
            """,
            chunk=_chunk(),
            chunk_blocks=[
                {"paragraph_id": "p0001", "block_id": "b0001", "block_type": "paragraph"},
                {"paragraph_id": "p0002", "block_id": "b0002", "block_type": "paragraph"},
            ],
        )

        self.assertEqual(
            parsed["translated_text"],
            "First full block.\n\nSecond full block.",
        )

    def test_parse_translation_output_falls_back_when_json_is_invalid(self) -> None:
        parsed = parse_translation_output(
            output="Uno.\n\nDos.",
            chunk=_chunk(),
            chunk_blocks=[
                {"paragraph_id": "p0001", "block_id": "b0001", "block_type": "paragraph"},
                {"paragraph_id": "p0002", "block_id": "b0002", "block_type": "paragraph"},
            ],
        )

        self.assertEqual(
            parsed["block_translations"],
            [
                {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "Uno."},
                {"block_id": "b0002", "paragraph_id": "p0002", "translated_text": "Dos."},
            ],
        )

    def test_parse_translation_output_accepts_shape_safe_table_rows(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "Producto\\tPuntuacion\\nAlfa\\t1",
              "block_translations": [
                {
                  "block_id": "b0001",
                  "paragraph_id": "p0001",
                  "translated_text": "ignored in favor of table_rows",
                  "table_rows": [["Producto", "Puntuacion"], ["Alfa", "1"]]
                }
              ]
            }
            """,
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "source_text": "Product\tScore\nAlpha\t1",
                    "table_rows": [["Product", "Score"], ["Alpha", "1"]],
                    "table_shape": [2, 2],
                }
            ],
        )

        self.assertEqual(
            parsed["block_translations"],
            [
                {
                    "block_id": "b0001",
                    "paragraph_id": "p0001",
                    "translated_text": "Producto\tPuntuacion\nAlfa\t1",
                    "table_rows": [["Producto", "Puntuacion"], ["Alfa", "1"]],
                }
            ],
        )

    def test_parse_translation_output_preserves_source_table_when_shape_is_bad(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "| Producto | Puntuacion |",
              "block_translations": [
                {
                  "block_id": "b0001",
                  "paragraph_id": "p0001",
                  "translated_text": "| Producto | Puntuacion |",
                  "table_rows": [["Producto", "Puntuacion", "Extra"]]
                }
              ]
            }
            """,
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "source_text": "Product\tScore\nAlpha\t1",
                    "table_rows": [["Product", "Score"], ["Alpha", "1"]],
                    "table_shape": [2, 2],
                }
            ],
        )

        self.assertEqual(
            parsed["block_translations"][0]["translated_text"],
            "Product\tScore\nAlpha\t1",
        )
        self.assertTrue(
            any("shape-safe translation" in note for note in parsed["notes"])
        )

    def test_revision_parse_uses_first_pass_when_table_shape_is_bad(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "| Produto | Pontuacao |",
              "block_translations": [
                {
                  "block_id": "b0001",
                  "paragraph_id": "p0001",
                  "translated_text": "| Produto | Pontuacao |",
                  "table_rows": [["Produto", "Pontuacao", "Extra"]]
                }
              ]
            }
            """,
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "source_text": "Product\tScore\nAlpha\t1",
                    "table_rows": [["Product", "Score"], ["Alpha", "1"]],
                    "table_shape": [2, 2],
                }
            ],
            fallback_translations_by_block_id={
                "b0001": {
                    "block_id": "b0001",
                    "paragraph_id": "p0001",
                    "translated_text": "Producto\tPuntuacion\nAlfa\t1",
                    "table_rows": [["Producto", "Puntuacion"], ["Alfa", "1"]],
                }
            },
        )

        self.assertEqual(
            parsed["block_translations"][0]["translated_text"],
            "Producto\tPuntuacion\nAlfa\t1",
        )
        self.assertEqual(
            parsed["block_translations"][0]["table_rows"],
            [["Producto", "Puntuacion"], ["Alfa", "1"]],
        )
        self.assertTrue(
            any("previous valid translation" in note for note in parsed["notes"])
        )

    def test_revision_parse_uses_first_pass_when_inline_tokens_are_missing(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "Revised without token.",
              "block_translations": [
                {
                  "block_id": "b0001",
                  "paragraph_id": "p0001",
                  "translated_text": "Revised without token."
                }
              ]
            }
            """,
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "paragraph",
                    "source_text": "Original [[INLINE_0001]]source.",
                    "inline_placeholders": [{"token": "[[INLINE_0001]]"}],
                }
            ],
            fallback_translations_by_block_id={
                "b0001": {
                    "block_id": "b0001",
                    "paragraph_id": "p0001",
                    "translated_text": "First pass [[INLINE_0001]]translation.",
                }
            },
        )

        self.assertEqual(
            parsed["block_translations"][0]["translated_text"],
            "First pass [[INLINE_0001]]translation.",
        )
        self.assertTrue(
            any("previous valid translation" in note for note in parsed["notes"])
        )

    def test_revision_parse_uses_first_pass_when_block_is_missing(self) -> None:
        parsed = parse_translation_output(
            output="""
            {
              "chunk_id": "chunk_0001",
              "translated_text": "Uno.",
              "block_translations": [
                {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "Uno."}
              ]
            }
            """,
            chunk=_chunk(),
            chunk_blocks=[
                {"paragraph_id": "p0001", "block_id": "b0001", "block_type": "paragraph"},
                {"paragraph_id": "p0002", "block_id": "b0002", "block_type": "paragraph"},
            ],
            fallback_translations_by_block_id={
                "b0002": {
                    "block_id": "b0002",
                    "paragraph_id": "p0002",
                    "translated_text": "Dos del primer pase.",
                }
            },
        )

        self.assertEqual(
            parsed["block_translations"],
            [
                {"block_id": "b0001", "paragraph_id": "p0001", "translated_text": "Uno."},
                {
                    "block_id": "b0002",
                    "paragraph_id": "p0002",
                    "translated_text": "Dos del primer pase.",
                },
            ],
        )
        self.assertTrue(
            any("missing in revised output" in note for note in parsed["notes"])
        )

    def test_invalid_json_fallback_preserves_table_when_text_shape_is_bad(self) -> None:
        parsed = parse_translation_output(
            output="| Producto | Puntuacion |",
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "source_text": "Product\tScore\nAlpha\t1",
                    "table_rows": [["Product", "Score"], ["Alpha", "1"]],
                    "table_shape": [2, 2],
                }
            ],
        )

        self.assertEqual(
            parsed["block_translations"][0]["translated_text"],
            "Product\tScore\nAlpha\t1",
        )
        self.assertTrue(
            any("fallback text was not shape-safe" in note for note in parsed["notes"])
        )

    def test_invalid_json_revision_fallback_uses_first_pass_table(self) -> None:
        parsed = parse_translation_output(
            output="| Produto | Pontuacao |",
            chunk=_single_block_chunk(),
            chunk_blocks=[
                {
                    "paragraph_id": "p0001",
                    "block_id": "b0001",
                    "block_type": "table",
                    "source_text": "Product\tScore\nAlpha\t1",
                    "table_rows": [["Product", "Score"], ["Alpha", "1"]],
                    "table_shape": [2, 2],
                }
            ],
            fallback_translations_by_block_id={
                "b0001": {
                    "block_id": "b0001",
                    "paragraph_id": "p0001",
                    "translated_text": "Producto\tPuntuacion\nAlfa\t1",
                    "table_rows": [["Producto", "Puntuacion"], ["Alfa", "1"]],
                }
            },
        )

        self.assertEqual(
            parsed["block_translations"][0]["translated_text"],
            "Producto\tPuntuacion\nAlfa\t1",
        )
        self.assertEqual(
            parsed["block_translations"][0]["table_rows"],
            [["Producto", "Puntuacion"], ["Alfa", "1"]],
        )
        self.assertTrue(
            any("previous valid translation" in note for note in parsed["notes"])
        )

    def test_collect_block_translations_joins_repeated_block_parts(self) -> None:
        collected = collect_block_translations(
            [
                {
                    "translation": {
                        "block_translations": [
                            {"block_id": "b0001", "translated_text": "First part."}
                        ]
                    }
                },
                {
                    "translation": {
                        "block_translations": [
                            {"block_id": "b0001", "translated_text": "Second part."},
                            {"block_id": "b0002", "translated_text": "Another block."},
                        ]
                    }
                },
            ]
        )

        self.assertEqual(collected["b0001"], "First part. Second part.")
        self.assertEqual(collected["b0002"], "Another block.")


class ModelRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_revision_pass_uses_completed_glossary_for_chunk(self) -> None:
        chunk = TranslationChunk(
            chunk_id="chunk_0001",
            paragraph_ids=["p0001"],
            text="The Moon Gate opened.",
            source_word_count=4,
            contains_partial_paragraph=False,
            starts_paragraph=True,
            continues_paragraph=False,
            ends_paragraph=True,
        )
        first_pass_chunks = [
            {
                "chunk": chunk.to_dict(),
                "glossary_used": [],
                "translation": {
                    "chunk_id": "chunk_0001",
                    "paragraph_ids": ["p0001"],
                    "translated_text": "The Moon Gate opened.",
                    "block_translations": [],
                },
                "raw_model_output": "",
            }
        ]
        glossary = [
            {
                "entry_id": "g0001",
                "source_terms": ["Moon Gate"],
                "target_terms": ["Chandra Dwar"],
                "preferred_target": "Chandra Dwar",
                "category": "place",
                "priority": "medium",
                "reason": "Named location.",
            }
        ]
        captured_prompts: list[str] = []
        original_run_agent_with_retries = workflow.run_agent_with_retries
        original_safe_print = workflow.safe_print

        class Result:
            final_output = (
                '{"chunk_id": "chunk_0001", '
                '"paragraph_ids": ["p0001"], '
                '"translated_text": "Chandra Dwar opened.", '
                '"block_translations": []}'
            )

        async def fake_run_agent_with_retries(
            *,
            agent,
            prompt,
            label,
            retries,
            base_delay_seconds,
        ):
            captured_prompts.append(prompt)
            return Result()

        workflow.run_agent_with_retries = fake_run_agent_with_retries
        workflow.safe_print = lambda *args, **kwargs: None
        try:
            revised_chunks, _ = await workflow.revise_translated_chunks(
                chunks=[chunk],
                first_pass_chunks=first_pass_chunks,
                target_language="Hindi",
                brief="Use the glossary.",
                reviewer_feedback="Make terminology consistent.",
                paragraph_blocks={},
                context_chunk_count=0,
                model_retries=0,
                retry_base_delay_seconds=0,
                glossary=glossary,
            )
        finally:
            workflow.run_agent_with_retries = original_run_agent_with_retries
            workflow.safe_print = original_safe_print

        self.assertIn("Moon Gate", captured_prompts[0])
        self.assertIn("Chandra Dwar", captured_prompts[0])
        self.assertEqual(
            revised_chunks[0]["glossary_used"][0]["source_terms"],
            ["Moon Gate"],
        )

    async def test_run_agent_with_retries_recovers_after_transient_error(self) -> None:
        calls = 0
        original_run = workflow.Runner.run
        original_safe_print = workflow.safe_print

        class Result:
            final_output = "ok"

        async def fake_run(agent, prompt):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("APIConnectionError: getaddrinfo failed")
            return Result()

        workflow.Runner.run = fake_run
        workflow.safe_print = lambda *args, **kwargs: None
        try:
            result = await workflow.run_agent_with_retries(
                agent=object(),
                prompt="prompt",
                label="test call",
                retries=1,
                base_delay_seconds=0,
            )
        finally:
            workflow.Runner.run = original_run
            workflow.safe_print = original_safe_print

        self.assertEqual(result.final_output, "ok")
        self.assertEqual(calls, 2)

    async def test_run_agent_with_retries_does_not_retry_permanent_error(self) -> None:
        calls = 0
        original_run = workflow.Runner.run

        async def fake_run(agent, prompt):
            nonlocal calls
            calls += 1
            raise RuntimeError("404 model not found")

        workflow.Runner.run = fake_run
        try:
            with self.assertRaises(RuntimeError):
                await workflow.run_agent_with_retries(
                    agent=object(),
                    prompt="prompt",
                    label="test call",
                    retries=3,
                    base_delay_seconds=0,
                )
        finally:
            workflow.Runner.run = original_run

        self.assertEqual(calls, 1)


def _chunk() -> TranslationChunk:
    return TranslationChunk(
        chunk_id="chunk_0001",
        paragraph_ids=["p0001", "p0002"],
        text="One.\n\nTwo.",
        source_word_count=2,
        contains_partial_paragraph=False,
        starts_paragraph=True,
        continues_paragraph=False,
        ends_paragraph=True,
    )


def _single_block_chunk() -> TranslationChunk:
    return TranslationChunk(
        chunk_id="chunk_0001",
        paragraph_ids=["p0001"],
        text="Product\tScore\nAlpha\t1",
        source_word_count=2,
        contains_partial_paragraph=False,
        starts_paragraph=True,
        continues_paragraph=False,
        ends_paragraph=True,
    )


if __name__ == "__main__":
    unittest.main()
