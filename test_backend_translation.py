import json
import tempfile
import unittest
from pathlib import Path

from backend_state import LocalDocumentStore
from backend_translation import (
    TranslateNextSettings,
    retranslate_last_section,
    translate_next_section,
    translate_rest_of_document,
)


class FakeTranslationClient:
    def __init__(self) -> None:
        self.translation_prompt = ""
        self.glossary_prompt = ""

    async def translate(self, prompt: str) -> str:
        self.translation_prompt = prompt
        chunk = json_section(prompt, "Current chunk metadata:", "Current document blocks:")
        blocks = json_section(prompt, "Current document blocks:", "Current chunk text:")
        block_translations = []
        translated_parts = []
        for block in blocks:
            source_text = str(block.get("text") or block.get("source_text") or "").strip()
            translated_text = "Hola mundo." if source_text == "Hello world." else f"ES: {source_text}"
            translated_parts.append(translated_text)
            block_translations.append(
                {
                    "block_id": block["block_id"],
                    "paragraph_id": block["paragraph_id"],
                    "translated_text": translated_text,
                }
            )
        return json.dumps(
            {
                "chunk_id": chunk["chunk_id"],
                "paragraph_ids": chunk["paragraph_ids"],
                "translated_text": "\n\n".join(translated_parts),
                "block_translations": block_translations,
                "notes": [],
            }
        )

    async def curate_glossary(self, prompt: str) -> str:
        self.glossary_prompt = prompt
        return json.dumps(
            {
                "glossary": [
                    {
                        "source_terms": ["Hello world"],
                        "target_terms": ["Hola mundo"],
                        "preferred_target": "Hola mundo",
                        "category": "phrase",
                        "priority": "medium",
                        "reason": "Opening phrase.",
                    }
                ],
                "rejected_terms": [],
            }
        )


def json_section(prompt: str, marker: str, next_marker: str) -> object:
    start = prompt.index(marker) + len(marker)
    end = prompt.index(next_marker, start)
    return json.loads(prompt[start:end].strip())


class BackendTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_translate_next_section_saves_translation_and_glossary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text("Hello world.", encoding="utf-8")
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=100,
            )
            document_id = str(summary["document_id"])
            fake_client = FakeTranslationClient()

            result = await translate_next_section(
                store=store,
                document_id=document_id,
                settings=TranslateNextSettings(target_language="Spanish"),
                translator=fake_client,
            )

            preview = store.preview_response(document_id)
            sections = store.sections_response(document_id)
            glossary = store.load_glossary(document_id)

        self.assertEqual(result["section_id"], "sec_0001")
        self.assertEqual(result["next_section_id"], None)
        self.assertEqual(result["usage"]["mode"], "translate_next")
        self.assertEqual(result["usage"]["word_count"], 2)
        self.assertGreater(result["usage"]["estimated_total_tokens"], 0)
        self.assertIn("Source language: English", fake_client.translation_prompt)
        self.assertIn("from English into Spanish", fake_client.translation_prompt)
        self.assertIn("Hello world.", fake_client.translation_prompt)
        self.assertIn("Hola mundo.", fake_client.glossary_prompt)
        self.assertEqual(preview["blocks"][0]["display_text"], "Hola mundo.")
        self.assertEqual(sections["translation_cursor"], 1)
        self.assertEqual(glossary[0]["source_terms"], ["Hello world"])

    async def test_retranslate_last_section_updates_last_translated_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text("Hello world.", encoding="utf-8")
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=100,
            )
            document_id = str(summary["document_id"])
            fake_client = FakeTranslationClient()
            await translate_next_section(
                store=store,
                document_id=document_id,
                settings=TranslateNextSettings(target_language="Spanish"),
                translator=fake_client,
            )

            result = await retranslate_last_section(
                store=store,
                document_id=document_id,
                settings=TranslateNextSettings(target_language="Spanish"),
                translator=FakeTranslationClient(),
            )

        self.assertEqual(result["mode"], "retranslate_last")
        self.assertEqual(result["section_id"], "sec_0001")
        self.assertEqual(result["translation_cursor"], 1)

    async def test_translate_rest_of_document_uses_bulk_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text(
                "Hello world.\n\nSecond block for the same document.",
                encoding="utf-8",
            )
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=3,
            )
            document_id = str(summary["document_id"])
            fake_client = FakeTranslationClient()

            result = await translate_rest_of_document(
                store=store,
                document_id=document_id,
                settings=TranslateNextSettings(target_language="Spanish"),
                translator=fake_client,
            )

            sections = store.sections_response(document_id)
            preview = store.preview_response(document_id)

        self.assertEqual(result["mode"], "translate_rest")
        self.assertEqual(result["translated_chunk_count"], 1)
        self.assertEqual(result["usage"]["mode"], "translate_rest")
        self.assertEqual(result["usage"]["chunk_size_words"], 1500)
        self.assertEqual(result["usage"]["translated_block_count"], 2)
        self.assertEqual(sections["next_section_id"], None)
        self.assertEqual(sections["remaining_estimate"]["remaining_block_count"], 0)
        self.assertEqual(preview["blocks"][0]["display_text"], "Hola mundo.")
        self.assertEqual(
            preview["blocks"][1]["display_text"],
            "ES: Second block for the same document.",
        )


if __name__ == "__main__":
    unittest.main()
