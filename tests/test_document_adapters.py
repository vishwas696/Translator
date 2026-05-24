import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from translator.documents.adapters import load_docx, load_epub, load_txt


class DocumentAdapterTests(unittest.TestCase):
    def test_load_txt_creates_paragraph_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.txt"
            path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")

            parsed = load_txt(path)

        self.assertEqual(parsed.source_format, "txt")
        self.assertEqual([block.type for block in parsed.blocks], ["paragraph", "paragraph"])
        self.assertEqual(parsed.to_translation_text(), "First paragraph.\n\nSecond paragraph.")

    def test_load_docx_detects_heading_paragraph_table_and_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.docx"
            _write_minimal_docx(path)

            parsed = load_docx(path)

        block_types = [block.type for block in parsed.blocks]
        self.assertEqual(parsed.source_format, "docx")
        self.assertIn("heading", block_types)
        self.assertIn("paragraph", block_types)
        self.assertIn("caption", block_types)
        self.assertIn("footnote", block_types)
        self.assertIn("endnote", block_types)
        self.assertIn("comment", block_types)
        self.assertIn("header", block_types)
        self.assertIn("footer", block_types)
        self.assertIn("page_break", block_types)
        self.assertIn("section_break", block_types)
        self.assertIn("table", block_types)
        self.assertIn("image", block_types)
        self.assertEqual(parsed.blocks[0].level, 1)
        linked_block = next(block for block in parsed.blocks if block.text.startswith("Open link"))
        self.assertEqual(linked_block.metadata["hyperlinks"][0]["target"], "https://example.com")
        self.assertTrue(linked_block.metadata["contains_checkbox"])
        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["rows"], [["Term", "Meaning"]])
        self.assertTrue(table_block.metadata["has_header_rows"])
        self.assertTrue(table_block.metadata["has_merged_cells"])
        self.assertTrue(table_block.metadata["contains_equations"])
        image_block = next(block for block in parsed.blocks if block.type == "image")
        self.assertFalse(image_block.translate)
        self.assertEqual(image_block.metadata["description"], "A test image")
        self.assertIn("Header text", parsed.to_translation_text())
        self.assertIn("Footnote text.", parsed.to_translation_text())

    def test_load_docx_preserves_table_cell_superscripts_and_cell_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "table-cells.docx"
            _write_docx_table_cell_text_edge_cases(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(
            table_block.metadata["rows"],
            [
                ["Cell Type", "Range"],
                ["White cells (3\u00d710\u00b3/mL)", "4.8-10.8"],
                [
                    "Red cells (3\u00d710\u00b3/mL); Adult males; Adult females",
                    "4.3-5.9; 3.5-5.0",
                ],
                ["OCR unit (3\u00d710\u00b3/mL)", "1-2"],
            ],
        )
        self.assertTrue(
            table_block.metadata["row_metadata"][1]["cells"][0]["formatting"][
                "superscript"
            ]
        )
        self.assertIn("translation_rows", table_block.metadata)
        self.assertIn("[[INLINE_0001]]", table_block.metadata["translation_rows"][1][0])

    def test_load_docx_parses_nested_tables_as_separate_table_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested-table.docx"
            _write_docx_nested_table(path)

            parsed = load_docx(path)

        table_blocks = [block for block in parsed.blocks if block.type == "table"]
        self.assertEqual(len(table_blocks), 2)
        self.assertEqual(
            table_blocks[0].metadata["rows"],
            [["Outer left", "Outer intro; Outer tail"]],
        )
        self.assertNotIn("Inner key", table_blocks[0].text)
        self.assertTrue(table_blocks[0].metadata["has_nested_tables"])
        self.assertEqual(
            table_blocks[0].metadata["row_metadata"][0]["cells"][1][
                "nested_table_count"
            ],
            1,
        )
        self.assertEqual(table_blocks[0].metadata["nesting_level"], 0)
        self.assertEqual(table_blocks[1].metadata["rows"], [["Inner key", "Inner value"]])
        self.assertEqual(table_blocks[1].metadata["nesting_level"], 1)

    def test_load_docx_extracts_content_control_inside_table_cell_as_cell_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cell-content-control.docx"
            _write_docx_table_cell_content_control(path)

            parsed = load_docx(path)

        table_blocks = [block for block in parsed.blocks if block.type == "table"]
        self.assertEqual(len(table_blocks), 1)
        self.assertEqual(table_blocks[0].metadata["rows"], [["Label", "Controlled value"]])
        cell_metadata = table_blocks[0].metadata["row_metadata"][0]["cells"][1]
        self.assertTrue(cell_metadata["contains_form_control"])
        self.assertEqual(
            cell_metadata["content_controls"],
            [{"alias": "Cell Alias", "tag": "cell-tag", "id": "456"}],
        )
        self.assertEqual(parsed.to_translation_text(), "Label\tControlled value")

    def test_load_docx_extracts_table_nested_inside_top_level_content_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sdt-table.docx"
            _write_docx_top_level_content_control_table(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["rows"], [["Inner key", "Inner value"]])
        self.assertEqual(
            table_block.metadata["content_control"],
            {"alias": "Table Alias", "tag": "table-tag", "id": "789"},
        )
        self.assertEqual(
            table_block.metadata["content_controls"],
            [{"alias": "Table Alias", "tag": "table-tag", "id": "789"}],
        )
        self.assertIn("/sdtContent/tbl[1]", table_block.metadata["tree_path"])

    def test_load_docx_adds_inline_placeholders_for_superscript_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inline-placeholders.docx"
            _write_docx_inline_placeholder_docx(path)

            parsed = load_docx(path)

        block = next(block for block in parsed.blocks if block.translate)
        self.assertEqual(block.text, "Overall Adjusted R² = 0.45")
        self.assertEqual(
            block.metadata["inline_source_text"],
            "Overall Adjusted R[[INLINE_0001]] = 0.45",
        )
        self.assertEqual(
            block.metadata["inline_placeholders"],
            [
                {
                    "token": "[[INLINE_0001]]",
                    "text": "2",
                    "display_text": "²",
                    "kind": "superscript",
                }
            ],
        )

    def test_load_docx_adds_inline_placeholder_for_inline_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inline-image.docx"
            _write_docx_inline_image_between_text(path)

            parsed = load_docx(path)

        block = next(block for block in parsed.blocks if block.translate)
        self.assertEqual(block.text, "Text before icon. Text after icon.")
        self.assertEqual(
            block.metadata["inline_source_text"],
            "Text before icon. [[INLINE_0001]]Text after icon.",
        )
        self.assertEqual(
            block.metadata["inline_placeholders"],
            [
                {
                    "token": "[[INLINE_0001]]",
                    "text": "",
                    "display_text": "",
                    "kind": "inline_image",
                }
            ],
        )

    def test_load_docx_orders_multiple_inline_image_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "multiple-inline-images.docx"
            _write_docx_multiple_inline_images(path)

            parsed = load_docx(path)

        block = next(block for block in parsed.blocks if block.translate)
        self.assertEqual(
            block.metadata["inline_source_text"],
            "[[INLINE_0001]]Start [[INLINE_0002]]middle[[INLINE_0003]]",
        )
        self.assertEqual(
            [placeholder["kind"] for placeholder in block.metadata["inline_placeholders"]],
            ["inline_image", "inline_image", "inline_image"],
        )

    def test_load_docx_table_cell_keeps_inline_image_placeholder_in_translation_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "table-inline-image.docx"
            _write_docx_table_cell_inline_image(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["rows"], [["Label", "Before after"]])
        self.assertEqual(
            table_block.metadata["translation_rows"],
            [["Label", "Before [[INLINE_0001]]after"]],
        )
        self.assertEqual(
            table_block.metadata["row_metadata"][0]["cells"][1]["inline_placeholders"][0]["kind"],
            "inline_image",
        )

    def test_load_docx_detects_top_level_content_control_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "content-control.docx"
            _write_docx_top_level_content_control_docx(path)

            parsed = load_docx(path)

        block = next(block for block in parsed.blocks if block.text == "Content control value")
        self.assertEqual(block.type, "paragraph")
        self.assertEqual(
            block.metadata["content_control"],
            {"alias": "Alias", "tag": "tag-value", "id": "123"},
        )

    def test_load_docx_detects_non_english_contents_layout_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hindi-contents.docx"
            _write_docx_non_english_contents_layout_table(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["table_role"], "toc_layout")
        self.assertEqual(table_block.metadata["table_role_confidence"], "high")
        self.assertTrue(table_block.metadata["is_layout_table"])

    def test_load_docx_detects_semantic_toc_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "semantic-toc.docx"
            _write_docx_semantic_toc_table(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["table_role"], "toc_layout")
        self.assertEqual(table_block.metadata["table_role_confidence"], "high")
        self.assertIn("toc_field", table_block.metadata["table_role_signals"])
        self.assertIn("toc_paragraph_style", table_block.metadata["table_role_signals"])

    def test_load_docx_marks_short_weak_toc_as_possible_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "possible-toc.docx"
            _write_docx_possible_toc_table(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["table_role"], "possible_toc_layout")
        self.assertEqual(table_block.metadata["table_role_confidence"], "medium")
        self.assertFalse(table_block.metadata["is_layout_table"])

    def test_load_docx_keeps_numeric_grid_as_data_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "numeric-table.docx"
            _write_docx_numeric_data_table(path)

            parsed = load_docx(path)

        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["table_role"], "data")
        self.assertEqual(table_block.metadata["table_role_confidence"], "high")
        self.assertFalse(table_block.metadata["is_layout_table"])

    def test_load_epub_reads_spine_order_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.epub"
            _write_minimal_epub(path)

            parsed = load_epub(path)

        block_types = [block.type for block in parsed.blocks]
        self.assertEqual(parsed.source_format, "epub")
        self.assertIn("metadata_title", block_types)
        self.assertIn("metadata_creator", block_types)
        self.assertIn("metadata_language", block_types)
        self.assertIn("image", block_types)
        self.assertIn("toc", block_types)
        self.assertIn("heading", block_types)
        self.assertIn("paragraph", block_types)
        self.assertIn("page_break", block_types)
        self.assertIn("footnote", block_types)
        self.assertIn("caption", block_types)
        self.assertIn("table", block_types)
        self.assertIn("code", block_types)
        self.assertNotIn("Hidden text", [block.text for block in parsed.blocks])
        self.assertEqual(next(block for block in parsed.blocks if block.type == "heading").text, "Chapter One")
        cover_block = next(block for block in parsed.blocks if block.metadata.get("is_cover"))
        self.assertEqual(cover_block.metadata["src"], "OEBPS/cover.png")
        paragraph_block = next(block for block in parsed.blocks if block.text.startswith("Hello EPUB"))
        self.assertTrue(paragraph_block.metadata["formatting"]["italic"])
        self.assertEqual(paragraph_block.metadata["hrefs"], ["https://example.com"])
        self.assertTrue(paragraph_block.metadata["contains_math"])
        self.assertTrue(paragraph_block.metadata["contains_page_break"])
        image_block = next(block for block in parsed.blocks if block.metadata.get("src") == "image.png")
        self.assertEqual(image_block.metadata["alt"], "A test image")
        self.assertEqual(image_block.metadata["title"], "Image title")
        table_block = next(block for block in parsed.blocks if block.type == "table")
        self.assertEqual(table_block.metadata["rows"], [["A", "x=1"]])
        self.assertTrue(table_block.metadata["has_header_cells"])
        self.assertTrue(table_block.metadata["has_merged_cells"])
        self.assertTrue(table_block.metadata["row_metadata"][0]["cells"][1]["looks_formula_like"])


