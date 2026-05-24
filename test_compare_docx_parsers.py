import tempfile
import unittest
from pathlib import Path

from compare_docx_parsers import (
    compare_docx_parsers,
    is_docx2python_media_placeholder,
    is_docx2python_note_separator,
)
from test_document_adapters import _write_minimal_docx


class DocxParserComparisonTests(unittest.TestCase):
    def test_compare_docx_parsers_returns_independent_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.docx"
            _write_minimal_docx(source_path)

            comparison = compare_docx_parsers(source_path)
            report = comparison.to_dict()

        self.assertEqual(report["source_path"], str(source_path))
        self.assertGreaterEqual(report["our_parser"]["total_blocks"], 1)
        self.assertIn("table_shapes", report["our_parser"])
        self.assertIn("table_shapes", report["docx2python"])
        self.assertIn("tables", report["deltas"])
        self.assertIn("table_shape_differences", report)
        self.assertIsInstance(report["likely_misses"], list)

    def test_docx2python_artifact_filters(self) -> None:
        self.assertTrue(is_docx2python_media_placeholder("----media/image1.png----"))
        self.assertFalse(is_docx2python_media_placeholder("Figure 1. Chart image."))
        self.assertTrue(is_docx2python_note_separator("footnote-1)"))
        self.assertTrue(is_docx2python_note_separator("endnote0)"))
        self.assertFalse(
            is_docx2python_note_separator(
                "footnote1)\t True footnote part should count."
            )
        )


if __name__ == "__main__":
    unittest.main()
