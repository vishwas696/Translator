import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from translator.documents.adapters import load_docx
from translator.documents.writers import write_translated_document


class WriterDocxScenarioTests(unittest.TestCase):
    def test_translates_docx_heading_hierarchy_and_preserves_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "headings.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_heading_hierarchy_docx(source_path)
            parsed = load_docx(source_path)

            heading_blocks = [block for block in parsed.blocks if block.type == "heading"]
            self.assertEqual([block.level for block in heading_blocks], [1, 2, 3])

            translations = {
                heading_blocks[0].block_id: "TR Chapter Title",
                heading_blocks[1].block_id: "TR Section Title",
                heading_blocks[2].block_id: "TR Subsection Title",
            }
            paragraph_block = next(block for block in parsed.blocks if block.type == "paragraph")
            translations[paragraph_block.block_id] = "TR Paragraph."

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id=translations,
                output_path=output_path,
            )
            exported = load_docx(output_path)

        exported_headings = [block for block in exported.blocks if block.type == "heading"]
        self.assertEqual([block.level for block in exported_headings], [1, 2, 3])
        self.assertEqual(
            [block.text for block in exported_headings],
            ["TR Chapter Title", "TR Section Title", "TR Subsection Title"],
        )
        self.assertIn("TR Paragraph.", exported.to_translation_text())
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)
        self.assertEqual(report.warnings, [])

    def test_translates_docx_story_blocks_and_preserves_non_text_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "scenario.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_scenario_docx(source_path)
            parsed = load_docx(source_path)

            translations = _translations_by_type(
                parsed,
                {
                    "heading": "TR Heading",
                    "paragraph": "TR Paragraph",
                    "caption": "TR Caption",
                    "table": "TR Product\tTR Score\nTR Alpha\tTR 1\nTR Beta\tTR 2",
                    "footnote": "TR Footnote",
                    "endnote": "TR Endnote",
                    "comment": "TR Comment",
                    "header": "TR Header",
                    "footer": "TR Footer",
                },
            )

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id=translations,
                output_path=output_path,
            )
            exported = load_docx(output_path)

            with ZipFile(output_path) as exported_docx:
                names = set(exported_docx.namelist())
                document_xml = exported_docx.read("word/document.xml").decode("utf-8")
                rels_xml = exported_docx.read("word/_rels/document.xml.rels").decode("utf-8")
                image_bytes = exported_docx.read("word/media/image1.png")

        exported_text = exported.to_translation_text()
        exported_types = [block.type for block in exported.blocks]
        table_block = next(block for block in exported.blocks if block.type == "table")
        image_block = next(block for block in exported.blocks if block.type == "image")

        self.assertIn("TR Heading", exported_text)
        self.assertIn("TR Paragraph", exported_text)
        self.assertIn("TR Caption", exported_text)
        self.assertIn("TR Product\tTR Score\nTR Alpha\tTR 1\nTR Beta\tTR 2", exported_text)
        self.assertIn("TR Footnote", exported_text)
        self.assertIn("TR Endnote", exported_text)
        self.assertIn("TR Comment", exported_text)
        self.assertIn("TR Header", exported_text)
        self.assertIn("TR Footer", exported_text)
        self.assertEqual(
            table_block.metadata["rows"],
            [["TR Product", "TR Score"], ["TR Alpha", "TR 1"], ["TR Beta", "TR 2"]],
        )
        self.assertIn("page_break", exported_types)
        self.assertIn("section_break", exported_types)
        self.assertIn("word/media/image1.png", names)
        self.assertEqual(image_bytes, b"image-bytes")
        self.assertIn("Target=\"media/image1.png\"", rels_xml)
        self.assertIn("r:embed=\"rIdImage\"", document_xml)
        self.assertIn("w:type=\"page\"", document_xml)
        self.assertIn("<w:sectPr", document_xml)
        self.assertEqual(image_block.metadata["description"], "Scenario image")
        self.assertEqual(image_block.metadata["title"], "Image title")
        self.assertEqual(image_block.metadata["target"], "media/image1.png")
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)
        self.assertEqual(report.warnings, [])

    def test_docx_formula_objects_survive_translation_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "formulas.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_formula_docx(source_path)
            parsed = load_docx(source_path)

            paragraph_block = next(block for block in parsed.blocks if block.type == "paragraph")
            table_block = next(block for block in parsed.blocks if block.type == "table")
            self.assertTrue(paragraph_block.metadata["contains_equation"])
            self.assertTrue(table_block.metadata["contains_equations"])

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "TR Solve this equation:",
                    table_block.block_id: "TR Formula\tx+1=2",
                },
                output_path=output_path,
            )
            exported = load_docx(output_path)
            with ZipFile(output_path) as exported_docx:
                document_xml = exported_docx.read("word/document.xml").decode("utf-8")

        exported_paragraph = next(block for block in exported.blocks if block.type == "paragraph")
        exported_table = next(block for block in exported.blocks if block.type == "table")
        self.assertTrue(exported_paragraph.metadata["contains_equation"])
        self.assertTrue(exported_table.metadata["contains_equations"])
        self.assertIn("TR Solve this equation:", exported_paragraph.text)
        self.assertIn("x+1=2", exported_paragraph.text)
        self.assertEqual(exported_table.metadata["rows"], [["TR Formula", "x+1=2"]])
        self.assertIn("<m:oMath>", document_xml)
        self.assertIn("<m:t>x+1=2</m:t>", document_xml)
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)

    def test_docx_merged_header_table_survives_translation_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "merged-table.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_merged_table_docx(source_path)
            parsed = load_docx(source_path)

            table_block = next(block for block in parsed.blocks if block.type == "table")
            self.assertTrue(table_block.metadata["has_header_rows"])
            self.assertTrue(table_block.metadata["has_merged_cells"])
            self.assertEqual(
                table_block.metadata["row_metadata"][0]["cells"][0]["grid_span"],
                "2",
            )
            self.assertEqual(
                table_block.metadata["row_metadata"][2]["cells"][0]["vertical_merge"],
                "restart",
            )
            self.assertEqual(
                table_block.metadata["row_metadata"][3]["cells"][0]["vertical_merge"],
                "continue",
            )

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: (
                        "TR Merged Header\n"
                        "TR Left\tTR Right\n"
                        "TR Group\tTR One\n"
                        "\tTR Two"
                    )
                },
                output_path=output_path,
            )
            exported = load_docx(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")
            with ZipFile(output_path) as exported_docx:
                document_xml = exported_docx.read("word/document.xml").decode("utf-8")

        self.assertEqual(
            exported_table.metadata["rows"],
            [
                ["TR Merged Header"],
                ["TR Left", "TR Right"],
                ["TR Group", "TR One"],
                ["", "TR Two"],
            ],
        )
        self.assertTrue(exported_table.metadata["has_header_rows"])
        self.assertTrue(exported_table.metadata["has_merged_cells"])
        self.assertEqual(
            exported_table.metadata["row_metadata"][0]["cells"][0]["grid_span"],
            "2",
        )
        self.assertEqual(
            exported_table.metadata["row_metadata"][2]["cells"][0]["vertical_merge"],
            "restart",
        )
        self.assertEqual(
            exported_table.metadata["row_metadata"][3]["cells"][0]["vertical_merge"],
            "continue",
        )
        self.assertIn('<w:gridSpan w:val="2"', document_xml)
        self.assertIn('<w:vMerge w:val="restart"', document_xml)
        self.assertIn("<w:vMerge", document_xml)
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)
        self.assertEqual(report.warnings, [])

    def test_mismatched_docx_table_translation_warns_and_keeps_readable_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "scenario.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_scenario_docx(source_path)
            parsed = load_docx(source_path)

            translations = _translations_by_type(
                parsed,
                {
                    "heading": "TR Heading",
                    "paragraph": "TR Paragraph",
                    "caption": "TR Caption",
                    "table": "Loose table translation",
                    "footnote": "TR Footnote",
                    "endnote": "TR Endnote",
                    "comment": "TR Comment",
                    "header": "TR Header",
                    "footer": "TR Footer",
                },
            )

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id=translations,
                output_path=output_path,
            )
            exported = load_docx(output_path)

        table_block = next(block for block in exported.blocks if block.type == "table")

        self.assertIn(
            "A translated DOCX table did not preserve row/cell shape; "
            "placing the full translated table text in the first cell.",
            report.warnings,
        )
        self.assertEqual(
            table_block.metadata["rows"],
            [["Loose table translation", "Score"], ["Alpha", "1"], ["Beta", "2"]],
        )
        self.assertIn("Loose table translation\tScore\nAlpha\t1\nBeta\t2", exported.to_translation_text())

    def test_docx_contents_layout_table_rebuilds_as_clean_toc_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "contents-table.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_contents_layout_table_docx(source_path)
            parsed = load_docx(source_path)

            table_block = next(block for block in parsed.blocks if block.type == "table")
            self.assertEqual(table_block.metadata["table_role"], "toc_layout")

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: (
                        "अध्याय सामग्री\n"
                        "सामान्य हेमेटोपोएसिस, 531; "
                        "श्वेत कोशिकाओं के विकार, 533; "
                        "ल्यूकोपेनिया, 533"
                    )
                },
                output_path=output_path,
            )
            exported = load_docx(output_path)

        exported_table = next(block for block in exported.blocks if block.type == "table")
        self.assertEqual(
            exported_table.metadata["rows"],
            [
                ["अध्याय सामग्री", ""],
                ["सामान्य हेमेटोपोएसिस", "531"],
                ["श्वेत कोशिकाओं के विकार", "533"],
                ["ल्यूकोपेनिया", "533"],
            ],
        )
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)


