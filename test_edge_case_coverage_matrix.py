from __future__ import annotations

from pathlib import Path
import unittest


TEST_REFS = {
    "adapter_docx": "test_document_adapters.py::DocumentAdapterTests.test_load_docx_detects_heading_paragraph_table_and_image",
    "adapter_epub": "test_document_adapters.py::DocumentAdapterTests.test_load_epub_reads_spine_order_and_blocks",
    "adapter_edges_docx": "test_document_adapter_edge_cases.py::DocumentAdapterEdgeCaseTests.test_docx_styles_lists_formatting_hidden_bookmarks_and_text_boxes",
    "adapter_edges_epub": "test_document_adapter_edge_cases.py::DocumentAdapterEdgeCaseTests.test_epub_lists_language_sidebar_scene_breaks_and_formatting_metadata",
    "adapter_txt": "test_document_adapters.py::DocumentAdapterTests.test_load_txt_creates_paragraph_blocks",
    "writer_docx": "test_document_writers.py::DocumentWriterTests.test_writes_translated_docx_package",
    "writer_epub": "test_document_writers.py::DocumentWriterTests.test_writes_translated_epub_package",
    "writer_txt": "test_document_writers.py::DocumentWriterTests.test_writes_translated_txt",
    "docx_assets": "test_writer_docx_scenarios.py::WriterDocxScenarioTests.test_translates_docx_story_blocks_and_preserves_non_text_assets",
    "epub_assets": "test_writer_epub_scenarios.py::EpubWriterScenarioTests.test_epub_scenario_translates_content_and_preserves_assets",
    "docx_headings": "test_writer_docx_scenarios.py::WriterDocxScenarioTests.test_translates_docx_heading_hierarchy_and_preserves_levels",
    "epub_headings": "test_writer_epub_scenarios.py::EpubWriterScenarioTests.test_epub_heading_hierarchy_translates_and_preserves_levels",
    "docx_tables": "test_writer_docx_scenarios.py::WriterDocxScenarioTests.test_docx_merged_header_table_survives_translation_export",
    "epub_tables": "test_writer_epub_scenarios.py::EpubWriterScenarioTests.test_epub_complex_table_attributes_survive_translation_export",
    "docx_formulas": "test_writer_docx_scenarios.py::WriterDocxScenarioTests.test_docx_formula_objects_survive_translation_export",
    "epub_formulas": "test_writer_epub_scenarios.py::EpubWriterScenarioTests.test_epub_mathml_survives_translation_export",
    "docx_table_mismatch": "test_writer_docx_scenarios.py::WriterDocxScenarioTests.test_mismatched_docx_table_translation_warns_and_keeps_readable_fallback",
    "epub_table_mismatch": "test_writer_epub_scenarios.py::EpubWriterScenarioTests.test_epub_table_shape_mismatch_warns_and_falls_back_to_first_cell",
    "docx_writer_edges": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_auxiliary_parts_translate_while_package_assets_survive",
    "docx_rich_inline_equations": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_rich_inline_writeback_does_not_duplicate_omml_equation_text",
    "docx_rich_inline_hyperlinks": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_rich_inline_writeback_keeps_hyperlink_text_inside_hyperlink",
    "docx_rich_inline_revisions": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_rich_inline_writeback_keeps_inserted_text_inside_tracked_insert",
    "docx_rich_inline_hidden": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_rich_inline_writeback_preserves_hidden_and_noproof_run_boundaries",
    "docx_rich_inline_styles": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_rich_inline_writeback_keeps_styled_runs_non_empty",
    "docx_missing": "test_document_writers_docx_edge_cases.py::DocumentWriterDocxEdgeCaseTests.test_docx_missing_block_translations_preserve_source_text_in_later_parts",
    "epub_writer_edges": "test_document_writers_epub_edge_cases.py::EpubWriterEdgeCaseTests.test_epub_export_preserves_package_structure_and_nontranslated_blocks",
    "epub_missing": "test_document_writers_epub_edge_cases.py::EpubWriterEdgeCaseTests.test_missing_epub_translation_units_preserve_source_text",
    "chunker": "test_translation_chunker.py::TranslationChunkerTests.test_groups_complete_paragraphs_until_limit",
    "chunker_long_sentence": "test_translation_chunker.py::TranslationChunkerTests.test_splits_long_paragraph_at_last_full_stop_under_limit",
    "chunker_long_word": "test_translation_chunker.py::TranslationChunkerTests.test_splits_long_paragraph_at_word_boundary_when_no_full_stop",
    "prompt_static": "test_translation_prompt.py::TranslationPromptTests.test_static_brief_includes_document_type_guidance",
    "prompt_unknown": "test_translation_prompt.py::TranslationPromptTests.test_unknown_document_type_falls_back_to_general_guidance",
    "prompt_blocks": "test_translation_prompt.py::TranslationPromptTests.test_parse_translation_output_normalizes_block_translations",
    "prompt_invalid": "test_translation_prompt.py::TranslationPromptTests.test_parse_translation_output_falls_back_when_json_is_invalid",
    "prompt_collect": "test_translation_prompt.py::TranslationPromptTests.test_collect_block_translations_joins_repeated_block_parts",
    "glossary_merge": "test_glossary.py::GlossaryTests.test_merges_duplicate_source_terms_and_target_variants",
    "glossary_filter": "test_glossary.py::GlossaryTests.test_filters_relevant_glossary_for_chunk",
    "glossary_parse": "test_glossary.py::GlossaryTests.test_extracts_glossary_from_fenced_json",
}


