import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from translator.documents.adapters import WORD_NS, load_docx
from translator.documents.model import ParsedDocument
from translator.documents.writers import write_translated_document
from test_document_adapters import (
    _write_docx_inline_image_and_superscript,
    _write_docx_inline_image_between_text,
    _write_docx_multiple_inline_images,
    _write_docx_nested_table,
    _write_docx_table_cell_inline_image,
    _write_docx_table_cell_content_control,
    _write_docx_top_level_content_control_table,
    _write_minimal_docx,
)


class DocumentWriterDocxEdgeCaseTests(unittest.TestCase):
    def test_docx_table_shape_mismatch_warns_and_preserves_table_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_minimal_docx(source_path)
            parsed = load_docx(source_path)

            report = write_translated_document(
                parsed_document=parsed,
                translated_text=_translated_text_for_blocks(
                    parsed.blocks,
                    table_text="Only one translated cell",
                ),
                output_path=output_path,
            )

            exported = load_docx(output_path)
            table_block = next(block for block in exported.blocks if block.type == "table")

        self.assertIn(
            "A translated DOCX table did not preserve row/cell shape; "
            "placing the full translated table text in the first cell.",
            report.warnings,
        )
        self.assertEqual(table_block.metadata["rows"], [["Only one translated cell", "Meaning"]])
        self.assertTrue(table_block.metadata["has_header_rows"])
        self.assertTrue(table_block.metadata["has_merged_cells"])
        self.assertTrue(table_block.metadata["contains_equations"])

    def test_docx_table_translation_pads_omitted_blank_leading_header_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "blank-leading-cell.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_blank_leading_cell_table_docx(source_path)
            parsed = load_docx(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: "Koeffizient\tt-Wert\nZeile\t1,21\t0,27"
                },
                output_path=output_path,
            )

            exported = load_docx(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")

        self.assertEqual(
            exported_table.metadata["rows"],
            [["", "Koeffizient", "t-Wert"], ["Zeile", "1,21", "0,27"]],
        )
        self.assertNotIn(
            "A translated DOCX table did not preserve row/cell shape; "
            "placing the full translated table text in the first cell.",
            report.warnings,
        )

    def test_docx_writer_uses_structured_table_rows_with_trailing_empty_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "trailing-empty-cell.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_trailing_empty_cell_table_docx(source_path)
            parsed = load_docx(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: {
                        "translated_text": "Uno\tDos\nTres\tCuatro\tCinco",
                        "table_rows": [
                            ["Uno", "Dos", ""],
                            ["Tres", "Cuatro", "Cinco"],
                        ],
                    },
                },
                output_path=output_path,
            )

            exported = load_docx(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")

        self.assertEqual(
            exported_table.metadata["rows"],
            [["Uno", "Dos", ""], ["Tres", "Cuatro", "Cinco"]],
        )
        self.assertNotIn(
            "A translated DOCX table did not preserve row/cell shape; "
            "placing the full translated table text in the first cell.",
            report.warnings,
        )

    def test_docx_writer_rebases_inline_tokens_across_table_cell_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "table-cell-paragraph-tokens.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_table_cell_multiple_rich_paragraphs_docx(source_path)
            parsed = load_docx(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: {
                        "translated_text": "标签\t[[INLINE_0001]]值 A[[INLINE_0002]]; [[INLINE_0003]]值 B[[INLINE_0004]]",
                        "table_rows": [
                            [
                                "标签",
                                "[[INLINE_0001]]值 A[[INLINE_0002]]; [[INLINE_0003]]值 B[[INLINE_0004]]",
                            ]
                        ],
                    }
                },
                output_path=output_path,
            )

            exported = load_docx(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")

        self.assertEqual(exported_table.metadata["rows"], [["标签", "值 A; 值 B"]])
        self.assertNotIn("[[INLINE_", exported_table.text)

    def test_docx_writer_translates_nested_tables_without_flattening_parent_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "nested-table.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_nested_table(source_path)
            parsed = load_docx(source_path)
            table_blocks = [block for block in parsed.blocks if block.type == "table"]

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_blocks[0].block_id: "Exterior izquierdo\tIntro exterior; Final exterior",
                    table_blocks[1].block_id: "Clave interna\tValor interno",
                },
                output_path=output_path,
            )

            exported = load_docx(output_path)
            exported_tables = [block for block in exported.blocks if block.type == "table"]

        self.assertEqual(report.applied_unit_count, 2)
        self.assertEqual(
            exported_tables[0].metadata["rows"],
            [["Exterior izquierdo", "Intro exterior; Final exterior"]],
        )
        self.assertEqual(
            exported_tables[1].metadata["rows"],
            [["Clave interna", "Valor interno"]],
        )
        self.assertNotIn("Clave interna", exported_tables[0].text)

    def test_docx_writer_updates_content_control_inside_table_cell_without_losing_sdt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "cell-content-control.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_table_cell_content_control(source_path)
            parsed = load_docx(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: "Etiqueta\tValor traducido",
                },
                output_path=output_path,
            )

            exported = load_docx(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")
            with ZipFile(output_path) as exported_docx:
                document_xml = exported_docx.read("word/document.xml").decode("utf-8")

        self.assertEqual(report.applied_unit_count, 1)
        self.assertEqual(exported_table.metadata["rows"], [["Etiqueta", "Valor traducido"]])
        self.assertIn("<w:sdt>", document_xml)
        self.assertIn("<w:sdtPr>", document_xml)
        self.assertIn("<w:sdtContent>", document_xml)
        self.assertIn('w:val="Cell Alias"', document_xml)
        self.assertIn('w:val="cell-tag"', document_xml)
        self.assertIn('w:val="456"', document_xml)

    def test_docx_writer_translates_table_nested_inside_content_control_preserving_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sdt-table.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_top_level_content_control_table(source_path)
            parsed = load_docx(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: "Clave interna\tValor interno",
                },
                output_path=output_path,
            )

            exported = load_docx(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")
            with ZipFile(output_path) as exported_docx:
                document_xml = exported_docx.read("word/document.xml").decode("utf-8")

        self.assertEqual(report.applied_unit_count, 1)
        self.assertEqual(exported_table.metadata["rows"], [["Clave interna", "Valor interno"]])
        self.assertIn("<w:sdt>", document_xml)
        self.assertLess(document_xml.index("<w:sdt>"), document_xml.index("<w:tbl>"))
        self.assertIn('w:val="Table Alias"', document_xml)
        self.assertIn('w:val="table-tag"', document_xml)
        self.assertIn('w:val="789"', document_xml)

    def test_docx_writer_uses_tree_paths_when_flat_block_order_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "two-paragraphs.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_two_paragraph_docx(source_path)
            parsed = load_docx(source_path)
            text_blocks = [block for block in parsed.blocks if block.translate]
            reordered = ParsedDocument(
                source_path=parsed.source_path,
                source_format=parsed.source_format,
                blocks=[text_blocks[1], text_blocks[0]],
            )

            report = write_translated_document(
                parsed_document=reordered,
                translations_by_block_id={
                    text_blocks[0].block_id: "Primero traducido",
                    text_blocks[1].block_id: "Segundo traducido",
                },
                output_path=output_path,
            )

            exported_text = load_docx(output_path).to_translation_text()

        self.assertEqual(
            exported_text,
            "Primero traducido\n\nSegundo traducido",
        )
        self.assertEqual(report.applied_unit_count, 2)
        self.assertFalse(
            any(warning.startswith("Block alignment warning") for warning in report.warnings)
        )

    def test_docx_replacement_uses_normal_run_after_superscript_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "superscript-marker.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_superscript_marker_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "\u207a = Signifikanz bei Schwellenwert 0,05"
                },
                output_path=output_path,
            )

            text, run_vert_aligns = _paragraph_text_and_run_vert_aligns(output_path)

        self.assertEqual(text, "\u207a = Signifikanz bei Schwellenwert 0,05")
        self.assertIn(("\u207a = Signifikanz bei Schwellenwert 0,05", None), run_vert_aligns)
        self.assertNotIn(
            ("\u207a = Signifikanz bei Schwellenwert 0,05", "superscript"),
            run_vert_aligns,
        )

    def test_docx_replacement_rebuilds_inline_placeholder_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "inline-placeholder.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_inline_placeholder_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "Gesamt-adjustiertes R[[INLINE_0001]] = 0,45"
                },
                output_path=output_path,
            )

            text, run_vert_aligns = _paragraph_text_and_run_vert_aligns(output_path)

        self.assertEqual(text, "Gesamt-adjustiertes R2 = 0,45")
        self.assertIn(("2", "superscript"), run_vert_aligns)
        self.assertIn(("Gesamt-adjustiertes R", None), run_vert_aligns)
        self.assertIn((" = 0,45", None), run_vert_aligns)

    def test_docx_replacement_preserves_inline_image_position_with_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "inline-image.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_inline_image_between_text(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: (
                        "Texto antes del icono. [[INLINE_0001]]Texto después del icono."
                    )
                },
                output_path=output_path,
            )

            order = _paragraph_child_order(output_path)

        self.assertEqual(
            order,
            ["text:Texto antes del icono. ", "drawing", "text:Texto después del icono."],
        )

    def test_docx_replacement_preserves_multiple_inline_image_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "multiple-inline-images.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_multiple_inline_images(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: (
                        "[[INLINE_0001]]Inicio [[INLINE_0002]]medio[[INLINE_0003]]"
                    )
                },
                output_path=output_path,
            )

            order = _paragraph_child_order(output_path)

        self.assertEqual(
            order,
            ["drawing", "text:Inicio ", "drawing", "text:medio", "drawing"],
        )

    def test_docx_replacement_preserves_inline_image_and_superscript_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "inline-image-superscript.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_inline_image_and_superscript(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: (
                        "Icono [[INLINE_0001]] R ajustado[[INLINE_0002]] valor"
                    )
                },
                output_path=output_path,
            )

            order = _paragraph_child_order(output_path, include_vertical_alignment=True)

        self.assertEqual(
            order,
            [
                "text:Icono ",
                "drawing",
                "text: R ajustado",
                "superscript:2",
                "text: valor",
            ],
        )

    def test_docx_table_cell_preserves_inline_image_position_with_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "table-inline-image.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_docx_table_cell_inline_image(source_path)
            parsed = load_docx(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: "Etiqueta\tAntes [[INLINE_0001]]despuÃ©s",
                },
                output_path=output_path,
            )

            order = _table_cell_paragraph_child_order(output_path, row_index=0, cell_index=1)

        self.assertEqual(order, ["text:Antes ", "drawing", "text:despuÃ©s"])

    def test_docx_writer_updates_top_level_content_control_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "content-control.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_content_control_docx(source_path)
            parsed = load_docx(source_path)
            block = next(block for block in parsed.blocks if block.translate)

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={block.block_id: "Translated control value"},
                output_path=output_path,
            )

            exported_text = load_docx(output_path).to_translation_text()

        self.assertIn("Translated control value", exported_text)
        self.assertEqual(report.applied_unit_count, 1)

    def test_docx_missing_block_translations_preserve_source_text_in_later_parts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_minimal_docx(source_path)
            parsed = load_docx(source_path)

            report = write_translated_document(
                parsed_document=parsed,
                translated_text="\n\n".join(
                    [
                        "Translated heading",
                        "Translated paragraph",
                        "Translated caption",
                        "Translated term\tTranslated meaning",
                    ]
                ),
                output_path=output_path,
            )

            exported_text = load_docx(output_path).to_translation_text()

        self.assertIn("Translated heading", exported_text)
        self.assertIn("Translated paragraph", exported_text)
        self.assertIn("Translated caption", exported_text)
        self.assertIn("Translated term\tTranslated meaning", exported_text)
        self.assertIn("Footnote text.", exported_text)
        self.assertIn("Endnote text.", exported_text)
        self.assertIn("Reviewer comment.", exported_text)
        self.assertIn("Header text", exported_text)
        self.assertIn("Footer text", exported_text)
        self.assertLess(report.applied_unit_count, report.translatable_block_count)
        self.assertTrue(
            any(
                warning.startswith("Missing translated text for")
                and warning.endswith("preserved source text.")
                for warning in report.warnings
            )
        )

    def test_docx_auxiliary_parts_translate_while_package_assets_survive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_minimal_docx(source_path)
            parsed = load_docx(source_path)

            write_translated_document(
                parsed_document=parsed,
                translated_text=_translated_text_for_blocks(
                    parsed.blocks,
                    table_text="Termino\tSignificado",
                ),
                output_path=output_path,
            )

            with ZipFile(output_path) as exported_docx:
                names = set(exported_docx.namelist())
                document_xml = exported_docx.read("word/document.xml").decode("utf-8")
                footnotes_xml = exported_docx.read("word/footnotes.xml").decode("utf-8")
                endnotes_xml = exported_docx.read("word/endnotes.xml").decode("utf-8")
                comments_xml = exported_docx.read("word/comments.xml").decode("utf-8")
                header_xml = exported_docx.read("word/header1.xml").decode("utf-8")
                footer_xml = exported_docx.read("word/footer1.xml").decode("utf-8")
                rels_xml = exported_docx.read("word/_rels/document.xml.rels").decode("utf-8")
                image_bytes = exported_docx.read("word/media/image1.png")

        self.assertIn("word/media/image1.png", names)
        self.assertEqual(image_bytes, b"fake")
        self.assertIn("Target=\"media/image1.png\"", rels_xml)
        self.assertIn("<w:drawing>", document_xml)
        self.assertIn("r:embed=\"rId1\"", document_xml)
        self.assertIn("w:type=\"page\"", document_xml)
        self.assertIn("<w:sectPr", document_xml)
        self.assertIn("Translated footnote", footnotes_xml)
        self.assertIn("Translated endnote", endnotes_xml)
        self.assertIn("Translated comment", comments_xml)
        self.assertIn("Translated header", header_xml)
        self.assertIn("Translated footer", footer_xml)

    def test_docx_rich_inline_writeback_does_not_duplicate_omml_equation_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "omml-equation.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_omml_equation_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "Ecuacion cuadratica: x + 1."
                },
                output_path=output_path,
            )

            paragraph = _first_paragraph(output_path)
            visible_text = _all_text(paragraph)

        self.assertEqual(visible_text.count("x + 1"), 1)
        self.assertEqual(len(paragraph.findall(".//m:oMath", WORD_NS)), 1)

    def test_docx_rich_inline_writeback_keeps_hyperlink_text_inside_hyperlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "hyperlink.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_hyperlink_only_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "Abrir documentacion"
                },
                output_path=output_path,
            )

            paragraph = _first_paragraph(output_path)
            hyperlink = paragraph.find("w:hyperlink", WORD_NS)
            assert hyperlink is not None

        self.assertEqual(_all_text(hyperlink), "Abrir documentacion")
        self.assertNotIn(
            "Abrir documentacion",
            "".join(
                _all_text(child)
                for child in paragraph
                if _local_name(child.tag) != "hyperlink"
            ),
        )

    def test_docx_rich_inline_writeback_removes_nested_style_tokens_inside_hyperlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "nested-hyperlink.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_hyperlink_with_nested_styles_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: (
                        "链接：[[INLINE_0001]]https://example.com/"
                        "[[INLINE_0003]]deep/path[[INLINE_0004]]"
                        "[[INLINE_0005]]?q=1[[INLINE_0006]][[INLINE_0002]]"
                    )
                },
                output_path=output_path,
            )

            paragraph = _first_paragraph(output_path)
            hyperlink = paragraph.find("w:hyperlink", WORD_NS)
            assert hyperlink is not None
            visible_text = _all_text(paragraph)

        self.assertEqual(visible_text, "链接：https://example.com/deep/path?q=1")
        self.assertEqual(_all_text(hyperlink), "https://example.com/deep/path?q=1")
        self.assertNotIn("[[INLINE_", visible_text)

    def test_docx_rich_inline_writeback_keeps_inserted_text_inside_tracked_insert(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "tracked-insert.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_tracked_insert_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "Texto insertado traducido"
                },
                output_path=output_path,
            )

            paragraph = _first_paragraph(output_path)
            inserted = paragraph.find("w:ins", WORD_NS)
            assert inserted is not None

        self.assertEqual(_all_text(inserted), "Texto insertado traducido")
        self.assertEqual(_all_text(paragraph), "Texto insertado traducido")

    def test_docx_rich_inline_writeback_preserves_hidden_and_noproof_run_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "hidden-noproof.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_hidden_and_noproof_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={paragraph_block.block_id: "Texto visible"},
                output_path=output_path,
            )

            paragraph = _first_paragraph(output_path)
            run_details = _paragraph_run_details(paragraph)

        self.assertIn(("Texto visible", False, False), run_details)
        self.assertFalse(
            any(
                text in {"Hidden source", "NoProof source"} and not (hidden or no_proof)
                for text, hidden, no_proof in run_details
            )
        )
        self.assertTrue(any(hidden for _text, hidden, _no_proof in run_details))
        self.assertTrue(any(no_proof for _text, _hidden, no_proof in run_details))

    def test_docx_rich_inline_writeback_keeps_styled_runs_non_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "styled-runs.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_styled_runs_docx(source_path)
            parsed = load_docx(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.translate)

            write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "Texto en negrita cursiva subrayado color"
                },
                output_path=output_path,
            )

            paragraph = _first_paragraph(output_path)
            styled_texts = _styled_run_texts(paragraph)

        self.assertTrue(styled_texts["bold"].strip())
        self.assertTrue(styled_texts["italic"].strip())
        self.assertTrue(styled_texts["underline"].strip())
        self.assertTrue(styled_texts["color"].strip())


