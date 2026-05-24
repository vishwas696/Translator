import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from bs4 import BeautifulSoup

from translator.documents.adapters import load_epub
from translator.documents.writers import write_translated_document


class EpubWriterEdgeCaseTests(unittest.TestCase):
    def test_epub_export_preserves_package_structure_and_nontranslated_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "edge.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_edge_case_epub(source_path)

            parsed = load_epub(source_path)
            translated_text = _translated_text_for_blocks(
                parsed.blocks,
                {
                    "metadata_title": "Translated Title",
                    "metadata_description": "Translated description.",
                    "heading": "Translated Chapter",
                    "paragraph": "Translated paragraph.",
                    "caption": "Translated caption.",
                    "table": "Header A\tHeader B\nUno\tDos",
                    "footnote": "Translated footnote.",
                },
            )

            report = write_translated_document(
                parsed_document=parsed,
                translated_text=translated_text,
                output_path=output_path,
            )

            self.assertEqual(report.translatable_block_count, report.applied_unit_count)
            self.assertNotIn(
                "Translated text unit count does not match translatable block count.",
                " ".join(report.warnings),
            )

            with ZipFile(output_path) as exported:
                self.assertEqual(exported.read("OEBPS/cover.png"), b"cover-bytes")
                self.assertEqual(exported.read("OEBPS/images/inline.png"), b"image-bytes")

                opf = exported.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn("<dc:title>Translated Title</dc:title>", opf)
                self.assertIn("<dc:description>Translated description.</dc:description>", opf)
                self.assertIn("<dc:creator>Original Author</dc:creator>", opf)
                self.assertIn("<dc:language>en</dc:language>", opf)
                self.assertIn('properties="cover-image"', opf)

                nav = BeautifulSoup(exported.read("OEBPS/nav.xhtml"), "html.parser")
                self.assertEqual(nav.find("nav").get_text(" ", strip=True), "Chapter One Appendix")
                self.assertEqual(nav.find("a", href="chapter1.xhtml").get_text(strip=True), "Chapter One")

                chapter = BeautifulSoup(exported.read("OEBPS/chapter1.xhtml"), "html.parser")
                self.assertEqual(chapter.find("h1").get_text(strip=True), "Translated Chapter")
                self.assertEqual(chapter.find("p", id="main").get_text(strip=True), "Translated paragraph.")
                self.assertEqual(chapter.find("p", id="hidden").get_text(strip=True), "Hidden source text")
                self.assertEqual(chapter.find("pre").get_text(strip=True), "for item in range(3): pass")
                self.assertEqual(chapter.find("span", id="page-9").get_text(strip=True), "9")
                self.assertEqual(chapter.find("figcaption").get_text(strip=True), "Translated caption.")
                self.assertEqual(chapter.find("aside").get_text(strip=True), "Translated footnote.")

                cells = [cell.get_text(strip=True) for cell in chapter.find("table").find_all(["th", "td"])]
                self.assertEqual(cells, ["Header A", "Header B", "Uno", "Dos"])

    def test_epub_table_shape_mismatch_warns_and_keeps_full_text_in_first_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "edge.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_edge_case_epub(source_path)

            parsed = load_epub(source_path)
            translated_text = _translated_text_for_blocks(
                parsed.blocks,
                {
                    "metadata_title": "Translated Title",
                    "metadata_description": "Translated description.",
                    "heading": "Translated Chapter",
                    "paragraph": "Translated paragraph.",
                    "caption": "Translated caption.",
                    "table": "Only one cell",
                    "footnote": "Translated footnote.",
                },
            )

            report = write_translated_document(
                parsed_document=parsed,
                translated_text=translated_text,
                output_path=output_path,
            )

            self.assertIn(
                "A translated EPUB table did not preserve row/cell shape; "
                "placing the full translated table text in the first cell.",
                report.warnings,
            )
            with ZipFile(output_path) as exported:
                chapter = BeautifulSoup(exported.read("OEBPS/chapter1.xhtml"), "html.parser")
                cells = [cell.get_text(strip=True) for cell in chapter.find("table").find_all(["th", "td"])]
                self.assertEqual(cells, ["Only one cell", "Header B", "One", "Two"])

    def test_missing_epub_translation_units_preserve_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "edge.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_edge_case_epub(source_path)

            parsed = load_epub(source_path)
            report = write_translated_document(
                parsed_document=parsed,
                translated_text="Translated Title",
                output_path=output_path,
            )

            self.assertLess(report.applied_unit_count, report.translatable_block_count)
            self.assertTrue(
                any(warning.startswith("Missing translated text for ") for warning in report.warnings)
            )

            exported = load_epub(output_path)
            exported_text = exported.to_translation_text()
            self.assertIn("Translated Title", exported_text)
            self.assertIn("Original description.", exported_text)
            self.assertIn("Chapter One", exported_text)
            self.assertIn("Source paragraph", exported_text)
            self.assertIn("Original footnote.", exported_text)
            self.assertIn("Header A\tHeader B\nOne\tTwo", exported_text)


def _translated_text_for_blocks(blocks, translations_by_type: dict[str, str]) -> str:
    units: list[str] = []
    for block in blocks:
        if not block.translate or not block.text.strip():
            continue
        units.append(translations_by_type[block.type])
    return "\n\n".join(units)


def _write_edge_case_epub(path: Path) -> None:
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
    <dc:title>Original Title</dc:title>
    <dc:creator>Original Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:description>Original description.</dc:description>
    <meta name="cover" content="cover"/>
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="inline-image" href="images/inline.png" media-type="image/png"/>
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
    <p id="main">Source paragraph <em>with emphasis</em>.</p>
    <p id="hidden" hidden="hidden">Hidden source text</p>
    <pre>for item in range(3): pass</pre>
    <span epub:type="pagebreak" id="page-9">9</span>
    <figure>
      <img src="images/inline.png" alt="Inline image"/>
      <figcaption>Original caption.</figcaption>
    </figure>
    <table>
      <tr><th>Header A</th><th>Header B</th></tr>
      <tr><td>One</td><td>Two</td></tr>
    </table>
    <aside epub:type="footnote">Original footnote.</aside>
  </body>
</html>
"""
    nav_xml = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
        <li><a href="appendix.xhtml">Appendix</a></li>
      </ol>
    </nav>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)
        epub.writestr("OEBPS/nav.xhtml", nav_xml)
        epub.writestr("OEBPS/cover.png", b"cover-bytes")
        epub.writestr("OEBPS/images/inline.png", b"image-bytes")


if __name__ == "__main__":
    unittest.main()