EDGE_CASE_TEST_COVERAGE = {
    "Format detection and loading": {
        "refs": ["adapter_docx", "adapter_epub", "adapter_txt"],
        "note": "Dedicated loaders are exercised for supported formats.",
    },
    "Book title, subtitle, author": {
        "refs": ["adapter_epub", "epub_assets", "docx_headings"],
        "note": "EPUB metadata and DOCX title-like heading paths are covered.",
    },
    "Front matter": {
        "refs": ["adapter_docx", "adapter_epub", "writer_docx", "writer_epub"],
        "note": "Front matter is currently handled as normal body/spine blocks.",
    },
    "Chapter titles and headings": {
        "refs": ["docx_headings", "epub_headings"],
        "note": "Heading level hierarchy is parsed, translated, exported, and reparsed.",
    },
    "Normal paragraphs": {
        "refs": ["adapter_txt", "writer_docx", "writer_epub", "writer_txt"],
        "note": "Normal block extraction and writer replacement are covered.",
    },
    "Very long paragraphs": {
        "refs": ["chunker_long_sentence", "chunker_long_word"],
        "note": "Long paragraph splitting is covered at the chunker layer.",
    },
    "Dialogue paragraphs": {
        "refs": ["adapter_docx", "adapter_epub", "writer_docx", "writer_epub"],
        "note": "Dialogue is structurally normal paragraph text at this layer.",
    },
    "Poetry, verse, lyrics": {
        "refs": ["adapter_edges_docx", "adapter_epub"],
        "note": "Line-break extraction is partially exercised; stanza semantics remain partial.",
    },
    "Epigraphs and chapter-opening quotes": {
        "refs": ["adapter_edges_docx", "adapter_epub", "epub_assets"],
        "note": "EPUB blockquote quote handling is exercised; DOCX quote styles are style-driven.",
    },
    "Pull quotes and callouts": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "epub_assets"],
        "note": "Quote/aside handling is tested as the current partial implementation.",
    },
    "Footnotes and endnotes": {
        "refs": ["adapter_docx", "docx_writer_edges", "adapter_epub", "epub_assets"],
        "note": "DOCX notes and EPUB footnote asides are parsed and exported.",
    },
    "Page headers, footers, page numbers": {
        "refs": ["adapter_docx", "docx_writer_edges"],
        "note": "DOCX header/footer parts are translated; page-number policy remains partial.",
    },
    "Table of contents entries": {
        "refs": ["adapter_edges_docx", "adapter_epub", "epub_assets"],
        "note": "EPUB nav/TOC preservation is tested; DOCX TOC style classification is partial.",
    },
    "Index and glossary entries": {
        "refs": ["adapter_edges_docx", "glossary_merge", "glossary_filter"],
        "note": "DOCX style classification and glossary mechanics are covered.",
    },
    "Bibliography and references": {
        "refs": ["adapter_edges_docx", "prompt_static"],
        "note": "Reference style classification is partial; citation integrity is prompt-driven.",
    },
    "Captions": {
        "refs": ["adapter_docx", "adapter_epub", "docx_assets", "epub_assets"],
        "note": "DOCX caption styles and EPUB figcaptions are parsed and exported.",
    },
    "Bold, italic, underline": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "epub_assets", "docx_rich_inline_styles"],
        "note": "Block-level formatting detection is exercised and DOCX rich run write-back keeps styled ranges non-empty.",
    },
    "Small caps": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "docx_rich_inline_styles"],
        "note": "Formatting flags are part of parser coverage; DOCX styled-run write-back is covered for the high-risk rich-run path.",
    },
    "Superscript and subscript": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "docx_formulas", "docx_rich_inline_equations", "epub_formulas"],
        "note": "Superscript/subscript risk is covered through formatting and formula tests.",
    },
    "Hyperlinks and internal links": {
        "refs": ["adapter_docx", "adapter_epub", "epub_assets", "docx_rich_inline_hyperlinks"],
        "note": "Link metadata is detected and DOCX hyperlink display text is written back inside the hyperlink wrapper.",
    },
    "Inline code and code blocks": {
        "refs": ["adapter_epub", "epub_assets"],
        "note": "EPUB pre/code preservation is tested; DOCX code-style support is partial.",
    },
    "Block quotes": {
        "refs": ["adapter_edges_docx", "adapter_epub", "epub_assets"],
        "note": "EPUB blockquote parsing/export is covered.",
    },
    "Ordered, unordered, and nested lists": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "epub_assets"],
        "note": "EPUB list items are exported; exact list reconstruction remains partial.",
    },
    "Indentation": {
        "refs": [],
        "deferred_reason": "DOCX indentation extraction is deferred; EPUB class/id hints are metadata only.",
    },
    "Intentional line breaks": {
        "refs": ["adapter_edges_docx", "adapter_epub"],
        "note": "DOCX break handling is covered in parser scenarios; EPUB remains partial.",
    },
    "Scene breaks such as `***`": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub"],
        "note": "Scene-break classification is covered by adapter behavior tracking.",
    },
    "Drop caps": {
        "refs": [],
        "deferred_reason": "Drop-cap preservation requires style/layout handling not implemented yet.",
    },
    "Text boxes and sidebars": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "epub_assets"],
        "note": "DOCX text box flags and EPUB aside/special handling are partial.",
    },
    "Marginal notes": {
        "refs": ["adapter_docx", "docx_writer_edges", "adapter_edges_epub"],
        "note": "DOCX comments and EPUB asides cover the current partial implementation.",
    },
    "Cover image": {
        "refs": ["adapter_epub", "epub_assets"],
        "note": "EPUB cover-image preservation is tested; DOCX cover pages remain partial.",
    },
    "Decorative images": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Images are preserved as non-translatable assets.",
    },
    "Inline images inside text flow": {
        "refs": ["adapter_docx", "docx_assets", "adapter_epub", "epub_assets"],
        "note": "Image metadata and package asset preservation are tested.",
    },
    "Full-page illustrations": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Image assets are preserved; page layout fidelity remains partial.",
    },
    "Diagrams, screenshots, maps with embedded text": {
        "refs": [],
        "deferred_reason": "OCR and translated image replacement are intentionally out of scope.",
    },
    "Image captions": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Caption extraction/export is covered.",
    },
    "Alt text": {
        "refs": ["adapter_docx", "adapter_epub", "epub_assets"],
        "note": "Alt/title/description metadata is parsed; translation policy remains partial.",
    },
    "Images that should not be translated": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Image blocks are asserted non-translatable and package assets survive export.",
    },
    "Simple tables": {
        "refs": ["writer_docx", "writer_epub", "docx_tables", "epub_tables"],
        "note": "Table parsing/export and cell replacement are covered.",
    },
    "Tables with merged cells": {
        "refs": ["docx_tables", "epub_tables"],
        "note": "DOCX grid/vMerge and EPUB colspan/rowspan are tested.",
    },
    "Tables with header rows/columns": {
        "refs": ["docx_tables", "epub_tables"],
        "note": "DOCX tblHeader and EPUB th/scope metadata are tested.",
    },
    "Table footnotes": {
        "refs": [],
        "deferred_reason": "Association between table cells and footnotes is not implemented yet.",
    },
    "Partially translatable table columns": {
        "refs": [],
        "deferred_reason": "Per-column/per-cell translate flags are not implemented yet.",
    },
    "Numeric, date, unit, currency, formula table cells": {
        "refs": ["docx_formulas", "epub_formulas", "epub_tables"],
        "note": "Formula-like and numeric table cell preservation is covered at parser/writer level.",
    },
    "Tables too large for one chunk": {
        "refs": [],
        "deferred_reason": "Large table row-group splitting is not implemented yet.",
    },
    "Table captions": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Caption blocks are covered; table-caption association remains partial.",
    },
    "Math formulas and equations": {
        "refs": ["docx_formulas", "docx_rich_inline_equations", "epub_formulas"],
        "note": "DOCX OMML is protected during rich inline write-back so equation text is not duplicated; EPUB MathML survival is tested.",
    },
    "Chemical formulas": {
        "refs": ["docx_formulas", "epub_formulas", "prompt_static"],
        "note": "Formula preservation behavior covers the structure risk; chemistry rules are prompt-level.",
    },
    "Legal clauses": {
        "refs": ["adapter_docx", "adapter_epub", "prompt_static"],
        "note": "Legal text is normal text structurally; document-type prompt coverage applies.",
    },
    "Forms, questionnaires, checkboxes": {
        "refs": ["adapter_docx", "adapter_epub"],
        "note": "Form/checkbox detection is part of adapter scenarios; state reconstruction remains partial.",
    },
    "Timelines and recipes": {
        "refs": ["adapter_docx", "adapter_epub", "writer_docx", "writer_epub"],
        "note": "Handled through paragraph/list/table structures.",
    },
    "Dictionaries and glossaries": {
        "refs": ["glossary_merge", "glossary_filter", "glossary_parse"],
        "note": "Glossary behavior is covered separately from document structure.",
    },
    "Already bilingual or multilingual text": {
        "refs": ["adapter_edges_epub", "prompt_static"],
        "note": "EPUB lang metadata and prompt policy are covered; DOCX run language remains partial.",
    },
    "Variables, function names, Greek/math symbols": {
        "refs": ["docx_formulas", "epub_formulas"],
        "note": "Formula object preservation is covered; symbol-specific translation rules are downstream.",
    },
    "Equation numbers and references": {
        "refs": ["adapter_docx", "adapter_epub", "docx_formulas", "docx_rich_inline_equations", "epub_formulas"],
        "note": "Field/bookmark/link metadata and formula structures are covered partially.",
    },
    "Geometry labels and diagram labels": {
        "refs": ["docx_formulas", "epub_formulas"],
        "note": "Text labels in formulas/prose are covered; embedded image labels are deferred.",
    },
    "DOCX styles": {
        "refs": ["adapter_edges_docx", "docx_headings", "docx_rich_inline_styles", "docx_rich_inline_hidden"],
        "note": "DOCX style IDs, heading styles, rich run styling, hidden text, and noProof run boundaries are tested.",
    },
    "EPUB HTML structure": {
        "refs": ["adapter_edges_epub", "adapter_epub", "epub_assets", "epub_tables"],
        "note": "Source item, tag, id/class/role, and table attributes are covered.",
    },
    "PDF extraction artifacts": {
        "refs": [],
        "deferred_reason": "PDF is intentionally out of core support.",
    },
    "OCR errors": {
        "refs": [],
        "deferred_reason": "OCR is intentionally out of core support.",
    },
    "Hyphenated line breaks and broken paragraphs": {
        "refs": ["adapter_edges_docx", "adapter_epub", "chunker"],
        "note": "Basic normalization/chunking is covered; explicit repair is partial.",
    },
    "Multi-column layout and text wrapping around images": {
        "refs": [],
        "deferred_reason": "Layout-fidelity reconstruction is not implemented yet.",
    },
    "Floating objects": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Floating images/text-box metadata is partial; exact positioning is not preserved.",
    },
    "Comments, revisions, tracked changes": {
        "refs": ["adapter_docx", "docx_writer_edges", "docx_rich_inline_revisions"],
        "note": "DOCX comments and tracked insertion write-back are covered; full accept/reject policy remains partial.",
    },
    "Hidden text": {
        "refs": ["adapter_edges_docx", "adapter_edges_epub", "adapter_epub", "epub_assets", "docx_rich_inline_hidden"],
        "note": "DOCX hidden/noProof run boundaries and EPUB hidden skip behavior are covered.",
    },
    "Bookmarks and cross-references": {
        "refs": ["adapter_edges_docx", "adapter_epub"],
        "note": "Bookmark/field/href metadata is covered; precise reconstruction remains partial.",
    },
    "Page breaks and section breaks": {
        "refs": ["adapter_docx", "docx_assets", "adapter_epub", "epub_assets"],
        "note": "DOCX page/section breaks and EPUB page-break spans are covered.",
    },
    "Proper nouns, names, invented terms, brands, acronyms": {
        "refs": ["glossary_merge", "glossary_filter", "prompt_static"],
        "note": "Terminology consistency is covered by glossary and prompt tests.",
    },
    "Measurements, currency, dates": {
        "refs": ["epub_tables", "prompt_static"],
        "note": "Numeric table metadata and prompt rules cover current behavior.",
    },
    "Idioms, pronouns, gendered language, tone, voice": {
        "refs": ["prompt_static", "prompt_unknown"],
        "note": "Translation-quality rules are prompt-level rather than adapter-level.",
    },
    "Do-not-translate terms and intentional foreign phrases": {
        "refs": ["adapter_edges_epub", "prompt_static", "glossary_filter"],
        "note": "Prompt and glossary rules cover current behavior; language metadata remains partial.",
    },
    "Missing, duplicated, merged, or reordered chunks": {
        "refs": ["prompt_blocks", "prompt_collect", "docx_missing", "epub_missing"],
        "note": "Block translation normalization and missing translation preservation are covered.",
    },
    "Model changes IDs or placeholders": {
        "refs": ["prompt_blocks"],
        "note": "Unknown block IDs are ignored and noted; stricter schema validation is still future work.",
    },
    "Model drops formatting": {
        "refs": ["adapter_docx", "adapter_epub", "docx_assets", "epub_assets", "docx_rich_inline_styles", "docx_rich_inline_hidden"],
        "note": "Metadata/warning behavior is tested, and high-risk DOCX inline ranges now have targeted write-back coverage.",
    },
    "Invalid JSON or output too long": {
        "refs": ["prompt_invalid", "chunker_long_sentence", "chunker_long_word"],
        "note": "Invalid JSON fallback and chunk-size splitting are covered.",
    },
    "Translated text expansion and layout overflow": {
        "refs": [],
        "deferred_reason": "Layout measurement and overflow remediation are not implemented yet.",
    },
    "Captions detach from images": {
        "refs": ["docx_assets", "epub_assets"],
        "note": "Adjacent image/caption blocks are covered; explicit association IDs remain partial.",
    },
    "Hyperlinks and cross-references lost": {
        "refs": ["adapter_docx", "adapter_epub", "epub_assets", "docx_rich_inline_hyperlinks"],
        "note": "Link/reference metadata is tested, and DOCX hyperlink text remains inside the original hyperlink wrapper.",
    },
}


