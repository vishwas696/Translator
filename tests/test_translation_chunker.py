import unittest

from translator.translation.chunker import chunk_manuscript


class TranslationChunkerTests(unittest.TestCase):
    def test_groups_complete_paragraphs_until_limit(self) -> None:
        manuscript = "One two three.\n\nFour five six seven."

        chunks = chunk_manuscript(manuscript, max_words=7)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].paragraph_ids, ["p0001", "p0002"])
        self.assertEqual(chunks[0].source_word_count, 7)
        self.assertFalse(chunks[0].contains_partial_paragraph)
        self.assertTrue(chunks[0].starts_paragraph)
        self.assertFalse(chunks[0].continues_paragraph)
        self.assertTrue(chunks[0].ends_paragraph)

    def test_splits_long_paragraph_at_last_full_stop_under_limit(self) -> None:
        manuscript = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."

        chunks = chunk_manuscript(manuscript, max_words=6)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, "Alpha beta gamma. Delta epsilon zeta.")
        self.assertEqual(chunks[0].source_word_count, 6)
        self.assertEqual(chunks[0].paragraph_ids, ["p0001"])
        self.assertTrue(chunks[0].contains_partial_paragraph)
        self.assertTrue(chunks[0].starts_paragraph)
        self.assertFalse(chunks[0].continues_paragraph)
        self.assertFalse(chunks[0].ends_paragraph)
        self.assertEqual(chunks[0].split_reason, "paragraph_too_long")

        self.assertEqual(chunks[1].text, "Eta theta iota.")
        self.assertTrue(chunks[1].contains_partial_paragraph)
        self.assertFalse(chunks[1].starts_paragraph)
        self.assertTrue(chunks[1].continues_paragraph)
        self.assertTrue(chunks[1].ends_paragraph)

    def test_splits_long_paragraph_at_word_boundary_when_no_full_stop(self) -> None:
        manuscript = "one two three four five"

        chunks = chunk_manuscript(manuscript, max_words=3)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, "one two three")
        self.assertEqual(chunks[1].text, "four five")
        self.assertTrue(chunks[1].continues_paragraph)
        self.assertTrue(chunks[1].ends_paragraph)


if __name__ == "__main__":
    unittest.main()