def _translated_text_for_blocks(blocks, table_text: str) -> str:
    units: list[str] = []
    for block in blocks:
        if not block.translate or not block.text.strip():
            continue
        if block.type == "table":
            units.append(table_text)
        else:
            units.append(f"Translated {block.type}")
    return "\n\n".join(units)


def _write_blank_leading_cell_table_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p/></w:tc>
        <w:tc><w:p><w:r><w:t>Coefficient</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>t-value</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Row</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>1.21</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>0.27</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_trailing_empty_cell_table_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Left</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Middle</w:t></w:r></w:p></w:tc>
        <w:tc><w:p/></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>C</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_table_cell_multiple_rich_paragraphs_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Label</w:t></w:r></w:p></w:tc>
        <w:tc>
          <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Value A</w:t></w:r></w:p>
          <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Value B</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_two_paragraph_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_superscript_marker_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>+</w:t></w:r>
      <w:r><w:t> = significance at 0.05 threshold</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_content_control_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:sdt>
      <w:sdtContent>
        <w:p><w:r><w:t>Content control value</w:t></w:r></w:p>
      </w:sdtContent>
    </w:sdt>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_inline_placeholder_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>Overall Adjusted R</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>
      <w:r><w:t> = 0.45</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_omml_equation_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    <w:p>
      <w:r><w:t xml:space="preserve">Equation: </w:t></w:r>
      <m:oMath>
        <m:r><m:t>x + 1</m:t></m:r>
      </m:oMath>
      <w:r><w:t>.</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_hyperlink_only_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:p>
      <w:hyperlink r:id="rIdLink">
        <w:r><w:t>Open documentation</w:t></w:r>
      </w:hyperlink>
    </w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLink"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    Target="https://example.com" TargetMode="External"/>
