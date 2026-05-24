import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from translator.documents.adapters import load_docx, load_epub, load_txt
from translator.documents.writers import write_translated_document
from test_document_adapters import _write_minimal_docx, _write_minimal_epub


class DocumentWriterTests(unittest.TestCase):
    def test_writes_translated_txt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            output_path = Path(temp_dir) / "translated.txt"
            source_path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
            parsed = load_txt(source_path)

            report = write_translated_document(
                parsed_document=parsed,
                translated_text="Primer parrafo.\n\nSegundo parrafo.",
                output_path=output_path,
            )

            self.assertEqual(output_path.read_text(encoding="utf-8"), "Primer parrafo.\n\nSegundo parrafo.")
            self.assertEqual(report.applied_unit_count, 2)

    def test_writes_translated_docx_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_minimal_docx(source_path)
            parsed = load_docx(source_path)
            translated_text = _translated_text_for_blocks(
                parsed.blocks,
                table_text="Termino\tSignificado",
            )

            report = write_translated_document(
                parsed_document=parsed,
                translated_text=translated_text,
                output_path=output_path,
            )
            exported = load_docx(output_path)

            exported_text = exported.to_translation_text()
            self.assertIn("Translated heading", exported_text)
            self.assertIn("Translated paragraph", exported_text)
            self.assertIn("Termino\tSignificado", exported_text)
            self.assertIn("Translated footnote", exported_text)
            self.assertIn("Translated endnote", exported_text)
            self.assertIn("Translated comment", exported_text)
            self.assertIn("Translated header", exported_text)
            self.assertIn("Translated footer", exported_text)
            self.assertEqual(report.translatable_block_count, report.applied_unit_count)

    def test_docx_footer_page_fields_keep_translated_text_between_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            output_path = Path(temp_dir) / "translated.docx"
            _write_minimal_docx(source_path)
            _replace_zip_entry(
                source_path,
                "word/footer1.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:r><w:t xml:space="preserve">Page </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve">PAGE</w:instrText><w:fldChar w:fldCharType="end"/></w:r>
    <w:r><w:t xml:space="preserve"> of </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve">NUMPAGES</w:instrText><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
</w:ftr>
""",
            )
            parsed = load_docx(source_path)
            footer = next(block for block in parsed.blocks if block.type == "footer")

            write_translated_document(
                parsed_document=parsed,
                output_path=output_path,
                translations_by_block_id={
                    footer.block_id: "Seite [[INLINE_0001]] von [[INLINE_0002]]"
                },
            )

            footer_xml = _zip_entry_text(output_path, "word/footer1.xml")
            self.assertIn("<w:t xml:space=\"preserve\">Seite </w:t>", footer_xml)
            self.assertIn("<w:instrText xml:space=\"preserve\">PAGE</w:instrText>", footer_xml)
            self.assertIn("<w:t xml:space=\"preserve\"> von </w:t>", footer_xml)
            self.assertIn("<w:instrText xml:space=\"preserve\">NUMPAGES</w:instrText>", footer_xml)
            self.assertNotIn(">Seite  von <", footer_xml)

    def test_writes_translated_epub_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.epub"
            output_path = Path(temp_dir) / "translated.epub"
            _write_minimal_epub(source_path)
            parsed = load_epub(source_path)
            translated_text = _translated_text_for_blocks(
                parsed.blocks,
                table_text="Columna A\tColumna B",
            )

            report = write_translated_document(
                parsed_document=parsed,
                translated_text=translated_text,
                output_path=output_path,
            )
            exported = load_epub(output_path)

            exported_text = exported.to_translation_text()
            self.assertIn("Translated metadata_title", exported_text)
            self.assertIn("Translated heading", exported_text)
            self.assertIn("Translated paragraph", exported_text)
            self.assertIn("Translated footnote", exported_text)
            self.assertIn("Translated caption", exported_text)
            self.assertIn("Columna A\tColumna B", exported_text)
            self.assertEqual(report.translatable_block_count, report.applied_unit_count)


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


def _zip_entry_text(path: Path, name: str) -> str:
    with ZipFile(path) as docx:
        return docx.read(name).decode("utf-8")


def _replace_zip_entry(path: Path, name: str, text: str) -> None:
    with ZipFile(path, "r") as docx:
        entries = {entry.filename: docx.read(entry.filename) for entry in docx.infolist()}
    entries[name] = text.encode("utf-8")
    with ZipFile(path, "w") as docx:
        for entry_name, data in entries.items():
            docx.writestr(entry_name, data)


if __name__ == "__main__":
    unittest.main()
