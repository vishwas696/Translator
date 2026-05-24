from datetime import UTC, datetime
import tempfile
import unittest
from pathlib import Path

from translator.storage.local import (
    InsufficientCreditsError,
    LocalDocumentStore,
    build_document_sections,
    section_response,
    translation_cursor,
)
from translator.documents.model import parsed_document_from_text


class BackendStateTests(unittest.TestCase):
    def make_store_with_document(self, temp_dir: str) -> tuple[LocalDocumentStore, str]:
        source_path = Path(temp_dir) / "sample.txt"
        source_path.write_text("Hello world.", encoding="utf-8")
        store = LocalDocumentStore(Path(temp_dir) / "backend")
        summary = store.create_document(
            source_path=source_path,
            original_filename="sample.txt",
            owner_user_id="wallet-user",
            owner_email="wallet@example.test",
        )
        return store, str(summary["document_id"])

    def test_builds_ordered_page_sized_sections(self) -> None:
        first = " ".join(f"a{i}" for i in range(40))
        second = " ".join(f"b{i}" for i in range(50))
        third = " ".join(f"c{i}" for i in range(30))
        parsed_document = parsed_document_from_text(
            f"{first}\n\n{second}\n\n{third}",
            source_format="txt",
        )

        sections = build_document_sections(
            parsed_document,
            target_words_per_section=100,
        )

        self.assertEqual([section.section_id for section in sections], ["sec_0001", "sec_0002"])
        self.assertEqual(sections[0].block_ids, ["b0001", "b0002"])
        self.assertEqual(sections[1].block_ids, ["b0003"])
        self.assertEqual(sections[0].word_count, 90)
        self.assertEqual(sections[1].word_count, 30)

    def test_section_response_locks_future_sections(self) -> None:
        sections = [
            {"section_id": "sec_0001", "index": 1, "block_ids": ["b0001"], "word_count": 5},
            {"section_id": "sec_0002", "index": 2, "block_ids": ["b0002"], "word_count": 5},
            {"section_id": "sec_0003", "index": 3, "block_ids": ["b0003"], "word_count": 5},
        ]
        translations = {"b0001": "translated"}
        cursor = translation_cursor(sections, translations)

        self.assertEqual(cursor, 1)
        previous = section_response(sections[0], translations, cursor)
        next_section = section_response(sections[1], translations, cursor)
        future = section_response(sections[2], translations, cursor)

        self.assertTrue(previous["can_translate"])
        self.assertEqual(previous["action"], "retranslate_last")
        self.assertTrue(next_section["can_translate"])
        self.assertEqual(next_section["action"], "translate_next")
        self.assertFalse(future["can_translate"])
        self.assertEqual(future["status"], "locked")
        self.assertIn("estimated_total_tokens", next_section)
        self.assertIn("estimated_cost_usd", next_section)

    def test_only_last_translated_section_can_be_retranslated(self) -> None:
        sections = [
            {"section_id": "sec_0001", "index": 1, "block_ids": ["b0001"], "word_count": 5},
            {"section_id": "sec_0002", "index": 2, "block_ids": ["b0002"], "word_count": 5},
            {"section_id": "sec_0003", "index": 3, "block_ids": ["b0003"], "word_count": 5},
        ]
        translations = {"b0001": "translated", "b0002": "translated"}
        cursor = translation_cursor(sections, translations)

        first = section_response(sections[0], translations, cursor)
        second = section_response(sections[1], translations, cursor)
        third = section_response(sections[2], translations, cursor)

        self.assertFalse(first["can_translate"])
        self.assertIsNone(first["action"])
        self.assertEqual(first["status"], "translated")
        self.assertTrue(second["can_translate"])
        self.assertEqual(second["action"], "retranslate_last")
        self.assertTrue(third["can_translate"])
        self.assertEqual(third["action"], "translate_next")

    def test_credit_reservation_cannot_overspend_wallet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, document_id = self.make_store_with_document(temp_dir)
            store.ensure_signup_credit_grant(
                owner_user_id="wallet-user",
                owner_email="wallet@example.test",
                owner_auth_provider="dev",
                credits=5,
            )
            first_job = store.create_job(
                document_id=document_id,
                job_type="translate_next",
                owner_user_id="wallet-user",
                owner_email="wallet@example.test",
                owner_auth_provider="dev",
            )
            second_job = store.create_job(
                document_id=document_id,
                job_type="translate_rest",
                owner_user_id="wallet-user",
                owner_email="wallet@example.test",
                owner_auth_provider="dev",
            )

            store.reserve_credits_for_job(first_job, credits=4, model_tier="balanced")

            with self.assertRaises(InsufficientCreditsError):
                store.reserve_credits_for_job(second_job, credits=4, model_tier="balanced")

            self.assertEqual(store.credit_balance("wallet-user"), 1)

    def test_credit_capture_and_release_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, document_id = self.make_store_with_document(temp_dir)
            store.ensure_signup_credit_grant(
                owner_user_id="wallet-user",
                owner_email="wallet@example.test",
                owner_auth_provider="dev",
                credits=10,
            )
            job = store.create_job(
                document_id=document_id,
                job_type="translate_next",
                owner_user_id="wallet-user",
                owner_email="wallet@example.test",
                owner_auth_provider="dev",
            )
            store.reserve_credits_for_job(job, credits=6, model_tier="balanced")

            store.capture_credit_reservation(job, actual_credits=4)
            store.capture_credit_reservation(job, actual_credits=4)
            store.release_credit_reservation(job, reason="late_failure")

            entries = store.credit_ledger_for_owner("wallet-user")
            entry_types = [entry["entry_type"] for entry in entries]
            self.assertEqual(store.credit_balance("wallet-user"), 6)
            self.assertEqual(entry_types.count("charge"), 1)
            self.assertEqual(entry_types.count("refund"), 1)

    def test_preview_uses_saved_block_translation_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text("Hello world.\n\nSecond block.", encoding="utf-8")
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=10,
            )
            document_id = str(summary["document_id"])
            store.translations_path(document_id).write_text(
                '{"b0001": "Hola mundo."}',
                encoding="utf-8",
            )

            preview = store.preview_response(document_id)

        self.assertEqual(preview["blocks"][0]["display_text"], "Hola mundo.")
        self.assertEqual(preview["blocks"][0]["status"], "translated")
        self.assertEqual(preview["blocks"][1]["display_text"], "Second block.")
        self.assertEqual(preview["blocks"][1]["status"], "source")

    def test_tracks_active_jobs_for_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text("Hello world.", encoding="utf-8")
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=10,
            )
            document_id = str(summary["document_id"])

            job = store.create_job(
                document_id=document_id,
                job_type="translate_next",
                payload={"target_language": "Spanish"},
            )
            active_job = store.active_job_for_document(document_id, "translate_next")
            store.update_job(str(job["job_id"]), status="succeeded", progress=100)

            completed_active_job = store.active_job_for_document(
                document_id,
                "translate_next",
            )

        self.assertEqual(active_job["job_id"], job["job_id"])
        self.assertIsNone(completed_active_job)

    def test_finds_job_by_idempotency_key_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text("Hello world.", encoding="utf-8")
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=10,
            )
            document_id = str(summary["document_id"])

            job = store.create_job(
                document_id=document_id,
                job_type="translate_next",
                payload={
                    "target_language": "Spanish",
                    "idempotency_key": "click-1",
                },
            )
            store.update_job(str(job["job_id"]), status="succeeded", progress=100)
            matched_job = store.job_for_idempotency_key(
                document_id=document_id,
                job_type="translate_next",
                idempotency_key="click-1",
            )

        self.assertEqual(matched_job["job_id"], job["job_id"])

    def test_records_usage_once_per_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text("Hello world.", encoding="utf-8")
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=10,
                owner_user_id="user-a",
                owner_email="a@example.test",
            )
            document_id = str(summary["document_id"])
            job = store.create_job(
                document_id=document_id,
                job_type="translate_next",
                payload={"target_language": "Spanish"},
            )
            usage = {
                "mode": "translate_next",
                "section_id": "sec_0001",
                "word_count": 2,
                "chunk_count": 1,
                "chunk_size_words": 10,
                "translated_block_count": 1,
                "estimated_input_tokens": 353,
                "estimated_output_tokens": 3,
                "estimated_prompt_overhead_tokens": 350,
                "estimated_total_tokens": 356,
                "estimated_cost_usd": 0.000356,
            }

            first_record = store.record_usage(job, usage)
            second_record = store.record_usage(job, usage)
            summary = store.usage_summary(owner_user_id="user-a", document_id=document_id)

        self.assertEqual(first_record["usage_id"], second_record["usage_id"])
        self.assertEqual(summary["record_count"], 1)
        self.assertEqual(summary["total_word_count"], 2)
        self.assertEqual(summary["estimated_total_tokens"], 356)
        self.assertEqual(summary["estimated_cost_usd"], 0.000356)

    def test_counts_owner_uploads_and_active_translation_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            document_ids = []
            for index in range(3):
                source_path = Path(temp_dir) / f"sample_{index}.txt"
                source_path.write_text(f"Hello world {index}.", encoding="utf-8")
                summary = store.create_document(
                    source_path=source_path,
                    original_filename=source_path.name,
                    target_words_per_section=10,
                    owner_user_id="user-a",
                    owner_email="a@example.test",
                )
                document_ids.append(str(summary["document_id"]))

            first_job = store.create_job(document_ids[0], "translate_next")
            second_job = store.create_job(document_ids[1], "translate_rest")
            store.create_job(document_ids[2], "export")
            store.update_job(str(second_job["job_id"]), status="succeeded", progress=100)

            upload_count = store.upload_count_since(
                "user-a",
                since=datetime.min.replace(tzinfo=UTC),
            )
            active_jobs = store.active_translation_jobs_for_user("user-a")

        self.assertEqual(upload_count, 3)
        self.assertEqual([job["job_id"] for job in active_jobs], [first_job["job_id"]])

    def test_sections_response_includes_next_and_rest_cost_estimates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = " ".join(f"a{i}" for i in range(80))
            second = " ".join(f"b{i}" for i in range(80))
            third = " ".join(f"c{i}" for i in range(80))
            source_path = Path(temp_dir) / "sample.txt"
            source_path.write_text(
                f"{first}\n\n{second}\n\n{third}",
                encoding="utf-8",
            )
            store = LocalDocumentStore(Path(temp_dir) / "backend")
            summary = store.create_document(
                source_path=source_path,
                original_filename="sample.txt",
                target_words_per_section=100,
            )
            document_id = str(summary["document_id"])

            response = store.sections_response(document_id)

        next_estimate = response["next_section_estimate"]
        remaining_estimate = response["remaining_estimate"]
        self.assertEqual(next_estimate["section_id"], "sec_0001")
        self.assertEqual(next_estimate["chunk_count"], 1)
        self.assertEqual(remaining_estimate["chunk_size_words"], 1500)
        self.assertEqual(remaining_estimate["chunk_count"], 1)
        self.assertEqual(remaining_estimate["remaining_section_count"], 3)
        self.assertLess(
            remaining_estimate["estimated_cost_per_word_usd"],
            next_estimate["estimated_cost_per_word_usd"],
        )


if __name__ == "__main__":
    unittest.main()
