import tempfile
import unittest
from pathlib import Path

from translator.documents.adapters import load_docx
from translator.documents.docx_enrichment import enrich_docx_with_docx2python
from zipfile import ZipFile


class Docx2PythonEnrichmentTests(unittest.TestCase):
    def test_enrichment_adds_metadata_without_changing_block_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            _write_docx2python_friendly_docx(source_path)
            parsed = load_docx(source_path)

            result = enrich_docx_with_docx2python(parsed)

        self.assertEqual(len(result.parsed_document.blocks), len(parsed.blocks))
        self.assertIn(result.report["status"], {"ok", "partial"})
        enriched_blocks = [
            block
            for block in result.parsed_document.blocks
            if "docx2python_enrichment" in block.metadata
        ]
        self.assertGreater(len(enriched_blocks), 0)
        self.assertIn("run_style_counts", enriched_blocks[0].metadata["docx2python_enrichment"])


def _write_docx2python_friendly_docx(path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    package_relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Chapter One</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Overall Adjusted R</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>
      <w:r><w:t> = 0.45</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Term</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Meaning</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", package_relationships)
        docx.writestr("word/document.xml", document_xml)


if __name__ == "__main__":
    unittest.main()