def _translations_by_type(parsed, translated_text_by_type: dict[str, str]) -> dict[str, str]:
    translations: dict[str, str] = {}
    for block in parsed.blocks:
        translated_text = translated_text_by_type.get(block.type)
        if block.translate and block.text.strip() and translated_text is not None:
            translations[block.block_id] = translated_text
    return translations


def _write_heading_hierarchy_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Chapter Title</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
      <w:r><w:t>Section Title</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading3"/></w:pPr>
      <w:r><w:t>Subsection Title</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Paragraph below headings.</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_formula_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    <w:p>
      <w:r><w:t>Solve this equation: </w:t></w:r>
      <m:oMath><m:r><m:t>x+1=2</m:t></m:r></m:oMath>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Formula</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><m:oMath><m:r><m:t>x+1=2</m:t></m:r></m:oMath></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_merged_table_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:trPr><w:tblHeader/></w:trPr>
        <w:tc>
          <w:tcPr><w:gridSpan w:val="2"/></w:tcPr>
          <w:p><w:r><w:t>Merged Header</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Left</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Right</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:tcPr><w:vMerge w:val="restart"/></w:tcPr>
          <w:p><w:r><w:t>Group</w:t></w:r></w:p>
        </w:tc>
        <w:tc><w:p><w:r><w:t>One</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:tcPr><w:vMerge/></w:tcPr>
          <w:p/>
        </w:tc>
        <w:tc><w:p><w:r><w:t>Two</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_contents_layout_table_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>C H A P T E R C O N T E N T S</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t></w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:p><w:r><w:t>Normal Hematopoiesis, 531; Disorders of White Cells, 533</w:t></w:r></w:p>
        </w:tc>
        <w:tc>
          <w:p><w:r><w:t>Leukopenia, 533; Neutropenia, 533</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_scenario_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Scenario Heading</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Scenario paragraph.</w:t></w:r>
      <w:commentRangeStart w:id="0"/>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Caption"/></w:pPr>
      <w:r><w:t>Figure 1. Scenario caption.</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:trPr><w:tblHeader/></w:trPr>
        <w:tc><w:p><w:r><w:t>Product</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Score</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Alpha</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Beta</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:p>
      <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="Scenario image" title="Image title"/><a:blip r:embed="rIdImage"/></wp:inline></w:drawing></w:r>
    </w:p>
    <w:p>
      <w:r><w:br w:type="page"/></w:r>
      <w:pPr><w:sectPr/></w:pPr>
    </w:p>
  </w:body>
