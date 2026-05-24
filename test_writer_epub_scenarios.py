import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from document_adapters import load_epub
from document_writers import write_translated_document


class EpubWriterScenarioTests(unittest.TestCase):
    def test_epub_heading_hierarchy_translates_and_preserves_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "headings.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_heading_hierarchy_epub(source_path)

            parsed = load_epub(source_path)
            heading_blocks = [block for block in parsed.blocks if block.type == "heading"]
            self.assertEqual([block.level for block in heading_blocks], [1, 2, 3])

            translations = {
                heading_blocks[0].block_id: "TR Chapter Heading",
                heading_blocks[1].block_id: "TR Section Heading",
                heading_blocks[2].block_id: "TR Subsection Heading",
            }
            paragraph_block = next(block for block in parsed.blocks if block.type == "paragraph")
            translations[paragraph_block.block_id] = "TR Paragraph."

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id=translations,
                output_path=output_path,
            )
            exported = load_epub(output_path)

        exported_headings = [block for block in exported.blocks if block.type == "heading"]
        self.assertEqual([block.level for block in exported_headings], [1, 2, 3])
        self.assertEqual(
            [block.text for block in exported_headings],
            ["TR Chapter Heading", "TR Section Heading", "TR Subsection Heading"],
        )
        self.assertIn("TR Paragraph.", exported.to_translation_text())
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)
        self.assertEqual(report.warnings, [])

    def test_epub_scenario_translates_content_and_preserves_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "scenario.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_scenario_epub(source_path)

            parsed = load_epub(source_path)
            translations = _scenario_translations(parsed)

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id=translations,
                output_path=output_path,
            )
            exported = load_epub(output_path)

            self.assertEqual(report.translatable_block_count, report.applied_unit_count)
            self.assertEqual(report.warnings, [])

            exported_text = exported.to_translation_text()
            self.assertIn("Translated Package Title", exported_text)
            self.assertIn("Translated package description.", exported_text)
            self.assertIn("Translated Chapter Heading", exported_text)
            self.assertIn("Translated body paragraph.", exported_text)
            self.assertIn("Translated first list item.", exported_text)
            self.assertIn("Translated second list item.", exported_text)
            self.assertIn("Translated quoted text.", exported_text)
            self.assertIn("Translated footnote body.", exported_text)
            self.assertIn("Translated figure caption.", exported_text)
            self.assertIn("Translated H1\tTranslated H2\nTranslated R1C1\tTranslated R1C2", exported_text)

            exported_blocks = exported.blocks
            self.assertEqual(_first_text(exported_blocks, "toc"), "Chapter One Tables")
            self.assertEqual(_first_text(exported_blocks, "code"), "const value = 1;")
            cover_block = next(block for block in exported_blocks if block.metadata.get("is_cover"))
            self.assertFalse(cover_block.translate)
            self.assertEqual(cover_block.metadata["src"], "OEBPS/cover.png")
            inline_image = next(
                block
                for block in exported_blocks
                if block.type == "image" and block.metadata.get("src") == "images/inline.png"
            )
            self.assertEqual(inline_image.metadata["alt"], "Inline image alt")

            with ZipFile(output_path) as exported_zip:
                self.assertEqual(exported_zip.read("OEBPS/cover.png"), b"cover-bytes")
                self.assertEqual(exported_zip.read("OEBPS/images/inline.png"), b"inline-bytes")
                nav_xml = exported_zip.read("OEBPS/nav.xhtml").decode("utf-8")
                self.assertIn("Chapter One", nav_xml)
                self.assertIn("Tables", nav_xml)
                code_xml = exported_zip.read("OEBPS/chapter1.xhtml").decode("utf-8")
                self.assertIn("const value = 1;", code_xml)

    def test_epub_mathml_survives_translation_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "math.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_math_epub(source_path)

            parsed = load_epub(source_path)
            paragraph_block = next(block for block in parsed.blocks if block.type == "paragraph")
            table_block = next(block for block in parsed.blocks if block.type == "table")
            self.assertTrue(paragraph_block.metadata["contains_math"])
            self.assertTrue(table_block.metadata["contains_math"])

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    paragraph_block.block_id: "TR Solve this expression.",
                    table_block.block_id: "TR Formula\tx+1=2",
                },
                output_path=output_path,
            )
            exported = load_epub(output_path)
            with ZipFile(output_path) as exported_zip:
                chapter_xml = exported_zip.read("OEBPS/chapter1.xhtml").decode("utf-8")

        exported_paragraph = next(block for block in exported.blocks if block.type == "paragraph")
        exported_table = next(block for block in exported.blocks if block.type == "table")
        self.assertTrue(exported_paragraph.metadata["contains_math"])
        self.assertTrue(exported_table.metadata["contains_math"])
        self.assertIn("TR Solve this expression.", exported_paragraph.text)
        self.assertIn("x + 1 = 2", exported_paragraph.text)
        self.assertEqual(exported_table.metadata["rows"], [["TR Formula", "x + 1 = 2"]])
        self.assertIn("<math>", chapter_xml)
        self.assertIn("<mi>x</mi>", chapter_xml)
        self.assertIn("<mo>+</mo>", chapter_xml)
        self.assertIn("<mn>2</mn>", chapter_xml)
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)

    def test_epub_complex_table_attributes_survive_translation_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "complex-table.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_complex_table_epub(source_path)

            parsed = load_epub(source_path)
            table_block = next(block for block in parsed.blocks if block.type == "table")
            self.assertTrue(table_block.metadata["has_header_cells"])
            self.assertTrue(table_block.metadata["has_merged_cells"])
            self.assertEqual(
                table_block.metadata["row_metadata"][0]["cells"][0]["colspan"],
                "2",
            )
            self.assertEqual(
                table_block.metadata["row_metadata"][1]["cells"][0]["scope"],
                "row",
            )
            self.assertEqual(
                table_block.metadata["row_metadata"][2]["cells"][0]["rowspan"],
                "2",
            )

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id={
                    table_block.block_id: (
                        "TR Merged Header\n"
                        "TR Label\tTR Value\n"
                        "TR Group\tTR One\n"
                        "TR Two"
                    )
                },
                output_path=output_path,
            )
            exported = load_epub(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")
            with ZipFile(output_path) as exported_zip:
                chapter_xml = exported_zip.read("OEBPS/chapter1.xhtml").decode("utf-8")

        self.assertEqual(
            exported_table.metadata["rows"],
            [
                ["TR Merged Header"],
                ["TR Label", "TR Value"],
                ["TR Group", "TR One"],
                ["TR Two"],
            ],
        )
        self.assertTrue(exported_table.metadata["has_header_cells"])
        self.assertTrue(exported_table.metadata["has_merged_cells"])
        self.assertEqual(
            exported_table.metadata["row_metadata"][0]["cells"][0]["colspan"],
            "2",
        )
        self.assertEqual(
            exported_table.metadata["row_metadata"][1]["cells"][0]["scope"],
            "row",
        )
        self.assertEqual(
            exported_table.metadata["row_metadata"][2]["cells"][0]["rowspan"],
            "2",
        )
        self.assertIn('colspan="2"', chapter_xml)
        self.assertIn('rowspan="2"', chapter_xml)
        self.assertIn('scope="row"', chapter_xml)
        self.assertEqual(report.translatable_block_count, report.applied_unit_count)
        self.assertEqual(report.warnings, [])

    def test_epub_table_shape_mismatch_warns_and_falls_back_to_first_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "scenario.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_scenario_epub(source_path)

            parsed = load_epub(source_path)
            translations = _scenario_translations(parsed)
            table_block = next(block for block in parsed.blocks if block.type == "table")
            translations[table_block.block_id] = "Only one translated cell"

            report = write_translated_document(
                parsed_document=parsed,
                translations_by_block_id=translations,
                output_path=output_path,
            )
            exported = load_epub(output_path)
            exported_table = next(block for block in exported.blocks if block.type == "table")

            self.assertIn(
                "A translated EPUB table did not preserve row/cell shape; "
                "placing the full translated table text in the first cell.",
                report.warnings,
            )
            self.assertEqual(
                exported_table.text,
                "Only one translated cell\tHeader 2\nRow 1 Col 1\tRow 1 Col 2",
            )


def _scenario_translations(parsed) -> dict[str, str]:
    translations_by_text = {
        "Original Package Title": "Translated Package Title",
        "Original package description.": "Translated package description.",
        "Chapter One": "Translated Chapter Heading",
        "Body paragraph source.": "Translated body paragraph.",
        "First list item": "Translated first list item.",
        "Second list item": "Translated second list item.",
        "Quoted source text.": "Translated quoted text.",
        "Footnote source body.": "Translated footnote body.",
        "Figure caption source.": "Translated figure caption.",
        "Header 1\tHeader 2\nRow 1 Col 1\tRow 1 Col 2": (
            "Translated H1\tTranslated H2\nTranslated R1C1\tTranslated R1C2"
        ),
    }
    return {
        block.block_id: translations_by_text[block.text]
        for block in parsed.blocks
        if block.translate and block.text.strip()
    }


def _first_text(blocks, block_type: str) -> str:
    return next(block.text for block in blocks if block.type == block_type)


def _write_heading_hierarchy_epub(path: Path) -> None:
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf_xml = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>
"""
    chapter_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Chapter Heading</h1>
    <h2>Section Heading</h2>
    <h3>Subsection Heading</h3>
    <p>Paragraph below headings.</p>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)


def _write_math_epub(path: Path) -> None:
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf_xml = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>
"""
    chapter_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <p>Solve <math><mi>x</mi><mo>+</mo><mn>1</mn><mo>=</mo><mn>2</mn></math> today.</p>
    <table>
      <tr>
        <td>Formula</td>
        <td><math><mi>x</mi><mo>+</mo><mn>1</mn><mo>=</mo><mn>2</mn></math></td>
      </tr>
    </table>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)