def _write_minimal_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Chapter One</w:t></w:r>
    </w:p>
    <w:p>
      <w:hyperlink r:id="rId2"><w:r><w:t>Open link</w:t></w:r></w:hyperlink>
      <w:r><w:t> </w:t></w:r>
      <w:r><w:rPr><w:b/><w:i/><w:vertAlign w:val="superscript"/></w:rPr><w:t>Hello world.</w:t></w:r>
      <w:sdt><w:sdtPr><w:checkBox><w:checked w:val="1"/></w:checkBox></w:sdtPr><w:sdtContent><w:r><w:t>Yes</w:t></w:r></w:sdtContent></w:sdt>
      <w:bookmarkStart w:id="1" w:name="here"/>
      <w:r><w:instrText>REF here</w:instrText></w:r>
      <w:commentRangeStart w:id="0"/>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="Caption"/></w:pPr>
      <w:r><w:t>Figure 1. A caption.</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:trPr><w:tblHeader/></w:trPr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Term</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><m:oMath/><w:r><w:t>Meaning</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:p>
      <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="A test image" title="Image title"/><a:blip r:embed="rId1"/></wp:inline></w:drawing></w:r>
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
  <Relationship Id="rId1" Type="image" Target="media/image1.png"/>
  <Relationship Id="rId2" Type="hyperlink" Target="https://example.com" TargetMode="External"/>
  <Relationship Id="rId3" Type="header" Target="header1.xml"/>
  <Relationship Id="rId4" Type="footer" Target="footer1.xml"/>
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
        docx.writestr("word/media/image1.png", b"fake")


