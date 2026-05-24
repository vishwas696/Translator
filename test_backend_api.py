import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_STORE", "json")

import backend_api
from backend_razorpay import RazorpayOrderResult, RazorpayWebhookSignatureError
from backend_state import LocalDocumentStore
from test_backend_translation import FakeTranslationClient


class FailingTranslationClient:
    async def translate(self, prompt: str) -> str:
        raise RuntimeError("provider failure from test")

    async def curate_glossary(self, prompt: str) -> str:
        return "{}"


class BackendApiTests(unittest.TestCase):
    def test_backend_store_defaults_to_mysql(self) -> None:
        previous_store = os.environ.pop("BACKEND_STORE", None)

        class FakeMySqlDocumentStore:
            def __init__(self, root: Path) -> None:
                self.root = root

        try:
            with patch("backend_mysql_store.MySqlDocumentStore", FakeMySqlDocumentStore):
                selected_store = backend_api.backend_store()
        finally:
            restore_env_var("BACKEND_STORE", previous_store)

        self.assertIsInstance(selected_store, FakeMySqlDocumentStore)

    def test_upload_sections_preview_and_export_txt_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            upload_response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100", "source_language": "English"},
                files={
                    "file": (
                        "sample.txt",
                        b"Chapter 1\n\nHello world.\n\nSecond block.",
                        "text/plain",
                    )
                },
            )

            self.assertEqual(upload_response.status_code, 200)
            document_id = upload_response.json()["document_id"]
            self.assertEqual(upload_response.json()["source_language"], "English")

            sections_response = client.get(f"/documents/{document_id}/sections")
            self.assertEqual(sections_response.status_code, 200)
            self.assertEqual(sections_response.json()["next_section_id"], "sec_0001")
            self.assertEqual(
                sections_response.json()["sections"][0]["action"],
                "translate_next",
            )
            self.assertIn("next_section_estimate", sections_response.json())
            self.assertIn("remaining_estimate", sections_response.json())

            preview_response = client.get(f"/documents/{document_id}/preview")
            self.assertEqual(preview_response.status_code, 200)
            self.assertEqual(preview_response.json()["blocks"][0]["source_text"], "Chapter 1")

            export_response = client.post(f"/documents/{document_id}/export")
            self.assertEqual(export_response.status_code, 200)
            self.assertIn("download_path", export_response.json())
            self.assertIn("estimated_cost_usd", sections_response.json()["sections"][0])

    def test_frontend_index_is_served(self) -> None:
        client = TestClient(backend_api.app)

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("LexiFlow AI", response.text)

    def test_documents_endpoint_lists_only_current_user_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            owner_upload = client.post(
                "/documents/upload",
                headers={"X-Dev-User-Id": "user-a"},
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "owner.txt",
                        b"Hello owner.",
                        "text/plain",
                    )
                },
            )
            client.post(
                "/documents/upload",
                headers={"X-Dev-User-Id": "user-b"},
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "other.txt",
                        b"Hello other.",
                        "text/plain",
                    )
                },
            )

            response = client.get(
                "/documents",
                headers={"X-Dev-User-Id": "user-a"},
            )

        self.assertEqual(owner_upload.status_code, 200)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["document_count"], 1)
        self.assertEqual(
            response.json()["documents"][0]["document_id"],
            owner_upload.json()["document_id"],
        )

    def test_dev_user_ownership_blocks_other_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                headers={
                    "X-Dev-User-Id": "user-a",
                    "X-Dev-User-Email": "a@example.test",
                },
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]

            owner_response = client.get(
                f"/documents/{document_id}",
                headers={"X-Dev-User-Id": "user-a"},
            )
            other_user_response = client.get(
                f"/documents/{document_id}",
                headers={"X-Dev-User-Id": "user-b"},
            )

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(other_user_response.status_code, 404)

    def test_job_endpoint_blocks_other_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                headers={"X-Dev-User-Id": "user-a"},
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]
            job = backend_api.store.create_job(
                document_id=document_id,
                job_type="translate_next",
                payload={"target_language": "Spanish"},
            )

            owner_response = client.get(
                f"/jobs/{job['job_id']}",
                headers={"X-Dev-User-Id": "user-a"},
            )
            other_user_response = client.get(
                f"/jobs/{job['job_id']}",
                headers={"X-Dev-User-Id": "user-b"},
            )

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(other_user_response.status_code, 404)

    def test_document_usage_blocks_other_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                upload_response = client.post(
                    "/documents/upload",
                    headers={"X-Dev-User-Id": "user-a"},
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]
                client.post(
                    f"/documents/{document_id}/translate-next",
                    headers={"X-Dev-User-Id": "user-a"},
                    json={"source_language": "English", "target_language": "Spanish"},
                )

                owner_usage = client.get(
                    f"/documents/{document_id}/usage",
                    headers={"X-Dev-User-Id": "user-a"},
                )
                other_user_usage = client.get(
                    f"/documents/{document_id}/usage",
                    headers={"X-Dev-User-Id": "user-b"},
                )
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(owner_usage.status_code, 200)
        self.assertEqual(owner_usage.json()["record_count"], 1)
        self.assertEqual(owner_usage.json()["records"][0]["source_language"], "English")
        self.assertEqual(other_user_usage.status_code, 404)

    def test_daily_upload_quota_limits_user_uploads(self) -> None:
        previous_limit = os.environ.get("TRANSLATOR_DAILY_UPLOAD_LIMIT_PER_USER")
        os.environ["TRANSLATOR_DAILY_UPLOAD_LIMIT_PER_USER"] = "2"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                responses = [
                    client.post(
                        "/documents/upload",
                        headers={"X-Dev-User-Id": "user-a"},
                        data={"target_words_per_section": "100"},
                        files={
                            "file": (
                                f"sample_{index}.txt",
                                b"Hello world.",
                                "text/plain",
                            )
                        },
                    )
                    for index in range(3)
                ]
        finally:
            restore_env_var("TRANSLATOR_DAILY_UPLOAD_LIMIT_PER_USER", previous_limit)

        self.assertEqual([response.status_code for response in responses[:2]], [200, 200])
        self.assertEqual(responses[2].status_code, 429)
        self.assertEqual(responses[2].json()["detail"]["quota"]["type"], "daily_uploads")

    def test_active_translation_job_quota_limits_user_jobs(self) -> None:
        previous_limit = os.environ.get("TRANSLATOR_MAX_ACTIVE_JOBS_PER_USER")
        os.environ["TRANSLATOR_MAX_ACTIVE_JOBS_PER_USER"] = "2"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                document_ids = []
                for index in range(3):
                    upload_response = client.post(
                        "/documents/upload",
                        headers={"X-Dev-User-Id": "user-a"},
                        data={"target_words_per_section": "100"},
                        files={
                            "file": (
                                f"sample_{index}.txt",
                                b"Hello world.",
                                "text/plain",
                            )
                        },
                    )
                    document_ids.append(upload_response.json()["document_id"])
                backend_api.store.create_job(
                    document_id=document_ids[0],
                    job_type="translate_next",
                    payload={"target_language": "Spanish"},
                )
                backend_api.store.create_job(
                    document_id=document_ids[1],
                    job_type="translate_rest",
                    payload={"target_language": "Spanish"},
                )

                response = client.post(
                    f"/documents/{document_ids[2]}/translate-next",
                    headers={"X-Dev-User-Id": "user-a"},
                    json={"target_language": "Spanish"},
                )
        finally:
            restore_env_var("TRANSLATOR_MAX_ACTIVE_JOBS_PER_USER", previous_limit)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["detail"]["quota"]["type"],
            "active_translation_jobs",
        )

    def test_lifetime_free_word_quota_blocks_translation(self) -> None:
        previous_limit = os.environ.get("TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER")
        os.environ["TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER"] = "5"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                upload_response = client.post(
                    "/documents/upload",
                    headers={"X-Dev-User-Id": "user-a"},
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]
                job = backend_api.store.create_job(
                    document_id=document_id,
                    job_type="translate_next",
                    payload={"target_language": "Spanish"},
                )
                backend_api.store.update_job(str(job["job_id"]), status="succeeded")
                backend_api.store.record_usage(
                    job,
                    {
                        "mode": "translate_next",
                        "section_id": "sec_0000",
                        "word_count": 4,
                        "chunk_count": 1,
                        "chunk_size_words": 100,
                        "translated_block_count": 1,
                        "estimated_input_tokens": 355,
                        "estimated_output_tokens": 5,
                        "estimated_prompt_overhead_tokens": 350,
                        "estimated_total_tokens": 360,
                        "estimated_cost_usd": 0.00036,
                    },
                )

                response = client.post(
                    f"/documents/{document_id}/translate-next",
                    headers={"X-Dev-User-Id": "user-a"},
                    json={"target_language": "Spanish"},
                )
                usage_response = client.get(
                    "/usage/me",
                    headers={"X-Dev-User-Id": "user-a"},
                )
        finally:
            restore_env_var("TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER", previous_limit)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(
            response.json()["detail"]["quota"]["type"],
            "lifetime_free_translation_words",
        )
        self.assertEqual(response.json()["detail"]["quota"]["remaining_words"], 1)
        self.assertEqual(usage_response.json()["quota"]["limit_words"], 5)

    def test_wallet_signup_checkout_and_mock_payment_are_server_controlled(self) -> None:
        previous_mock_enabled = os.environ.get("TRANSLATOR_ENABLE_MOCK_PAYMENTS")
        previous_mock_secret = os.environ.get("TRANSLATOR_MOCK_PAYMENT_SECRET")
        previous_signup_credits = os.environ.get("TRANSLATOR_SIGNUP_CREDITS")
        os.environ["TRANSLATOR_ENABLE_MOCK_PAYMENTS"] = "1"
        os.environ["TRANSLATOR_MOCK_PAYMENT_SECRET"] = "secret-for-tests"
        os.environ["TRANSLATOR_SIGNUP_CREDITS"] = "200"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)

                wallet_before = client.get(
                    "/billing/wallet",
                    headers={"X-Dev-User-Id": "billing-user"},
                )
                checkout = client.post(
                    "/billing/checkout-session",
                    headers={"X-Dev-User-Id": "billing-user"},
                    json={
                        "package_id": "starter_500",
                        "provider": "mock",
                        "credits": 999999,
                        "amount_cents": 1,
                    },
                )
                wallet_after_checkout = client.get(
                    "/billing/wallet",
                    headers={"X-Dev-User-Id": "billing-user"},
                )
                unauthorized_complete = client.post(
                    f"/billing/mock-payments/{checkout.json()['order_id']}/complete",
                    headers={"X-Dev-User-Id": "billing-user"},
                    json={},
                )
                other_user_complete = client.post(
                    f"/billing/mock-payments/{checkout.json()['order_id']}/complete",
                    headers={
                        "X-Dev-User-Id": "other-billing-user",
                        "X-Mock-Payment-Secret": "secret-for-tests",
                    },
                    json={"external_payment_id": "provider_event_other"},
                )
                authorized_complete = client.post(
                    f"/billing/mock-payments/{checkout.json()['order_id']}/complete",
                    headers={
                        "X-Dev-User-Id": "billing-user",
                        "X-Mock-Payment-Secret": "secret-for-tests",
                    },
                    json={"external_payment_id": "provider_event_1"},
                )
                duplicate_complete = client.post(
                    f"/billing/mock-payments/{checkout.json()['order_id']}/complete",
                    headers={
                        "X-Dev-User-Id": "billing-user",
                        "X-Mock-Payment-Secret": "secret-for-tests",
                    },
                    json={"external_payment_id": "provider_event_1"},
                )
        finally:
            restore_env_var("TRANSLATOR_ENABLE_MOCK_PAYMENTS", previous_mock_enabled)
            restore_env_var("TRANSLATOR_MOCK_PAYMENT_SECRET", previous_mock_secret)
            restore_env_var("TRANSLATOR_SIGNUP_CREDITS", previous_signup_credits)

        self.assertEqual(wallet_before.json()["balance_credits"], 200)
        self.assertEqual(checkout.status_code, 200)
        self.assertEqual(checkout.json()["status"], "pending")
        self.assertEqual(checkout.json()["credits"], 500)
        self.assertEqual(checkout.json()["amount_cents"], 500)
        self.assertEqual(wallet_after_checkout.json()["balance_credits"], 200)
        self.assertEqual(unauthorized_complete.status_code, 403)
        self.assertEqual(other_user_complete.status_code, 404)
        self.assertEqual(authorized_complete.status_code, 200)
        self.assertEqual(authorized_complete.json()["wallet"]["balance_credits"], 700)
        self.assertEqual(duplicate_complete.json()["wallet"]["balance_credits"], 700)

    def test_mock_checkout_disabled_does_not_create_order(self) -> None:
        previous_mock_enabled = os.environ.get("TRANSLATOR_ENABLE_MOCK_PAYMENTS")
        os.environ["TRANSLATOR_ENABLE_MOCK_PAYMENTS"] = "0"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)

                response = client.post(
                    "/billing/checkout-session",
                    headers={"X-Dev-User-Id": "billing-user"},
                    json={"package_id": "starter_500", "provider": "mock"},
                )
                orders = backend_api.store.load_payment_orders()
        finally:
            restore_env_var("TRANSLATOR_ENABLE_MOCK_PAYMENTS", previous_mock_enabled)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "mock_payments_disabled")
        self.assertEqual(orders, {})

    def test_razorpay_requires_webhook_before_checkout_order_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            with patch("backend_api.razorpay_payments_configured", return_value=False), patch(
                "backend_api.razorpay_orders_configured",
                return_value=True,
            ), patch(
                "backend_api.razorpay_webhook_configured",
                return_value=False,
            ):
                response = client.post(
                    "/billing/checkout-session",
                    headers={"X-Dev-User-Id": "razorpay-user"},
                    json={"package_id": "starter_500", "provider": "razorpay"},
                )
                orders = backend_api.store.load_payment_orders()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "razorpay_not_configured")
        self.assertEqual(orders, {})

    def test_razorpay_checkout_uses_server_package_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            with patch("backend_api.razorpay_payments_configured", return_value=True), patch(
                "backend_api.razorpay_checkout_key_id",
                return_value="rzp_test_key",
            ), patch(
                "backend_api.create_razorpay_order",
                return_value=RazorpayOrderResult(razorpay_order_id="order_test_server_values"),
            ) as create_session:
                response = client.post(
                    "/billing/checkout-session",
                    headers={"X-Dev-User-Id": "razorpay-user"},
                    json={
                        "package_id": "starter_500",
                        "provider": "razorpay",
                        "credits": 999999,
                        "amount_cents": 1,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "razorpay")
        self.assertEqual(response.json()["credits"], 500)
        self.assertEqual(response.json()["amount_cents"], 500)
        self.assertEqual(response.json()["razorpay_key_id"], "rzp_test_key")
        self.assertEqual(response.json()["razorpay_order_id"], "order_test_server_values")
        self.assertEqual(response.json()["external_payment_id"], "order_test_server_values")
        order = create_session.call_args.args[0]
        self.assertEqual(order["credits"], 500)
        self.assertEqual(order["amount_cents"], 500)

    def test_razorpay_webhook_completes_payment_idempotently(self) -> None:
        previous_signup_credits = os.environ.get("TRANSLATOR_SIGNUP_CREDITS")
        os.environ["TRANSLATOR_SIGNUP_CREDITS"] = "200"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                client.get("/billing/wallet", headers={"X-Dev-User-Id": "razorpay-user"})
                with patch("backend_api.razorpay_payments_configured", return_value=True), patch(
                    "backend_api.razorpay_checkout_key_id",
                    return_value="rzp_test_key",
                ), patch(
                    "backend_api.create_razorpay_order",
                    return_value=RazorpayOrderResult(razorpay_order_id="order_test_paid"),
                ):
                    checkout = client.post(
                        "/billing/checkout-session",
                        headers={"X-Dev-User-Id": "razorpay-user"},
                        json={"package_id": "starter_500", "provider": "razorpay"},
                    )

                order_id = checkout.json()["order_id"]
                event = razorpay_payment_event(
                    order_id=order_id,
                    razorpay_order_id="order_test_paid",
                    payment_id="pay_test_paid",
                    owner_user_id="razorpay-user",
                    package_id="starter_500",
                    amount=500,
                )
                with patch("backend_api.construct_razorpay_webhook_event", return_value=event):
                    first = client.post(
                        "/billing/razorpay/webhook",
                        headers={"X-Razorpay-Signature": "signed"},
                        content=b"{}",
                    )
                    second = client.post(
                        "/billing/razorpay/webhook",
                        headers={"X-Razorpay-Signature": "signed"},
                        content=b"{}",
                    )
                wallet = client.get("/billing/wallet", headers={"X-Dev-User-Id": "razorpay-user"})
        finally:
            restore_env_var("TRANSLATOR_SIGNUP_CREDITS", previous_signup_credits)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["status"], "paid")
        self.assertEqual(wallet.json()["balance_credits"], 700)
        purchase_entries = [
            entry
            for entry in wallet.json()["recent_ledger"]
            if entry["entry_type"] == "purchase"
        ]
        self.assertEqual(len(purchase_entries), 1)

    def test_razorpay_webhook_rejects_signature_and_amount_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            with patch("backend_api.construct_razorpay_webhook_event", side_effect=RazorpayWebhookSignatureError()):
                invalid_signature = client.post(
                    "/billing/razorpay/webhook",
                    headers={"X-Razorpay-Signature": "bad"},
                    content=b"{}",
                )

            order = backend_api.store.create_payment_order(
                owner_user_id="razorpay-user",
                owner_email="razorpay@example.test",
                owner_auth_provider="dev",
                package={"package_id": "starter_500", "name": "Starter", "credits": 500, "amount_cents": 500, "currency": "USD"},
                provider="razorpay",
            )
            backend_api.store.update_payment_order_checkout(
                order_id=str(order["order_id"]),
                checkout_url="",
                external_payment_id="order_test_mismatch",
                metadata={"razorpay_order_id": "order_test_mismatch"},
            )
            event = razorpay_payment_event(
                order_id=str(order["order_id"]),
                razorpay_order_id="order_test_mismatch",
                payment_id="pay_test_mismatch",
                owner_user_id="razorpay-user",
                package_id="starter_500",
                amount=1,
            )
            with patch("backend_api.construct_razorpay_webhook_event", return_value=event):
                amount_mismatch = client.post(
                    "/billing/razorpay/webhook",
                    headers={"X-Razorpay-Signature": "signed"},
                    content=b"{}",
                )

        self.assertEqual(invalid_signature.status_code, 400)
        self.assertEqual(
            invalid_signature.json()["detail"]["code"],
            "razorpay_webhook_signature_invalid",
        )
        self.assertEqual(amount_mismatch.status_code, 400)
        self.assertEqual(amount_mismatch.json()["detail"]["code"], "razorpay_amount_mismatch")
        self.assertEqual(backend_api.store.credit_balance("razorpay-user"), 0)

    def test_razorpay_webhook_rejects_untrusted_payment_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            order = backend_api.store.create_payment_order(
                owner_user_id="razorpay-user",
                owner_email="razorpay@example.test",
                owner_auth_provider="dev",
                package={"package_id": "starter_500", "name": "Starter", "credits": 500, "amount_cents": 500, "currency": "USD"},
                provider="razorpay",
            )
            backend_api.store.update_payment_order_checkout(
                order_id=str(order["order_id"]),
                checkout_url="",
                external_payment_id="order_test_metadata",
                metadata={"razorpay_order_id": "order_test_metadata"},
            )

            wrong_owner_event = razorpay_payment_event(
                order_id=str(order["order_id"]),
                razorpay_order_id="order_test_metadata",
                payment_id="pay_test_wrong_owner",
                owner_user_id="attacker-user",
                package_id="starter_500",
                amount=500,
            )
            wrong_package_event = razorpay_payment_event(
                order_id=str(order["order_id"]),
                razorpay_order_id="order_test_metadata",
                payment_id="pay_test_wrong_package",
                owner_user_id="razorpay-user",
                package_id="studio_3000",
                amount=500,
            )
            not_captured_event = razorpay_payment_event(
                order_id=str(order["order_id"]),
                razorpay_order_id="order_test_metadata",
                payment_id="pay_test_authorized",
                owner_user_id="razorpay-user",
                package_id="starter_500",
                amount=500,
                status="authorized",
            )
            with patch("backend_api.construct_razorpay_webhook_event", return_value=wrong_owner_event):
                wrong_owner = client.post(
                    "/billing/razorpay/webhook",
                    headers={"X-Razorpay-Signature": "signed"},
                    content=b"{}",
                )
            with patch("backend_api.construct_razorpay_webhook_event", return_value=wrong_package_event):
                wrong_package = client.post(
                    "/billing/razorpay/webhook",
                    headers={"X-Razorpay-Signature": "signed"},
                    content=b"{}",
                )
            with patch("backend_api.construct_razorpay_webhook_event", return_value=not_captured_event):
                not_captured = client.post(
                    "/billing/razorpay/webhook",
                    headers={"X-Razorpay-Signature": "signed"},
                    content=b"{}",
                )

        self.assertEqual(wrong_owner.status_code, 400)
        self.assertEqual(wrong_owner.json()["detail"]["code"], "razorpay_owner_mismatch")
        self.assertEqual(wrong_package.status_code, 400)
        self.assertEqual(wrong_package.json()["detail"]["code"], "razorpay_package_mismatch")
        self.assertEqual(not_captured.status_code, 409)
        self.assertEqual(not_captured.json()["detail"]["code"], "razorpay_payment_not_captured")
        self.assertEqual(backend_api.store.credit_balance("razorpay-user"), 0)

    def test_repeated_wallet_reads_do_not_duplicate_signup_credits(self) -> None:
        previous_signup_credits = os.environ.get("TRANSLATOR_SIGNUP_CREDITS")
        os.environ["TRANSLATOR_SIGNUP_CREDITS"] = "200"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)

                first = client.get("/billing/wallet", headers={"X-Dev-User-Id": "wallet-user"})
                second = client.get("/billing/wallet", headers={"X-Dev-User-Id": "wallet-user"})
        finally:
            restore_env_var("TRANSLATOR_SIGNUP_CREDITS", previous_signup_credits)

        self.assertEqual(first.json()["balance_credits"], 200)
        self.assertEqual(second.json()["balance_credits"], 200)
        signup_entries = [
            entry
            for entry in second.json()["recent_ledger"]
            if entry["entry_type"] == "signup_grant"
        ]
        self.assertEqual(len(signup_entries), 1)

    def test_model_tier_quote_uses_credits_not_frontend_costs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]

            quick = client.get(f"/documents/{document_id}/quote?model_tier=quick_draft")
            balanced = client.get(f"/documents/{document_id}/quote?model_tier=balanced")
            precision = client.get(f"/documents/{document_id}/quote?model_tier=precision")

        self.assertEqual(quick.status_code, 200)
        self.assertEqual(balanced.status_code, 200)
        self.assertEqual(precision.status_code, 200)
        self.assertLessEqual(
            quick.json()["next_section"]["estimated_credits"],
            balanced.json()["next_section"]["estimated_credits"],
        )
        self.assertGreater(
            precision.json()["next_section"]["estimated_credits"],
            balanced.json()["next_section"]["estimated_credits"],
        )

    def test_translation_requires_wallet_credits(self) -> None:
        previous_signup_credits = os.environ.get("TRANSLATOR_SIGNUP_CREDITS")
        os.environ["TRANSLATOR_SIGNUP_CREDITS"] = "0"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]

                response = client.post(
                    f"/documents/{document_id}/translate-next",
                    json={"target_language": "Spanish", "model_tier": "balanced"},
                )
        finally:
            restore_env_var("TRANSLATOR_SIGNUP_CREDITS", previous_signup_credits)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["detail"]["code"], "insufficient_credits")

    def test_translation_charges_reserved_credits_after_success(self) -> None:
        previous_signup_credits = os.environ.get("TRANSLATOR_SIGNUP_CREDITS")
        os.environ["TRANSLATOR_SIGNUP_CREDITS"] = "100"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                original_factory = backend_api.translator_factory
                backend_api.translator_factory = FakeTranslationClient
                client = TestClient(backend_api.app)
                try:
                    upload_response = client.post(
                        "/documents/upload",
                        data={"target_words_per_section": "100"},
                        files={
                            "file": (
                                "sample.txt",
                                b"Hello world.",
                                "text/plain",
                            )
                        },
                    )
                    document_id = upload_response.json()["document_id"]
                    quote = client.get(f"/documents/{document_id}/quote?model_tier=balanced")
                    estimated_credits = quote.json()["next_section"]["estimated_credits"]

                    translate_response = client.post(
                        f"/documents/{document_id}/translate-next",
                        json={
                            "target_language": "Spanish",
                            "model_tier": "balanced",
                            "estimated_credits": 0,
                            "credits": 0,
                            "wallet": {"balance_credits": 999999},
                        },
                    )
                    wallet = client.get("/billing/wallet")
                    ledger_types = [
                        item["entry_type"]
                        for item in wallet.json()["recent_ledger"]
                    ]
                finally:
                    backend_api.translator_factory = original_factory
        finally:
            restore_env_var("TRANSLATOR_SIGNUP_CREDITS", previous_signup_credits)

        self.assertEqual(translate_response.status_code, 202)
        self.assertEqual(wallet.json()["balance_credits"], 100 - estimated_credits)
        self.assertIn("reserve", ledger_types)
        self.assertIn("charge", ledger_types)

    def test_failed_translation_refunds_reserved_credits(self) -> None:
        previous_signup_credits = os.environ.get("TRANSLATOR_SIGNUP_CREDITS")
        os.environ["TRANSLATOR_SIGNUP_CREDITS"] = "100"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                original_factory = backend_api.translator_factory
                backend_api.translator_factory = FailingTranslationClient
                client = TestClient(backend_api.app)
                try:
                    upload_response = client.post(
                        "/documents/upload",
                        data={"target_words_per_section": "100"},
                        files={
                            "file": (
                                "sample.txt",
                                b"Hello world.",
                                "text/plain",
                            )
                        },
                    )
                    document_id = upload_response.json()["document_id"]

                    translate_response = client.post(
                        f"/documents/{document_id}/translate-next",
                        json={"target_language": "Spanish", "model_tier": "balanced"},
                    )
                    job_response = client.get(translate_response.json()["poll_url"])
                    wallet = client.get("/billing/wallet")
                finally:
                    backend_api.translator_factory = original_factory
        finally:
            restore_env_var("TRANSLATOR_SIGNUP_CREDITS", previous_signup_credits)

        self.assertEqual(translate_response.status_code, 202)
        self.assertEqual(job_response.json()["status"], "failed")
        self.assertEqual(wallet.json()["balance_credits"], 100)

    def test_unknown_model_tier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]

            response = client.post(
                f"/documents/{document_id}/translate-next",
                json={"target_language": "Spanish", "model_tier": "front_end_free_model"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "model_tier_not_found")

    def test_google_auth_mode_requires_bearer_token(self) -> None:
        previous_auth_mode = os.environ.get("TRANSLATOR_AUTH_MODE")
        previous_client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        os.environ["TRANSLATOR_AUTH_MODE"] = "google"
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "client-id"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
        finally:
            restore_env_var("TRANSLATOR_AUTH_MODE", previous_auth_mode)
            restore_env_var("GOOGLE_OAUTH_CLIENT_ID", previous_client_id)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "authentication_required")
        self.assertIn("sign in", response.json()["detail"]["message"].lower())

    def test_google_auth_mode_sets_owner_from_verified_token(self) -> None:
        previous_auth_mode = os.environ.get("TRANSLATOR_AUTH_MODE")
        previous_client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        os.environ["TRANSLATOR_AUTH_MODE"] = "google"
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "client-id"

        def fake_verify(token: str, client_id: str) -> dict[str, object]:
            self.assertEqual(client_id, "client-id")
            if token == "user-a-token":
                return {
                    "sub": "google-user-a",
                    "email": "user-a@example.test",
                    "email_verified": True,
                }
            return {
                "sub": "google-user-b",
                "email": "user-b@example.test",
                "email_verified": True,
            }

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)
                with patch("backend_auth.verify_google_id_token", side_effect=fake_verify):
                    upload_response = client.post(
                        "/documents/upload",
                        headers={"Authorization": "Bearer user-a-token"},
                        data={"target_words_per_section": "100"},
                        files={
                            "file": (
                                "sample.txt",
                                b"Hello world.",
                                "text/plain",
                            )
                        },
                    )
                    document_id = upload_response.json()["document_id"]
                    owner_response = client.get(
                        f"/documents/{document_id}",
                        headers={"Authorization": "Bearer user-a-token"},
                    )
                    other_user_response = client.get(
                        f"/documents/{document_id}",
                        headers={"Authorization": "Bearer user-b-token"},
                    )
        finally:
            restore_env_var("TRANSLATOR_AUTH_MODE", previous_auth_mode)
            restore_env_var("GOOGLE_OAUTH_CLIENT_ID", previous_client_id)

        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()["owner_user_id"], "google-user-a")
        self.assertEqual(upload_response.json()["owner_email"], "user-a@example.test")
        self.assertEqual(upload_response.json()["owner_auth_provider"], "google")
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(other_user_response.status_code, 404)

    def test_translate_next_endpoint_uses_next_section_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]

                translate_response = client.post(
                    f"/documents/{document_id}/translate-next",
                    json={"target_language": "Spanish"},
                )
                job_response = client.get(translate_response.json()["poll_url"])
                glossary_response = client.get(f"/documents/{document_id}/glossary")
                sections_response = client.get(f"/documents/{document_id}/sections")
                preview_response = client.get(f"/documents/{document_id}/preview")
                usage_response = client.get("/usage/me")
                document_usage_response = client.get(f"/documents/{document_id}/usage")
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(translate_response.status_code, 202)
        self.assertIn("job_id", translate_response.json())
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.json()["status"], "succeeded")
        self.assertEqual(job_response.json()["result"]["section_id"], "sec_0001")
        self.assertEqual(glossary_response.status_code, 200)
        self.assertEqual(glossary_response.json()["entry_count"], 1)
        self.assertEqual(sections_response.json()["translation_cursor"], 1)
        self.assertEqual(
            preview_response.json()["blocks"][0]["display_text"],
            "Hola mundo.",
        )
        self.assertEqual(usage_response.status_code, 200)
        self.assertEqual(usage_response.json()["record_count"], 1)
        self.assertEqual(usage_response.json()["total_word_count"], 2)
        self.assertGreater(usage_response.json()["estimated_total_tokens"], 0)
        self.assertEqual(document_usage_response.status_code, 200)
        self.assertEqual(document_usage_response.json()["record_count"], 1)

    def test_retranslate_last_endpoint_creates_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]
                client.post(
                    f"/documents/{document_id}/translate-next",
                    json={"target_language": "Spanish"},
                )

                retranslate_response = client.post(
                    f"/documents/{document_id}/retranslate-last",
                    json={"target_language": "Spanish"},
                )
                job_response = client.get(retranslate_response.json()["poll_url"])
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(retranslate_response.status_code, 202)
        self.assertEqual(job_response.json()["status"], "succeeded")
        self.assertEqual(job_response.json()["result"]["mode"], "retranslate_last")

    def test_translate_next_idempotency_key_replays_completed_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                first = " ".join(f"a{i}" for i in range(80))
                second = " ".join(f"b{i}" for i in range(80))
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            f"{first}\n\n{second}".encode("utf-8"),
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]
                request_body = {
                    "target_language": "Spanish",
                    "idempotency_key": "translate-next-click-1",
                }

                first_response = client.post(
                    f"/documents/{document_id}/translate-next",
                    json=request_body,
                )
                duplicate_response = client.post(
                    f"/documents/{document_id}/translate-next",
                    json=request_body,
                )
                sections_response = client.get(f"/documents/{document_id}/sections")
                usage_response = client.get("/usage/me")
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(first_response.status_code, 202)
        self.assertEqual(duplicate_response.status_code, 202)
        self.assertEqual(
            duplicate_response.json()["job_id"],
            first_response.json()["job_id"],
        )
        self.assertTrue(duplicate_response.json()["idempotent_replay"])
        self.assertEqual(sections_response.json()["translation_cursor"], 1)
        self.assertEqual(sections_response.json()["next_section_id"], "sec_0002")
        self.assertEqual(usage_response.json()["record_count"], 1)

    def test_translate_next_duplicate_click_while_running_returns_active_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.\n\nSecond block.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]
            active_job = backend_api.store.create_job(
                document_id=document_id,
                job_type="translate_next",
                payload={"target_language": "Spanish"},
            )
            backend_api.store.update_job(
                str(active_job["job_id"]),
                status="running",
                progress=25,
                message="Translating next section",
            )

            duplicate_response = client.post(
                f"/documents/{document_id}/translate-next",
                json={"target_language": "Spanish"},
            )
            sections_response = client.get(f"/documents/{document_id}/sections")

        self.assertEqual(duplicate_response.status_code, 202)
        self.assertEqual(duplicate_response.json()["job_id"], active_job["job_id"])
        self.assertTrue(duplicate_response.json()["reused_active_job"])
        self.assertFalse(duplicate_response.json()["idempotent_replay"])
        self.assertEqual(sections_response.json()["translation_cursor"], 0)
        self.assertEqual(sections_response.json()["next_section_id"], "sec_0001")

    def test_translate_next_rejects_reused_idempotency_key_with_different_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]
                client.post(
                    f"/documents/{document_id}/translate-next",
                    json={
                        "target_language": "Spanish",
                        "idempotency_key": "same-key",
                    },
                )

                duplicate_response = client.post(
                    f"/documents/{document_id}/translate-next",
                    json={
                        "target_language": "German",
                        "idempotency_key": "same-key",
                    },
                )
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(duplicate_response.status_code, 409)
        self.assertIn(
            "different options",
            duplicate_response.json()["detail"]["message"],
        )

    def test_translate_rest_endpoint_creates_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.\n\nSecond block.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]

                translate_response = client.post(
                    f"/documents/{document_id}/translate-rest",
                    json={"target_language": "Spanish"},
                )
                job_response = client.get(translate_response.json()["poll_url"])
                sections_response = client.get(f"/documents/{document_id}/sections")
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(translate_response.status_code, 202)
        self.assertEqual(job_response.json()["status"], "succeeded")
        self.assertEqual(job_response.json()["result"]["mode"], "translate_rest")
        self.assertEqual(sections_response.json()["next_section_id"], None)
        self.assertEqual(
            sections_response.json()["remaining_estimate"]["remaining_block_count"],
            0,
        )

    def test_translate_rest_idempotency_key_replays_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            original_factory = backend_api.translator_factory
            backend_api.translator_factory = FakeTranslationClient
            client = TestClient(backend_api.app)
            try:
                upload_response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"Hello world.\n\nSecond block.",
                            "text/plain",
                        )
                    },
                )
                document_id = upload_response.json()["document_id"]
                request_body = {
                    "target_language": "Spanish",
                    "idempotency_key": "translate-rest-click-1",
                }

                first_response = client.post(
                    f"/documents/{document_id}/translate-rest",
                    json=request_body,
                )
                duplicate_response = client.post(
                    f"/documents/{document_id}/translate-rest",
                    json=request_body,
                )
            finally:
                backend_api.translator_factory = original_factory

        self.assertEqual(first_response.status_code, 202)
        self.assertEqual(duplicate_response.status_code, 202)
        self.assertEqual(
            duplicate_response.json()["job_id"],
            first_response.json()["job_id"],
        )
        self.assertTrue(duplicate_response.json()["idempotent_replay"])

    def test_translate_rest_duplicate_click_while_running_returns_active_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.\n\nSecond block.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]
            active_job = backend_api.store.create_job(
                document_id=document_id,
                job_type="translate_rest",
                payload={"target_language": "Spanish"},
            )
            backend_api.store.update_job(
                str(active_job["job_id"]),
                status="running",
                progress=25,
                message="Translating rest of document",
            )

            duplicate_response = client.post(
                f"/documents/{document_id}/translate-rest",
                json={"target_language": "Spanish"},
            )
            sections_response = client.get(f"/documents/{document_id}/sections")

        self.assertEqual(duplicate_response.status_code, 202)
        self.assertEqual(duplicate_response.json()["job_id"], active_job["job_id"])
        self.assertTrue(duplicate_response.json()["reused_active_job"])
        self.assertFalse(duplicate_response.json()["idempotent_replay"])
        self.assertEqual(
            sections_response.json()["remaining_estimate"]["remaining_block_count"],
            2,
        )

    def test_upload_rejects_files_over_size_limit(self) -> None:
        previous_limit = os.environ.get("TRANSLATOR_MAX_UPLOAD_BYTES")
        os.environ["TRANSLATOR_MAX_UPLOAD_BYTES"] = "5"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
                client = TestClient(backend_api.app)

                response = client.post(
                    "/documents/upload",
                    data={"target_words_per_section": "100"},
                    files={
                        "file": (
                            "sample.txt",
                            b"this is too large",
                            "text/plain",
                        )
                    },
                )
        finally:
            if previous_limit is None:
                os.environ.pop("TRANSLATOR_MAX_UPLOAD_BYTES", None)
            else:
                os.environ["TRANSLATOR_MAX_UPLOAD_BYTES"] = previous_limit

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "file_too_large")
        self.assertIn("too large", response.json()["detail"]["message"])

    def test_upload_rejects_unsupported_file_type_with_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.pdf",
                        b"%PDF-not-supported",
                        "application/pdf",
                    )
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_file_type")
        self.assertIn("DOCX", response.json()["detail"]["action"])

    def test_upload_rejects_empty_file_with_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "empty.txt",
                        b"",
                        "text/plain",
                    )
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "empty_upload")
        self.assertIn("empty", response.json()["detail"]["message"])

    def test_upload_rejects_document_without_readable_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "blank.txt",
                        b"   \n\n\t ",
                        "text/plain",
                    )
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "no_translatable_text")
        self.assertIn("readable text", response.json()["detail"]["message"])

    def test_unknown_document_error_is_user_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            response = client.get("/documents/doc_missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "document_not_found")
        self.assertNotIn("backend_storage", response.json()["detail"]["message"])

    def test_request_validation_error_is_user_safe(self) -> None:
        client = TestClient(backend_api.app)

        response = client.get("/documents/doc_missing/preview?limit=500")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "validation_error")
        self.assertIn("request fields", response.json()["detail"]["message"])

    def test_same_language_translation_error_is_user_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                data={
                    "target_words_per_section": "100",
                    "source_language": "English",
                },
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]

            response = client.post(
                f"/documents/{document_id}/translate-next",
                json={"source_language": "English", "target_language": "English"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "same_language_pair")
        self.assertIn("different", response.json()["detail"]["message"])

    def test_download_before_export_has_export_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)
            upload_response = client.post(
                "/documents/upload",
                data={"target_words_per_section": "100"},
                files={
                    "file": (
                        "sample.txt",
                        b"Hello world.",
                        "text/plain",
                    )
                },
            )
            document_id = upload_response.json()["document_id"]

            response = client.get(f"/documents/{document_id}/exports/latest/download")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "export_not_found")
        self.assertIn("export", response.json()["detail"]["message"])

    def test_provider_errors_are_user_safe(self) -> None:
        timeout_message = backend_api.format_translation_error(
            RuntimeError("litellm.Timeout: Nvidia_nimException - Error code: 504")
        )
        model_message = backend_api.format_translation_error(
            RuntimeError("Unsupported MODEL_PROVIDER. Use MODEL_PROVIDER=nvidia or MODEL_PROVIDER=gemini.")
        )
        generic_message = backend_api.format_translation_error(
            RuntimeError("Traceback with private implementation detail")
        )

        self.assertIn("timed out", timeout_message)
        self.assertIn("model", model_message)
        self.assertNotIn("Traceback", generic_message)

    def test_get_job_returns_404_for_unknown_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            backend_api.store = LocalDocumentStore(Path(temp_dir) / "backend")
            client = TestClient(backend_api.app)

            response = client.get("/jobs/job_000000000000")

        self.assertEqual(response.status_code, 404)


def restore_env_var(name: str, previous_value: str | None) -> None:
    if previous_value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous_value


def razorpay_payment_event(
    *,
    order_id: str,
    razorpay_order_id: str,
    payment_id: str,
    owner_user_id: str,
    package_id: str,
    amount: int,
    status: str = "captured",
) -> dict[str, object]:
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": razorpay_order_id,
                    "status": status,
                    "amount": amount,
                    "currency": "USD",
                    "notes": {
                        "order_id": order_id,
                        "owner_user_id": owner_user_id,
                        "package_id": package_id,
                        "credits": "999999",
                    },
                }
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