</Relationships>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", relationships_xml)


def _write_hyperlink_with_nested_styles_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:p>
      <w:r><w:t xml:space="preserve">Link: </w:t></w:r>
      <w:hyperlink r:id="rIdLink">
        <w:r><w:t>https://example.com/</w:t></w:r>
        <w:r><w:rPr><w:b/></w:rPr><w:t>deep/path</w:t></w:r>
        <w:r><w:rPr><w:i/></w:rPr><w:t>?q=1</w:t></w:r>
      </w:hyperlink>
    </w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLink"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    Target="https://example.com/deep/path?q=1" TargetMode="External"/>
</Relationships>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", relationships_xml)


def _write_tracked_insert_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:ins w:id="1" w:author="Reviewer" w:date="2026-05-19T10:00:00Z">
        <w:r><w:t>Inserted source text</w:t></w:r>
      </w:ins>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_hidden_and_noproof_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>Visible source</w:t></w:r>
      <w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden source</w:t></w:r>
      <w:r><w:rPr><w:noProof/></w:rPr><w:t>NoProof source</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_styled_runs_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:rPr><w:b/></w:rPr><w:t>Bold</w:t></w:r>
      <w:r><w:t xml:space="preserve"> </w:t></w:r>
      <w:r><w:rPr><w:i/></w:rPr><w:t>Italic</w:t></w:r>
      <w:r><w:t xml:space="preserve"> </w:t></w:r>
      <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>Underline</w:t></w:r>
      <w:r><w:t xml:space="preserve"> </w:t></w:r>
      <w:r><w:rPr><w:color w:val="C00000"/></w:rPr><w:t>Color</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _paragraph_text_and_run_vert_aligns(path: Path) -> tuple[str, list[tuple[str, str | None]]]:
    with ZipFile(path) as docx:
        root = ET.fromstring(docx.read("word/document.xml"))

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraph = root.find(".//w:p", ns)
    assert paragraph is not None

    text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
    runs: list[tuple[str, str | None]] = []
    for run in paragraph.findall("w:r", ns):
        run_text = "".join(node.text or "" for node in run.findall(".//w:t", ns))
        if not run_text:
            continue
        vert_align = run.find("w:rPr/w:vertAlign", ns)
        runs.append(
            (
                run_text,
                vert_align.attrib.get(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"
                )
                if vert_align is not None
                else None,
            )
        )
    return text, runs


