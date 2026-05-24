import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from document_adapters import load_docx, load_epub


class DocumentAdapterEdgeCaseTests(unittest.TestCase):
    def test_docx_styles_lists_formatting_hidden_bookmarks_and_text_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "edge.docx"
            _write_docx_adapter_edge_cases(path)

            parsed = load_docx(path)

        blocks_by_text = {block.text: block for block in parsed.blocks if block.text}

        self.assertEqual(blocks_by_text["Quoted text."].type, "quote")
        self.assertEqual(blocks_by_text["TOC entry"].type, "toc_entry")
        self.assertEqual(blocks_by_text["Index entry"].type, "index_entry")
        self.assertEqual(blocks_by_text["Reference entry"].type, "reference")

        list_block = blocks_by_text["Nested list item"]
        self.assertEqual(list_block.type, "list_item")
        self.assertEqual(list_block.metadata["list"], {"num_id": "7", "level": "1"})

        formatted_block = blocks_by_text["Formatted text"]
        self.assertTrue(formatted_block.metadata["formatting"]["bold"])
        self.assertTrue(formatted_block.metadata["formatting"]["italic"])
        self.assertTrue(formatted_block.metadata["formatting"]["underline"])
        self.assertTrue(formatted_block.metadata["formatting"]["small_caps"])
        self.assertTrue(formatted_block.metadata["formatting"]["superscript"])
        self.assertTrue(formatted_block.metadata["formatting"]["subscript"])

        self.assertTrue(blocks_by_text["Hidden text"].metadata["contains_hidden_text"])
        self.assertTrue(blocks_by_text["Inside text box"].metadata["contains_text_box"])
        self.assertEqual(
            blocks_by_text["Bookmarked text"].metadata["bookmarks"],
            [{"id": "2", "name": "bookmark_here"}],
        )
        self.assertEqual(blocks_by_text["Bookmarked text"].metadata["field_codes"], ["REF bookmark_here"])

        scene_break = blocks_by_text["***"]
        self.assertEqual(scene_break.type, "special")
        self.assertTrue(scene_break.metadata["preserve_exact"])

        self.assertEqual(blocks_by_text["Line one\nLine two"].text, "Line one\nLine two")

    def test_epub_lists_language_sidebar_scene_breaks_and_formatting_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "edge.epub"
            _write_epub_adapter_edge_cases(path)

            parsed = load_epub(path)

        blocks_by_text = {block.text: block for block in parsed.blocks if block.text}
        self.assertNotIn("Hidden text", blocks_by_text)

        list_block = blocks_by_text["First item"]
        self.assertEqual(list_block.type, "list_item")
        self.assertEqual(list_block.metadata["list_type"], "ol")
        self.assertEqual(list_block.metadata["list_level"], 1)

        linked_block = blocks_by_text["Bonjour"]
        self.assertEqual(linked_block.metadata["lang"], "fr")
        self.assertEqual(linked_block.metadata["hrefs"], ["#note"])

        self.assertEqual(blocks_by_text["Endnote body."].type, "endnote")
        self.assertEqual(blocks_by_text["Sidebar body."].type, "special")

        scene_break = blocks_by_text["***"]
        self.assertEqual(scene_break.type, "special")
        self.assertTrue(scene_break.metadata["preserve_exact"])

        formatted = blocks_by_text["Power 2 index i"]
        self.assertTrue(formatted.metadata["formatting"]["underline"])
        self.assertTrue(formatted.metadata["formatting"]["small_caps"])
        self.assertTrue(formatted.metadata["formatting"]["superscript"])
        self.assertTrue(formatted.metadata["formatting"]["subscript"])


def _write_docx_adapter_edge_cases(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Quote"/></w:pPr><w:r><w:t>Quoted text.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr><w:r><w:t>TOC entry</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Index1"/></w:pPr><w:r><w:t>Index entry</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Bibliography"/></w:pPr><w:r><w:t>Reference entry</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="7"/></w:numPr></w:pPr>
      <w:r><w:t>Nested list item</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:rPr><w:b/><w:i/><w:u/><w:smallCaps/><w:vertAlign w:val="superscript"/></w:rPr><w:t>Formatted text</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t></w:t></w:r>
    </w:p>
    <w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden text</w:t></w:r></w:p>
    <w:p><w:r><w:txbxContent><w:p><w:r><w:t>Inside text box</w:t></w:r></w:p></w:txbxContent></w:r></w:p>
    <w:p>
      <w:bookmarkStart w:id="2" w:name="bookmark_here"/>
      <w:r><w:t>Bookmarked text</w:t></w:r>
      <w:r><w:instrText>REF bookmark_here</w:instrText></w:r>
    </w:p>
    <w:p><w:r><w:t>***</w:t></w:r></w:p>
    <w:p><w:r><w:t>Line one</w:t><w:br/><w:t>Line two</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_epub_adapter_edge_cases(path: Path) -> None:
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
    <ol><li>First item</li></ol>
    <p lang="fr"><a href="#note">Bonjour</a></p>
    <aside epub:type="endnote">Endnote body.</aside>
    <aside class="sidebar">Sidebar body.</aside>
    <p hidden="hidden">Hidden text</p>
    <p>***</p>
    <p class="smallcaps" style="text-decoration: underline">Power <sup>2</sup> index <sub>i</sub></p>
  </body>
</html>
"""
    with ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", container_xml)
        epub.writestr("OEBPS/content.opf", opf_xml)
        epub.writestr("OEBPS/chapter1.xhtml", chapter_xml)


if __name__ == "__main__":
    unittest.main()