class EdgeCaseCoverageMatrixTests(unittest.TestCase):
    def test_every_matrix_edge_case_has_coverage_manifest_entry(self) -> None:
        matrix_rows = _coverage_matrix_rows()

        self.assertEqual(
            set(matrix_rows),
            set(EDGE_CASE_TEST_COVERAGE),
            "Every coverage-matrix edge case must have exactly one manifest entry.",
        )

    def test_manifest_entries_have_evidence_or_deferred_reason(self) -> None:
        for edge_case, entry in EDGE_CASE_TEST_COVERAGE.items():
            refs = entry.get("refs", [])
            deferred_reason = entry.get("deferred_reason", "")
            note = entry.get("note", "")

            self.assertIsInstance(refs, list, edge_case)
            self.assertTrue(
                refs or deferred_reason,
                f"{edge_case} needs test refs or a deferred_reason.",
            )
            if refs:
                self.assertTrue(note, f"{edge_case} needs a note explaining coverage.")

    def test_referenced_tests_exist(self) -> None:
        for edge_case, entry in EDGE_CASE_TEST_COVERAGE.items():
            for ref_key in entry.get("refs", []):
                ref = TEST_REFS[ref_key]
                file_name, qualified_name = ref.split("::", 1)
                class_name, test_name = qualified_name.split(".", 1)
                file_path = Path(file_name)

                self.assertTrue(file_path.exists(), ref)
                source = file_path.read_text(encoding="utf-8")
                self.assertIn(f"class {class_name}", source, ref)
                self.assertIn(f"def {test_name}", source, ref)

    def test_deferred_rows_match_matrix_status(self) -> None:
        matrix_rows = _coverage_matrix_rows()
        for edge_case, entry in EDGE_CASE_TEST_COVERAGE.items():
            if "deferred_reason" not in entry:
                continue
            statuses = matrix_rows[edge_case]
            self.assertTrue(
                {"Deferred", "Not applicable"}.intersection(statuses),
                f"{edge_case} has a deferred_reason but is not deferred/not-applicable in matrix.",
            )


def _coverage_matrix_rows() -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for line in Path("DOCX_EPUB_COVERAGE_MATRIX.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        if line.startswith("| Edge case") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        edge_case, docx_status, epub_status = cells[:3]
        rows[edge_case] = (docx_status, epub_status)
    return rows


if __name__ == "__main__":
    unittest.main()