def _write_docx_table_cell_text_edge_cases(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Cell Type</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Range</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:p>
            <w:r><w:t>White cells (3&#215;10</w:t></w:r>
            <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>3</w:t></w:r>
            <w:r><w:t>/mL)</w:t></w:r>
          </w:p>
        </w:tc>
        <w:tc><w:p><w:r><w:t>4.8-10.8</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:p>
            <w:r><w:t>Red cells (3&#215;10</w:t></w:r>
            <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>3</w:t></w:r>
            <w:r><w:t>/mL)</w:t></w:r>
          </w:p>
          <w:p><w:r><w:t>Adult males</w:t></w:r></w:p>
          <w:p><w:r><w:t>Adult females</w:t></w:r></w:p>
        </w:tc>
        <w:tc>
          <w:p><w:r><w:t>4.3-5.9</w:t></w:r></w:p>
          <w:p><w:r><w:t>3.5-5.0</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:p>
            <w:r><w:t>OCR unit (310</w:t></w:r>
            <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>3</w:t></w:r>
            <w:r><w:t>/mL)</w:t></w:r>
          </w:p>
        </w:tc>
        <w:tc><w:p><w:r><w:t>1-2</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_inline_placeholder_docx(path: Path) -> None:
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


def _write_docx_inline_image_between_text(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:body>
    <w:p>
      <w:r><w:t xml:space="preserve">Text before icon. </w:t></w:r>
      <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Icon" descr="Inline icon"/><a:blip r:embed="rIdIcon"/></wp:inline></w:drawing></w:r>
      <w:r><w:t>Text after icon.</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdIcon"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="media/icon.png"/>
</Relationships>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", relationships_xml)
        docx.writestr("word/media/icon.png", b"icon")


def _write_docx_multiple_inline_images(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:body>
    <w:p>
      <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Icon 1"/><a:blip r:embed="rIdIcon1"/></wp:inline></w:drawing></w:r>
      <w:r><w:t xml:space="preserve">Start </w:t></w:r>
      <w:r><w:drawing><wp:inline><wp:docPr id="2" name="Icon 2"/><a:blip r:embed="rIdIcon2"/></wp:inline></w:drawing></w:r>
      <w:r><w:t>middle</w:t></w:r>
      <w:r><w:drawing><wp:inline><wp:docPr id="3" name="Icon 3"/><a:blip r:embed="rIdIcon3"/></wp:inline></w:drawing></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdIcon1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/icon1.png"/>
  <Relationship Id="rIdIcon2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/icon2.png"/>
  <Relationship Id="rIdIcon3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/icon3.png"/>
</Relationships>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", relationships_xml)
        docx.writestr("word/media/icon1.png", b"icon1")
        docx.writestr("word/media/icon2.png", b"icon2")
        docx.writestr("word/media/icon3.png", b"icon3")


def _write_docx_inline_image_and_superscript(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:body>
    <w:p>
      <w:r><w:t xml:space="preserve">Icon </w:t></w:r>
      <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Icon"/><a:blip r:embed="rIdIcon"/></wp:inline></w:drawing></w:r>
      <w:r><w:t xml:space="preserve"> Adjusted R</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>
      <w:r><w:t> value</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdIcon" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/icon.png"/>
</Relationships>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", relationships_xml)
        docx.writestr("word/media/icon.png", b"icon")


def _write_docx_table_cell_inline_image(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Label</w:t></w:r></w:p></w:tc>
        <w:tc>
          <w:p>
            <w:r><w:t xml:space="preserve">Before </w:t></w:r>
            <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Icon"/><a:blip r:embed="rIdIcon"/></wp:inline></w:drawing></w:r>
            <w:r><w:t>after</w:t></w:r>
          </w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdIcon" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/icon.png"/>
</Relationships>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", relationships_xml)
        docx.writestr("word/media/icon.png", b"icon")


def _write_docx_top_level_content_control_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:sdt>
      <w:sdtPr>
        <w:alias w:val="Alias"/>
        <w:tag w:val="tag-value"/>
        <w:id w:val="123"/>
      </w:sdtPr>
      <w:sdtContent>
        <w:p><w:r><w:t>Content control value</w:t></w:r></w:p>
      </w:sdtContent>
    </w:sdt>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_nested_table(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc>
          <w:p><w:r><w:t>Outer left</w:t></w:r></w:p>
        </w:tc>
        <w:tc>
          <w:p><w:r><w:t>Outer intro</w:t></w:r></w:p>
          <w:tbl>
            <w:tr>
              <w:tc><w:p><w:r><w:t>Inner key</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Inner value</w:t></w:r></w:p></w:tc>
            </w:tr>
          </w:tbl>
          <w:p><w:r><w:t>Outer tail</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_table_cell_content_control(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Label</w:t></w:r></w:p></w:tc>
        <w:tc>
          <w:sdt>
            <w:sdtPr>
              <w:alias w:val="Cell Alias"/>
              <w:tag w:val="cell-tag"/>
              <w:id w:val="456"/>
            </w:sdtPr>
            <w:sdtContent>
              <w:p><w:r><w:t>Controlled value</w:t></w:r></w:p>
            </w:sdtContent>
          </w:sdt>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_top_level_content_control_table(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:sdt>
      <w:sdtPr>
        <w:alias w:val="Table Alias"/>
        <w:tag w:val="table-tag"/>
        <w:id w:val="789"/>
      </w:sdtPr>
      <w:sdtContent>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Inner key</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Inner value</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
      </w:sdtContent>
    </w:sdt>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_non_english_contents_layout_table(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>विषय-सूची</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t></w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc>
          <w:p><w:r><w:t>सामान्य हेमेटोपोएसिस, 531; श्वेत कोशिकाओं के विकार, 533; ल्यूकोपेनिया, 533; न्यूट्रोपेनिया, 533</w:t></w:r></w:p>
        </w:tc>
        <w:tc>
          <w:p><w:r><w:t>लिम्फैडेनाइटिस, 535; लिम्फोमा, 538; मायलोमा, 553; थाइमोमा, 575</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_semantic_toc_table(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc>
          <w:p>
            <w:r><w:instrText>TOC \\o "1-3" \\h \\z \\u</w:instrText></w:r>
          </w:p>
          <w:p>
            <w:pPr><w:pStyle w:val="TOC1"/></w:pPr>
            <w:r><w:t>Chapter One</w:t></w:r>
            <w:tab/>
            <w:r><w:t>3</w:t></w:r>
          </w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_possible_toc_table(path: Path) -> None:
    lead_paragraphs = "\n".join(
        f"<w:p><w:r><w:t>Lead paragraph {index}</w:t></w:r></w:p>"
        for index in range(12)
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {lead_paragraphs}
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Alpha, 1; Beta, 2; Gamma, 3</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_docx_numeric_data_table(path: Path) -> None:
    rows_xml = "\n".join(
        "<w:tr>"
        f"<w:tc><w:p><w:r><w:t>{index}</w:t></w:r></w:p></w:tc>"
        f"<w:tc><w:p><w:r><w:t>{index * 10}</w:t></w:r></w:p></w:tc>"
        f"<w:tc><w:p><w:r><w:t>{index * 100}</w:t></w:r></w:p></w:tc>"
        "</w:tr>"
        for index in range(1, 6)
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      {rows_xml}
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_minimal_epub(path: Path) -> None:
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf_xml = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         version="3.0">
  <metadata>
    <dc:title>Sample Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
  </manifest>
  <spine>
    <itemref idref="chap1"/>
  </spine>
</package>
"""
    chapter_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Chapter One</h1>
    <p>Hello EPUB <em>reader</em>. <a href="https://example.com">Link</a> <span epub:type="pagebreak" id="page-1">1</span> <math><mi>x</mi><mo>=</mo><mn>1</mn></math></p>
    <p hidden="hidden">Hidden text</p>
    <aside epub:type="footnote">Footnote body.</aside>
    <pre>for x in range(3): pass</pre>
    <figure>
      <img src="image.png" alt="A test image" title="Image title"/>
      <figcaption>A useful caption.</figcaption>
    </figure>
    <table><tr><th>A</th><td colspan="2">x=1</td></tr></table>
  </body>
</html>
"""
    nav_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Chapter One</a></li></ol></nav>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)
        epub.writestr("OEBPS/nav.xhtml", nav_xml)
        epub.writestr("OEBPS/cover.png", b"fake")


if __name__ == "__main__":
    unittest.main()