</w:document>
"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdImage" Type="image" Target="media/image1.png"/>
  <Relationship Id="rIdHeader" Type="header" Target="header1.xml"/>
  <Relationship Id="rIdFooter" Type="footer" Target="footer1.xml"/>
</Relationships>
"""
    footnotes_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:footnote w:id="1"><w:p><w:r><w:t>Footnote text.</w:t></w:r></w:p></w:footnote>
</w:footnotes>
"""
    endnotes_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:endnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:endnote w:id="2"><w:p><w:r><w:t>Endnote text.</w:t></w:r></w:p></w:endnote>
</w:endnotes>
"""
    comments_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:comment w:id="0"><w:p><w:r><w:t>Reviewer comment.</w:t></w:r></w:p></w:comment>
</w:comments>
"""
    header_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Header text</w:t></w:r></w:p>
</w:hdr>
"""
    footer_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Footer text</w:t></w:r></w:p>
</w:ftr>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", rels_xml)
        docx.writestr("word/footnotes.xml", footnotes_xml)
        docx.writestr("word/endnotes.xml", endnotes_xml)
        docx.writestr("word/comments.xml", comments_xml)
        docx.writestr("word/header1.xml", header_xml)
        docx.writestr("word/footer1.xml", footer_xml)
        docx.writestr("word/media/image1.png", b"image-bytes")


if __name__ == "__main__":
    unittest.main()