def _write_complex_table_epub(path: Path) -> None:
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    opf_xml = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>
"""
    chapter_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <table>
      <tr><th colspan="2" scope="colgroup">Merged Header</th></tr>
      <tr><th scope="row">Label</th><td>Value</td></tr>
      <tr><td rowspan="2">Group</td><td>One</td></tr>
      <tr><td>Two</td></tr>
    </table>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)


def _write_scenario_epub(path: Path) -> None:
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
    <dc:title>Original Package Title</dc:title>
    <dc:creator>Original Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:description>Original package description.</dc:description>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover-image" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="inline-image" href="images/inline.png" media-type="image/png"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>
"""
    nav_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
        <li><a href="chapter1.xhtml#table-section">Tables</a></li>
      </ol>
    </nav>
  </body>
</html>
"""
    chapter_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>Chapter One</h1>
    <p id="body-paragraph">Body paragraph source.</p>
    <ul>
      <li>First list item</li>
      <li>Second list item</li>
    </ul>
    <blockquote>Quoted source text.</blockquote>
    <aside epub:type="footnote">Footnote source body.</aside>
    <figure>
      <img src="images/inline.png" alt="Inline image alt" title="Inline image title"/>
      <figcaption>Figure caption source.</figcaption>
    </figure>
    <pre>const value = 1;</pre>
    <table id="table-section">
      <tr><th>Header 1</th><th>Header 2</th></tr>
      <tr><td>Row 1 Col 1</td><td>Row 1 Col 2</td></tr>
    </table>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/nav.xhtml", nav_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)
        epub.writestr("OEBPS/cover.png", b"cover-bytes")
        epub.writestr("OEBPS/images/inline.png", b"inline-bytes")


if __name__ == "__main__":
    unittest.main()