def _paragraph_child_order(
    path: Path,
    include_vertical_alignment: bool = False,
) -> list[str]:
    with ZipFile(path) as docx:
        root = ET.fromstring(docx.read("word/document.xml"))

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraph = root.find(".//w:p", ns)
    assert paragraph is not None

    order: list[str] = []
    for run in paragraph.findall("w:r", ns):
        if run.find("w:drawing", ns) is not None:
            order.append("drawing")
            continue
        run_text = "".join(node.text or "" for node in run.findall("w:t", ns))
        if run_text:
            vert_align = run.find("w:rPr/w:vertAlign", ns)
            if include_vertical_alignment and vert_align is not None:
                value = vert_align.attrib.get(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"
                )
                order.append(f"{value}:{run_text}")
            else:
                order.append(f"text:{run_text}")
    return order


def _table_cell_paragraph_child_order(
    path: Path,
    row_index: int,
    cell_index: int,
) -> list[str]:
    with ZipFile(path) as docx:
        root = ET.fromstring(docx.read("word/document.xml"))

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    rows = root.findall(".//w:tbl/w:tr", ns)
    row = rows[row_index]
    cells = row.findall("w:tc", ns)
    paragraph = cells[cell_index].find("w:p", ns)
    assert paragraph is not None

    order: list[str] = []
    for run in paragraph.findall("w:r", ns):
        if run.find("w:drawing", ns) is not None:
            order.append("drawing")
            continue
        run_text = "".join(node.text or "" for node in run.findall("w:t", ns))
        if run_text:
            order.append(f"text:{run_text}")
    return order


