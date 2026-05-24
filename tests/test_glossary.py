import unittest

from translator.translation.glossary import (
    extract_glossary_entries,
    glossary_for_chunk,
    merge_glossary_entries,
    term_appears_in_text,
)


class GlossaryTests(unittest.TestCase):
    def test_merges_duplicate_source_terms_and_target_variants(self) -> None:
        glossary = merge_glossary_entries(
            glossary=[],
            new_entries=[
                {
                    "source_terms": ["attic"],
                    "target_terms": ["desván"],
                    "preferred_target": "desván",
                    "category": "setting",
                    "priority": "medium",
                    "reason": "Ambiguous setting term.",
                }
            ],
            chunk_id="chunk_0001",
        )

        glossary = merge_glossary_entries(
            glossary=glossary,
            new_entries=[
                {
                    "source_terms": ["Attic", "attic door"],
                    "target_terms": ["desván", "puerta del desván"],
                    "preferred_target": "puerta del desván",
                    "category": "object",
                    "priority": "high",
                    "reason": "Important recurring object.",
                }
            ],
            chunk_id="chunk_0002",
        )

        self.assertEqual(len(glossary), 1)
        self.assertEqual(glossary[0]["entry_id"], "g0001")
        self.assertEqual(glossary[0]["source_terms"], ["attic", "attic door"])
        self.assertEqual(
            glossary[0]["target_terms"],
            ["desván", "puerta del desván"],
        )
        self.assertEqual(glossary[0]["preferred_target"], "desván")
        self.assertEqual(glossary[0]["priority"], "high")
        self.assertEqual(glossary[0]["usage_count"], 2)
        self.assertEqual(glossary[0]["last_seen_chunk_id"], "chunk_0002")

    def test_filters_relevant_glossary_for_chunk(self) -> None:
        glossary = [
            {
                "entry_id": "g0001",
                "source_terms": ["Mira"],
                "target_terms": ["Mira"],
                "preferred_target": "Mira",
                "category": "character",
                "priority": "high",
                "reason": "Character name.",
            },
            {
                "entry_id": "g0002",
                "source_terms": ["brass key"],
                "target_terms": ["llave de latón"],
                "preferred_target": "llave de latón",
                "category": "object",
                "priority": "medium",
                "reason": "Recurring object.",
            },
            {
                "entry_id": "g0003",
                "source_terms": ["map"],
                "target_terms": ["mapa"],
                "preferred_target": "mapa",
                "category": "object",
                "priority": "medium",
                "reason": "Important object.",
            },
        ]

        relevant = glossary_for_chunk(glossary, "She held the old brass key.")

        self.assertEqual(len(relevant), 2)
        self.assertEqual(relevant[0]["source_terms"], ["Mira"])
        self.assertEqual(relevant[1]["source_terms"], ["brass key"])

    def test_extracts_glossary_from_fenced_json(self) -> None:
        output = """```json
        {
          "glossary": [
            {
              "source_terms": ["The Map in the Attic"],
              "target_terms": ["El mapa en el desván"],
              "preferred_target": "El mapa en el desván",
              "category": "title",
              "priority": "high",
              "reason": "Chapter title."
            }
          ]
        }
        ```"""

        entries = extract_glossary_entries(output, chunk_id="chunk_0001")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_terms"], ["The Map in the Attic"])
        self.assertEqual(entries[0]["target_terms"], ["El mapa en el desván"])
        self.assertEqual(entries[0]["preferred_target"], "El mapa en el desván")

    def test_extracts_glossary_from_json_wrapped_in_prose(self) -> None:
        output = """Here is the glossary update:
        {
          "glossary": [
            {
              "source_terms": ["Moon Gate"],
              "target_terms": ["चंद्र द्वार"],
              "preferred_target": "चंद्र द्वार",
              "category": "place",
              "priority": "high",
              "reason": "Named location."
            }
          ]
        }
        Done."""

        entries = extract_glossary_entries(output, chunk_id="chunk_0002")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_terms"], ["Moon Gate"])
        self.assertEqual(entries[0]["preferred_target"], "चंद्र द्वार")

    def test_term_matching_tolerates_layout_whitespace_artifacts(self) -> None:
        self.assertTrue(term_appears_in_text("Moon Gate", "The Moon\nGate opened."))
        self.assertTrue(term_appears_in_text("Moon Gate", "The Moon\u00a0Gate opened."))
        self.assertTrue(term_appears_in_text("microservice", "A micro\u00adservice failed."))


if __name__ == "__main__":
    unittest.main()