def _first_paragraph(path: Path) -> ET.Element:
    with ZipFile(path) as docx:
        root = ET.fromstring(docx.read("word/document.xml"))

    paragraph = root.find(".//w:p", WORD_NS)
    assert paragraph is not None
    return paragraph


def _all_text(element: ET.Element) -> str:
    return "".join(
        node.text or ""
        for node in element.iter()
        if _local_name(node.tag) == "t"
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _paragraph_run_details(paragraph: ET.Element) -> list[tuple[str, bool, bool]]:
    details: list[tuple[str, bool, bool]] = []
    for run in paragraph.findall(".//w:r", WORD_NS):
        text = _all_text(run)
        details.append(
            (
                text,
                run.find("w:rPr/w:vanish", WORD_NS) is not None,
                run.find("w:rPr/w:noProof", WORD_NS) is not None,
            )
        )
    return details


def _styled_run_texts(paragraph: ET.Element) -> dict[str, str]:
    texts = {"bold": "", "italic": "", "underline": "", "color": ""}
    for run in paragraph.findall(".//w:r", WORD_NS):
        run_text = _all_text(run)
        if run.find("w:rPr/w:b", WORD_NS) is not None:
            texts["bold"] += run_text
        if run.find("w:rPr/w:i", WORD_NS) is not None:
            texts["italic"] += run_text
        if run.find("w:rPr/w:u", WORD_NS) is not None:
            texts["underline"] += run_text
        if run.find("w:rPr/w:color", WORD_NS) is not None:
            texts["color"] += run_text
    return texts


if __name__ == "__main__":
    unittest.main()
