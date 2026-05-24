from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any

try:
    import mysql.connector
except ImportError:  # pragma: no cover - exercised only when dependency is absent.
    mysql = None
else:
    mysql = mysql.connector

from translator.storage.local import (
    DEFAULT_OWNER_AUTH_PROVIDER,
    DEFAULT_OWNER_EMAIL,
    DEFAULT_OWNER_USER_ID,
    DEFAULT_SECTION_TARGET_WORDS,
    DEFAULT_SOURCE_LANGUAGE,
    InsufficientCreditsError,
    LocalDocumentStore,
    SUPPORTED_UPLOAD_SUFFIXES,
    build_document_sections,
    clamp_section_target,
    normalize_language_name,
    normalized_usage_fields,
    parsed_document_from_dict,
    token_price_per_1m_usd,
    utc_now_iso,
)
from translator.documents.adapters import load_document
from translator.documents.model import ParsedDocument
from translator.documents.writers import (
    BlockTranslation,
    default_translated_document_path,
    write_translated_document,
)


DOCUMENT_JSON_COLUMNS = {
    "metadata_json",
    "parsed_document_json",
    "sections_json",
    "translations_json",
    "section_translations_json",
    "glossary_json",
    "latest_export_json",
}


@dataclass(frozen=True)
class MySqlConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    ssl_disabled: bool
    auto_create_database: bool
    auto_init_schema: bool


class MySqlDocumentStore(LocalDocumentStore):
    """MySQL-backed state store with local files for originals and exports."""

    def __init__(self, root: Path, config: MySqlConfig | None = None) -> None:
        super().__init__(root)
        self.config = config or mysql_config_from_env()
        if mysql is None:
            raise RuntimeError(
                "MySQL backend selected but mysql-connector-python is not installed."
            )
        if self.config.auto_create_database:
            self.ensure_database()
        if self.config.auto_init_schema:
            self.initialize_schema()

    def create_document(
        self,
        source_path: Path,
        original_filename: str,
        target_words_per_section: int = DEFAULT_SECTION_TARGET_WORDS,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        owner_user_id: str = DEFAULT_OWNER_USER_ID,
        owner_email: str = DEFAULT_OWNER_EMAIL,
        owner_auth_provider: str = DEFAULT_OWNER_AUTH_PROVIDER,
    ) -> dict[str, object]:
        suffix = source_path.suffix.lower()
        if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))
            raise ValueError(f"Unsupported file type '{suffix}'. Supported types: {supported}.")

        document_id = self._new_document_id()
        document_dir = self.document_dir(document_id)
        originals_dir = document_dir / "original"
        originals_dir.mkdir(parents=True, exist_ok=True)
        original_path = originals_dir / f"original{suffix}"
        shutil.copyfile(source_path, original_path)

        try:
            parsed_document = load_document(original_path)
            sections = build_document_sections(
                parsed_document,
                target_words_per_section=target_words_per_section,
            )
            if not sections:
                raise ValueError("No translatable text was found in the uploaded document.")
            metadata = {
                "document_id": document_id,
                "original_filename": original_filename,
                "source_format": parsed_document.source_format,
                "source_language": normalize_language_name(source_language),
                "created_at": utc_now_iso(),
                "target_words_per_section": clamp_section_target(target_words_per_section),
                "owner_user_id": owner_user_id,
                "owner_email": owner_email,
                "owner_auth_provider": owner_auth_provider,
            }
            self._insert_document(
                document_id=document_id,
                owner_user_id=owner_user_id,
                owner_email=owner_email,
                owner_auth_provider=owner_auth_provider,
                original_filename=original_filename,
                source_format=parsed_document.source_format,
                target_words_per_section=clamp_section_target(target_words_per_section),
                metadata=metadata,
                parsed_document=parsed_document.to_dict(),
                sections=[section.to_dict() for section in sections],
            )
        except Exception:
            if document_dir.exists():
                shutil.rmtree(document_dir, ignore_errors=True)
            raise

        return self.document_summary(document_id)

    def load_metadata(self, document_id: str) -> dict[str, object]:
        return dict(self._document_json(document_id, "metadata_json", {}))

    def load_parsed_document(self, document_id: str) -> ParsedDocument:
        return parsed_document_from_dict(
            dict(self._document_json(document_id, "parsed_document_json", {}))
        )

    def load_sections(self, document_id: str) -> list[dict[str, object]]:
        raw_sections = self._document_json(document_id, "sections_json", [])
        if not isinstance(raw_sections, list):
            raise ValueError(f"Invalid section data for document {document_id}.")
        return [dict(section) for section in raw_sections if isinstance(section, dict)]

    def load_translations(self, document_id: str) -> dict[str, BlockTranslation]:
        raw_translations = self._document_json(document_id, "translations_json", {})
        if not isinstance(raw_translations, dict):
            return {}
        return {
            str(block_id): translation
            for block_id, translation in raw_translations.items()
            if isinstance(translation, (str, dict))
        }

    def save_translations(
        self,
        document_id: str,
        translations: dict[str, BlockTranslation],
    ) -> None:
        self._update_document_json(document_id, "translations_json", translations)

    def load_section_translations(self, document_id: str) -> dict[str, dict[str, object]]:
        raw_items = self._document_json(document_id, "section_translations_json", {})
        if not isinstance(raw_items, dict):
            return {}
        return {
            str(section_id): dict(value)
            for section_id, value in raw_items.items()
            if isinstance(value, dict)
        }

    def save_section_translation(
        self,
        document_id: str,
        section_id: str,
        translated_chunk: dict[str, object],
    ) -> None:
        section_translations = self.load_section_translations(document_id)
        section_translations[section_id] = translated_chunk
        self._update_document_json(
            document_id,
            "section_translations_json",
            section_translations,
        )

    def load_glossary(self, document_id: str) -> list[dict[str, object]]:
        raw_glossary = self._document_json(document_id, "glossary_json", [])
        if not isinstance(raw_glossary, list):
            return []
        return [dict(entry) for entry in raw_glossary if isinstance(entry, dict)]

    def save_glossary(
        self,
        document_id: str,
        glossary: list[dict[str, object]],
    ) -> None:
        self._update_document_json(document_id, "glossary_json", glossary)

    def export_document(self, document_id: str) -> dict[str, object]:
        parsed_document = self.load_parsed_document(document_id)
        translations = self.load_translations(document_id)
        export_dir = self.document_dir(document_id) / "exports"
        output_path = default_translated_document_path(
            output_dir=export_dir,
            source_format=parsed_document.source_format,
        )
        report = write_translated_document(
            parsed_document=parsed_document,
            output_path=output_path,
            translations_by_block_id=translations,
        )
        export_metadata = {
            "document_id": document_id,
            "created_at": utc_now_iso(),
            "output_path": str(output_path),
            "download_path": f"/documents/{document_id}/exports/latest/download",
            "report": report.to_dict(),
        }
        self._update_document_json(document_id, "latest_export_json", export_metadata)
        return export_metadata

    def latest_export_file(self, document_id: str) -> Path:
        metadata = self._document_json(document_id, "latest_export_json", {})
        output_path = Path(str(metadata.get("output_path", "")))
        if not output_path.exists():
            raise FileNotFoundError(f"No export file found for document {document_id}.")
        return output_path

    def create_job(
        self,
        document_id: str,
        job_type: str,
        payload: dict[str, object] | None = None,
        owner_user_id: str | None = None,
        owner_email: str | None = None,
        owner_auth_provider: str | None = None,
    ) -> dict[str, object]:
        metadata = self.load_metadata(document_id)
        job_id = self._new_job_id({})
        now = utc_now_iso()
        job = {
            "job_id": job_id,
            "document_id": document_id,
            "owner_user_id": owner_user_id or str(metadata.get("owner_user_id", "")),
            "owner_email": owner_email or str(metadata.get("owner_email", "")),
            "owner_auth_provider": owner_auth_provider
            or str(metadata.get("owner_auth_provider", "")),
            "type": job_type,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "payload": payload or {},
            "result": {},
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO jobs (
                  job_id, document_id, owner_user_id, owner_email,
                  owner_auth_provider, job_type, status, progress, message,
                  payload_json, result_json, error_text, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    job["job_id"],
                    job["document_id"],
                    job["owner_user_id"],
                    job["owner_email"],
                    job["owner_auth_provider"],
                    job["type"],
                    job["status"],
                    job["progress"],
                    job["message"],
                    json_text(job["payload"]),
                    json_text(job["result"]),
                    job["error"],
                    db_datetime_from_iso(str(job["created_at"])),
                    db_datetime_from_iso(str(job["updated_at"])),
                ),
            )
            connection.commit()
        return job

    def load_jobs(self) -> dict[str, dict[str, object]]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM jobs ORDER BY created_at ASC, job_id ASC")
            rows = cursor.fetchall()
        return {str(row["job_id"]): self._job_from_row(row) for row in rows}

    def load_job(self, job_id: str) -> dict[str, object]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        return self._job_from_row(row)

    def update_job(self, job_id: str, **updates: object) -> dict[str, object]:
        if not updates:
            return self.load_job(job_id)
        column_map = {
            "type": "job_type",
            "status": "status",
            "progress": "progress",
            "message": "message",
            "payload": "payload_json",
            "result": "result_json",
            "error": "error_text",
        }
        set_fragments = []
        values = []
        for key, value in updates.items():
            column = column_map.get(key)
            if column is None:
                continue
            set_fragments.append(f"{column} = %s")
            if column in {"payload_json", "result_json"}:
                values.append(json_text(value if isinstance(value, dict) else {}))
            else:
                values.append(value)
        if not set_fragments:
            return self.load_job(job_id)
        set_fragments.append("updated_at = %s")
        values.append(db_datetime_from_iso(utc_now_iso()))
        values.append(job_id)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"UPDATE jobs SET {', '.join(set_fragments)} WHERE job_id = %s",
                tuple(values),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown job_id: {job_id}")
        return self.load_job(job_id)

    def record_usage(
        self,
        job: dict[str, object],
        usage: dict[str, object],
    ) -> dict[str, object]:
        existing_record = self.usage_record_for_job(str(job.get("job_id", "")))
        if existing_record is not None:
            return existing_record

        usage_id = self._new_usage_id({})
        payload = job.get("payload", {})
        payload = payload if isinstance(payload, dict) else {}
        record = {
            "usage_id": usage_id,
            "job_id": job.get("job_id"),
            "document_id": job.get("document_id"),
            "job_type": job.get("type"),
            "owner_user_id": job.get("owner_user_id"),
            "owner_email": job.get("owner_email"),
            "owner_auth_provider": job.get("owner_auth_provider"),
            "source_language": payload.get("source_language"),
            "target_language": payload.get("target_language"),
            "document_type": payload.get("document_type"),
            "content_form": payload.get("content_form"),
            "created_at": utc_now_iso(),
            **normalized_usage_fields(usage),
        }
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO usage_records (
                  usage_id, job_id, document_id, owner_user_id, owner_email,
                  owner_auth_provider, job_type, mode, section_id, source_language,
                  target_language, document_type, content_form, word_count, chunk_count, chunk_size_words,
                  translated_block_count, estimated_input_tokens,
                  estimated_output_tokens, estimated_prompt_overhead_tokens,
                  estimated_total_tokens, estimated_cost_usd,
                  estimated_cost_per_word_usd, token_price_per_1m_usd, created_at
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    record["usage_id"],
                    record["job_id"],
                    record["document_id"],
                    record["owner_user_id"],
                    record["owner_email"],
                    record["owner_auth_provider"],
                    record["job_type"],
                    record["mode"],
                    record["section_id"],
                    record["source_language"],
                    record["target_language"],
                    record["document_type"],
                    record["content_form"],
                    record["word_count"],
                    record["chunk_count"],
                    record["chunk_size_words"],
                    record["translated_block_count"],
                    record["estimated_input_tokens"],
                    record["estimated_output_tokens"],
                    record["estimated_prompt_overhead_tokens"],
                    record["estimated_total_tokens"],
                    record["estimated_cost_usd"],
                    record["estimated_cost_per_word_usd"],
                    token_price_per_1m_usd(),
                    db_datetime_from_iso(str(record["created_at"])),
                ),
            )
            connection.commit()
        return record

    def usage_record_for_job(self, job_id: str) -> dict[str, object] | None:
        if not job_id:
            return None
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM usage_records WHERE job_id = %s", (job_id,))
            row = cursor.fetchone()
        return self._usage_from_row(row) if row is not None else None

    def load_usage_records(self) -> dict[str, dict[str, object]]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM usage_records ORDER BY created_at DESC")
            rows = cursor.fetchall()
        return {str(row["usage_id"]): self._usage_from_row(row) for row in rows}

    def credit_ledger_for_owner(self, owner_user_id: str) -> list[dict[str, object]]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT *
                FROM credit_ledger
                WHERE owner_user_id = %s
                ORDER BY created_at DESC, entry_id DESC
                """,
                (owner_user_id,),
            )
            rows = cursor.fetchall()
        return [self._credit_entry_from_row(row) for row in rows]

    def credit_balance(self, owner_user_id: str) -> int:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(credit_delta), 0) FROM credit_ledger WHERE owner_user_id = %s",
                (owner_user_id,),
            )
            row = cursor.fetchone()
        return int(row[0] if row else 0)

    def ensure_signup_credit_grant(
        self,
        owner_user_id: str,
        owner_email: str,
        owner_auth_provider: str,
        credits: int,
    ) -> dict[str, object] | None:
        credit_count = max(0, int(credits))
        if credit_count <= 0:
            return None
        with self.connection() as connection:
            lock_cursor = connection.cursor()
            self._acquire_mysql_lock(lock_cursor, f"credit_signup:{owner_user_id}")
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT *
                    FROM credit_ledger
                    WHERE owner_user_id = %s AND entry_type = 'signup_grant'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (owner_user_id,),
                )
                row = cursor.fetchone()
                if row is not None:
                    return self._credit_entry_from_row(row)
                entry_id = self._new_credit_entry_id({})
                now = utc_now_iso()
                insert_cursor = connection.cursor()
                insert_cursor.execute(
                    """
                    INSERT INTO credit_ledger (
                      entry_id, owner_user_id, owner_email, owner_auth_provider,
                      entry_type, credit_delta, credits, status, metadata_json,
                      created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry_id,
                        owner_user_id,
                        owner_email,
                        owner_auth_provider,
                        "signup_grant",
                        credit_count,
                        credit_count,
                        "posted",
                        json_text({"reason": "one_time_signup_credit"}),
                        db_datetime_from_iso(now),
                        db_datetime_from_iso(now),
                    ),
                )
                connection.commit()
                cursor.execute("SELECT * FROM credit_ledger WHERE entry_id = %s", (entry_id,))
                return self._credit_entry_from_row(cursor.fetchone())
            finally:
                self._release_mysql_lock(lock_cursor, f"credit_signup:{owner_user_id}")

    def create_credit_ledger_entry(
        self,
        owner_user_id: str,
        owner_email: str,
        owner_auth_provider: str,
        entry_type: str,
        credit_delta: int,
        credits: int,
        status: str = "posted",
        job_id: str | None = None,
        document_id: str | None = None,
        order_id: str | None = None,
        model_tier: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        entry_id = self._new_credit_entry_id({})
        now = utc_now_iso()
        entry = {
            "entry_id": entry_id,
            "owner_user_id": owner_user_id,
            "owner_email": owner_email,
            "owner_auth_provider": owner_auth_provider,
            "entry_type": entry_type,
            "credit_delta": int(credit_delta),
            "credits": max(0, int(credits)),
            "status": status,
            "job_id": job_id,
            "document_id": document_id,
            "order_id": order_id,
            "model_tier": model_tier,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO credit_ledger (
                  entry_id, owner_user_id, owner_email, owner_auth_provider,
                  entry_type, credit_delta, credits, status, job_id, document_id,
                  order_id, model_tier, metadata_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry["entry_id"],
                    entry["owner_user_id"],
                    entry["owner_email"],
                    entry["owner_auth_provider"],
                    entry["entry_type"],
                    entry["credit_delta"],
                    entry["credits"],
                    entry["status"],
                    entry["job_id"],
                    entry["document_id"],
                    entry["order_id"],
                    entry["model_tier"],
                    json_text(entry["metadata"]),
                    db_datetime_from_iso(str(entry["created_at"])),
                    db_datetime_from_iso(str(entry["updated_at"])),
                ),
            )
            connection.commit()
        return entry

    def create_payment_order(
        self,
        owner_user_id: str,
        owner_email: str,
        owner_auth_provider: str,
        package: dict[str, object],
        provider: str,
    ) -> dict[str, object]:
        order_id = self._new_payment_order_id({})
        now = utc_now_iso()
        order = {
            "order_id": order_id,
            "owner_user_id": owner_user_id,
            "owner_email": owner_email,
            "owner_auth_provider": owner_auth_provider,
            "package_id": package.get("package_id"),
            "credits": int(package.get("credits", 0) or 0),
            "amount_cents": int(package.get("amount_cents", 0) or 0),
            "currency": str(package.get("currency", "USD")),
            "provider": provider,
            "status": "pending",
            "checkout_url": f"/billing/mock-checkout/{order_id}",
            "external_payment_id": None,
            "metadata": {"package_name": package.get("name")},
            "created_at": now,
            "updated_at": now,
        }
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO payment_orders (
                  order_id, owner_user_id, owner_email, owner_auth_provider,
                  package_id, credits, amount_cents, currency, provider, status,
                  checkout_url, external_payment_id, metadata_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    order["order_id"],
                    order["owner_user_id"],
                    order["owner_email"],
                    order["owner_auth_provider"],
                    order["package_id"],
                    order["credits"],
                    order["amount_cents"],
                    order["currency"],
                    order["provider"],
                    order["status"],
                    order["checkout_url"],
                    order["external_payment_id"],
                    json_text(order["metadata"]),
                    db_datetime_from_iso(str(order["created_at"])),
                    db_datetime_from_iso(str(order["updated_at"])),
                ),
            )
            connection.commit()
        return order

    def update_payment_order_checkout(
        self,
        order_id: str,
        checkout_url: str,
        external_payment_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        order = self.load_payment_order(order_id)
        merged_metadata = dict(order.get("metadata", {}))
        if metadata:
            merged_metadata.update(metadata)
        now = utc_now_iso()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE payment_orders
                SET checkout_url = %s,
                    external_payment_id = COALESCE(%s, external_payment_id),
                    metadata_json = %s,
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    checkout_url,
                    external_payment_id,
                    json_text(merged_metadata),
                    db_datetime_from_iso(now),
                    order_id,
                ),
            )
            connection.commit()
        return self.load_payment_order(order_id)

    def load_payment_order(self, order_id: str) -> dict[str, object]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM payment_orders WHERE order_id = %s", (order_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown order_id: {order_id}")
        return self._payment_order_from_row(row)

    def payment_order_for_external_id(self, external_payment_id: str) -> dict[str, object]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE external_payment_id = %s
                   OR JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.razorpay_order_id')) = %s
                LIMIT 1
                """,
                (external_payment_id, external_payment_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown external_payment_id: {external_payment_id}")
        return self._payment_order_from_row(row)

    def complete_payment_order(
        self,
        order_id: str,
        external_payment_id: str,
    ) -> dict[str, object]:
        with self.connection() as connection:
            lock_cursor = connection.cursor()
            self._acquire_mysql_lock(lock_cursor, f"payment_order:{order_id}")
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM payment_orders WHERE order_id = %s", (order_id,))
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(f"Unknown order_id: {order_id}")
                order = self._payment_order_from_row(row)
                if order["status"] == "paid":
                    return order
                if order["status"] != "pending":
                    raise ValueError("Payment order is not payable.")
                now = utc_now_iso()
                cursor = connection.cursor()
                cursor.execute(
                    """
                    UPDATE payment_orders
                    SET status = %s, external_payment_id = %s, updated_at = %s
                    WHERE order_id = %s
                    """,
                    ("paid", external_payment_id, db_datetime_from_iso(now), order_id),
                )
                cursor.execute(
                    """
                    SELECT 1 FROM credit_ledger
                    WHERE order_id = %s AND entry_type = 'purchase'
                    LIMIT 1
                    """,
                    (order_id,),
                )
                has_purchase = cursor.fetchone() is not None
                if not has_purchase:
                    entry_id = self._new_credit_entry_id({})
                    cursor.execute(
                        """
                        INSERT INTO credit_ledger (
                          entry_id, owner_user_id, owner_email, owner_auth_provider,
                          entry_type, credit_delta, credits, status, order_id,
                          metadata_json, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            entry_id,
                            order["owner_user_id"],
                            order["owner_email"],
                            order["owner_auth_provider"],
                            "purchase",
                            order["credits"],
                            order["credits"],
                            "posted",
                            order_id,
                            json_text(
                                {
                                    "provider": order["provider"],
                                    "external_payment_id": external_payment_id,
                                    "amount_cents": order["amount_cents"],
                                    "currency": order["currency"],
                                }
                            ),
                            db_datetime_from_iso(now),
                            db_datetime_from_iso(now),
                        ),
                    )
                connection.commit()
            finally:
                self._release_mysql_lock(lock_cursor, f"payment_order:{order_id}")
        paid_order = self.load_payment_order(order_id)
        return paid_order

    def credit_reservation_for_job(self, job_id: str) -> dict[str, object] | None:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT *
                FROM credit_ledger
                WHERE job_id = %s AND entry_type = 'reserve'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (job_id,),
            )
            row = cursor.fetchone()
        return self._credit_entry_from_row(row) if row is not None else None

    def reserve_credits_for_job(
        self,
        job: dict[str, object],
        credits: int,
        model_tier: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        credit_count = max(0, int(credits))
        if credit_count <= 0:
            return None
        existing = self.credit_reservation_for_job(str(job.get("job_id", "")))
        if existing is not None:
            return existing
        owner_user_id = str(job.get("owner_user_id", ""))
        with self.connection() as connection:
            cursor = connection.cursor()
            self._acquire_mysql_lock(cursor, f"credit_wallet:{owner_user_id}")
            try:
                cursor.execute(
                    "SELECT COALESCE(SUM(credit_delta), 0) FROM credit_ledger WHERE owner_user_id = %s",
                    (owner_user_id,),
                )
                row = cursor.fetchone()
                available = int(row[0] if row else 0)
                if available < credit_count:
                    raise InsufficientCreditsError(credit_count, available)
                entry_id = self._new_credit_entry_id({})
                now = utc_now_iso()
                cursor.execute(
                    """
                    INSERT INTO credit_ledger (
                      entry_id, owner_user_id, owner_email, owner_auth_provider,
                      entry_type, credit_delta, credits, status, job_id, document_id,
                      model_tier, metadata_json, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry_id,
                        owner_user_id,
                        str(job.get("owner_email", "")),
                        str(job.get("owner_auth_provider", "")),
                        "reserve",
                        -credit_count,
                        credit_count,
                        "active",
                        str(job.get("job_id", "")),
                        str(job.get("document_id", "")),
                        model_tier,
                        json_text(metadata or {}),
                        db_datetime_from_iso(now),
                        db_datetime_from_iso(now),
                    ),
                )
                connection.commit()
            finally:
                self._release_mysql_lock(cursor, f"credit_wallet:{owner_user_id}")
        return self.credit_reservation_for_job(str(job.get("job_id", "")))

    def capture_credit_reservation(
        self,
        job: dict[str, object],
        actual_credits: int,
        usage_record: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        reservation = self.credit_reservation_for_job(str(job.get("job_id", "")))
        if reservation is None or reservation.get("status") != "active":
            return reservation
        reserved_credits = int(reservation.get("credits", 0) or 0)
        charged_credits = min(reserved_credits, max(0, int(actual_credits)))
        refunded_credits = max(0, reserved_credits - charged_credits)
        metadata = dict(reservation.get("metadata", {}))
        metadata.update(
            {
                "charged_credits": charged_credits,
                "refunded_credits": refunded_credits,
                "usage_id": usage_record.get("usage_id") if usage_record else None,
            }
        )
        now = utc_now_iso()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE credit_ledger
                SET status = %s, metadata_json = %s, updated_at = %s
                WHERE entry_id = %s
                """,
                (
                    "captured",
                    json_text(metadata),
                    db_datetime_from_iso(now),
                    reservation["entry_id"],
                ),
            )
            charge_entry_id = self._new_credit_entry_id({})
            cursor.execute(
                """
                INSERT INTO credit_ledger (
                  entry_id, owner_user_id, owner_email, owner_auth_provider,
                  entry_type, credit_delta, credits, status, job_id, document_id,
                  model_tier, metadata_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    charge_entry_id,
                    job.get("owner_user_id"),
                    job.get("owner_email"),
                    job.get("owner_auth_provider"),
                    "charge",
                    0,
                    charged_credits,
                    "posted",
                    job.get("job_id"),
                    job.get("document_id"),
                    reservation.get("model_tier"),
                    json_text(
                        {
                            "reservation_id": reservation["entry_id"],
                            "usage_id": metadata.get("usage_id"),
                        }
                    ),
                    db_datetime_from_iso(now),
                    db_datetime_from_iso(now),
                ),
            )
            if refunded_credits:
                refund_entry_id = self._new_credit_entry_id({})
                cursor.execute(
                    """
                    INSERT INTO credit_ledger (
                      entry_id, owner_user_id, owner_email, owner_auth_provider,
                      entry_type, credit_delta, credits, status, job_id, document_id,
                      model_tier, metadata_json, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        refund_entry_id,
                        job.get("owner_user_id"),
                        job.get("owner_email"),
                        job.get("owner_auth_provider"),
                        "refund",
                        refunded_credits,
                        refunded_credits,
                        "posted",
                        job.get("job_id"),
                        job.get("document_id"),
                        reservation.get("model_tier"),
                        json_text(
                            {
                                "reservation_id": reservation["entry_id"],
                                "reason": "unused_reserved_credits",
                            }
                        ),
                        db_datetime_from_iso(now),
                        db_datetime_from_iso(now),
                    ),
                )
            connection.commit()
        return self.credit_reservation_for_job(str(job.get("job_id", "")))

    def release_credit_reservation(
        self,
        job: dict[str, object],
        reason: str,
    ) -> dict[str, object] | None:
        reservation = self.credit_reservation_for_job(str(job.get("job_id", "")))
        if reservation is None or reservation.get("status") != "active":
            return reservation
        reserved_credits = int(reservation.get("credits", 0) or 0)
        metadata = dict(reservation.get("metadata", {}))
        metadata.update({"released_credits": reserved_credits, "release_reason": reason})
        now = utc_now_iso()
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE credit_ledger
                SET status = %s, metadata_json = %s, updated_at = %s
                WHERE entry_id = %s
                """,
                (
                    "released",
                    json_text(metadata),
                    db_datetime_from_iso(now),
                    reservation["entry_id"],
                ),
            )
            if reserved_credits:
                refund_entry_id = self._new_credit_entry_id({})
                cursor.execute(
                    """
                    INSERT INTO credit_ledger (
                      entry_id, owner_user_id, owner_email, owner_auth_provider,
                      entry_type, credit_delta, credits, status, job_id, document_id,
                      model_tier, metadata_json, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        refund_entry_id,
                        job.get("owner_user_id"),
                        job.get("owner_email"),
                        job.get("owner_auth_provider"),
                        "refund",
                        reserved_credits,
                        reserved_credits,
                        "posted",
                        job.get("job_id"),
                        job.get("document_id"),
                        reservation.get("model_tier"),
                        json_text({"reservation_id": reservation["entry_id"], "reason": reason}),
                        db_datetime_from_iso(now),
                        db_datetime_from_iso(now),
                    ),
                )
            connection.commit()
        return self.credit_reservation_for_job(str(job.get("job_id", "")))

    def document_metadata_for_owner(self, owner_user_id: str) -> list[dict[str, object]]:
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT metadata_json
                FROM documents
                WHERE owner_user_id = %s
                ORDER BY created_at DESC
                """,
                (owner_user_id,),
            )
            rows = cursor.fetchall()
        return [
            dict(json_value(row.get("metadata_json"), {}))
            for row in rows
            if isinstance(json_value(row.get("metadata_json"), {}), dict)
        ]

    def ensure_database(self) -> None:
        with self.connection(include_database=False) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            connection.commit()

    def initialize_schema(self) -> None:
        schema_path = Path(__file__).with_name("mysql_schema.sql")
        statements = [
            statement.strip()
            for statement in schema_path.read_text(encoding="utf-8").split(";")
            if statement.strip()
        ]
        with self.connection() as connection:
            cursor = connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            self._ensure_usage_schema_columns(cursor)
            self._ensure_billing_schema_indexes(cursor)
            connection.commit()

    def connection(self, include_database: bool = True):
        if mysql is None:
            raise RuntimeError(
                "MySQL backend selected but mysql-connector-python is not installed."
            )
        return mysql.connect(**self._connection_kwargs(include_database=include_database))

    def document_exists(self, document_id: str) -> bool:
        try:
            self.document_dir(document_id)
        except KeyError:
            return False
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT 1 FROM documents WHERE document_id = %s LIMIT 1",
                (document_id,),
            )
            return cursor.fetchone() is not None

    def job_exists(self, job_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT 1 FROM jobs WHERE job_id = %s LIMIT 1", (job_id,))
            return cursor.fetchone() is not None

    def usage_exists(self, usage_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT 1 FROM usage_records WHERE usage_id = %s LIMIT 1",
                (usage_id,),
            )
            return cursor.fetchone() is not None

    def credit_entry_exists(self, entry_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT 1 FROM credit_ledger WHERE entry_id = %s LIMIT 1",
                (entry_id,),
            )
            return cursor.fetchone() is not None

    def payment_order_exists(self, order_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT 1 FROM payment_orders WHERE order_id = %s LIMIT 1",
                (order_id,),
            )
            return cursor.fetchone() is not None

    def _new_document_id(self) -> str:
        while True:
            document_id = f"doc_{secrets.token_hex(6)}"
            if not self.document_exists(document_id):
                return document_id

    def _new_job_id(self, jobs: dict[str, dict[str, object]]) -> str:
        while True:
            job_id = f"job_{secrets.token_hex(6)}"
            if job_id not in jobs and not self.job_exists(job_id):
                return job_id

    def _new_usage_id(self, records: dict[str, dict[str, object]]) -> str:
        while True:
            usage_id = f"usage_{secrets.token_hex(6)}"
            if usage_id not in records and not self.usage_exists(usage_id):
                return usage_id

    def _new_credit_entry_id(self, entries: dict[str, dict[str, object]]) -> str:
        while True:
            entry_id = f"cred_{secrets.token_hex(6)}"
            if entry_id not in entries and not self.credit_entry_exists(entry_id):
                return entry_id

    def _new_payment_order_id(self, orders: dict[str, dict[str, object]]) -> str:
        while True:
            order_id = f"pay_{secrets.token_hex(6)}"
            if order_id not in orders and not self.payment_order_exists(order_id):
                return order_id

    def _connection_kwargs(self, include_database: bool = True) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "host": self.config.host,
            "port": self.config.port,
            "user": self.config.user,
            "password": self.config.password,
        }
        if include_database:
            kwargs["database"] = self.config.database
        if self.config.ssl_disabled:
            kwargs["ssl_disabled"] = True
        return kwargs

    def _ensure_usage_schema_columns(self, cursor: object) -> None:
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'usage_records'
            """,
            (self.config.database,),
        )
        columns = {str(row[0]) for row in cursor.fetchall()}
        if "mode" not in columns:
            cursor.execute("ALTER TABLE usage_records ADD COLUMN mode VARCHAR(64) NULL")
        if "section_id" not in columns:
            cursor.execute(
                "ALTER TABLE usage_records ADD COLUMN section_id VARCHAR(64) NULL"
            )
        if "source_language" not in columns:
            cursor.execute(
                "ALTER TABLE usage_records ADD COLUMN source_language VARCHAR(128) NULL"
            )
        if "estimated_cost_per_word_usd" not in columns:
            cursor.execute(
                """
                ALTER TABLE usage_records
                ADD COLUMN estimated_cost_per_word_usd DECIMAL(12, 8) NOT NULL DEFAULT 0
                """
            )

    def _ensure_billing_schema_indexes(self, cursor: object) -> None:
        cursor.execute(
            """
            SELECT INDEX_NAME
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'credit_ledger'
            """,
            (self.config.database,),
        )
        indexes = {str(row[0]) for row in cursor.fetchall()}
        if "uq_credit_ledger_job_type" not in indexes:
            cursor.execute(
                """
                ALTER TABLE credit_ledger
                ADD UNIQUE KEY uq_credit_ledger_job_type (job_id, entry_type)
                """
            )
        if "uq_credit_ledger_order_type" not in indexes:
            cursor.execute(
                """
                ALTER TABLE credit_ledger
                ADD UNIQUE KEY uq_credit_ledger_order_type (order_id, entry_type)
                """
            )

    def _acquire_mysql_lock(
        self,
        cursor: object,
        name: str,
        timeout_seconds: int = 10,
    ) -> None:
        cursor.execute("SELECT GET_LOCK(%s, %s)", (mysql_lock_name(name), timeout_seconds))
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Could not acquire billing lock.")
        value = row[0] if isinstance(row, tuple) else next(iter(row.values()))
        if int(value or 0) != 1:
            raise RuntimeError("Could not acquire billing lock.")

    def _release_mysql_lock(self, cursor: object, name: str) -> None:
        try:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (mysql_lock_name(name),))
            cursor.fetchone()
        except Exception:
            pass

    def _insert_document(
        self,
        document_id: str,
        owner_user_id: str,
        owner_email: str,
        owner_auth_provider: str,
        original_filename: str,
        source_format: str,
        target_words_per_section: int,
        metadata: dict[str, object],
        parsed_document: dict[str, object],
        sections: list[dict[str, object]],
    ) -> None:
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO documents (
                  document_id, owner_user_id, owner_email, owner_auth_provider,
                  original_filename, source_format, target_words_per_section,
                  metadata_json, parsed_document_json, sections_json,
                  translations_json, section_translations_json, glossary_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document_id,
                    owner_user_id,
                    owner_email,
                    owner_auth_provider,
                    original_filename,
                    source_format,
                    target_words_per_section,
                    json_text(metadata),
                    json_text(parsed_document),
                    json_text(sections),
                    json_text({}),
                    json_text({}),
                    json_text([]),
                ),
            )
            connection.commit()

    def _document_row(self, document_id: str) -> dict[str, object]:
        self.document_dir(document_id)
        with self.connection() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM documents WHERE document_id = %s", (document_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown document_id: {document_id}")
        return dict(row)

    def _document_json(
        self,
        document_id: str,
        column: str,
        default: object,
    ) -> Any:
        if column not in DOCUMENT_JSON_COLUMNS:
            raise ValueError(f"Unsupported document JSON column: {column}")
        row = self._document_row(document_id)
        return json_value(row.get(column), default)

    def _update_document_json(
        self,
        document_id: str,
        column: str,
        value: object,
    ) -> None:
        if column not in DOCUMENT_JSON_COLUMNS:
            raise ValueError(f"Unsupported document JSON column: {column}")
        self.document_dir(document_id)
        with self.connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"UPDATE documents SET {column} = %s WHERE document_id = %s",
                (json_text(value), document_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown document_id: {document_id}")

    def _job_from_row(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "job_id": str(row.get("job_id", "")),
            "document_id": str(row.get("document_id", "")),
            "owner_user_id": str(row.get("owner_user_id", "")),
            "owner_email": str(row.get("owner_email", "")),
            "owner_auth_provider": str(row.get("owner_auth_provider", "")),
            "type": str(row.get("job_type", "")),
            "status": str(row.get("status", "")),
            "progress": int(row.get("progress", 0) or 0),
            "message": str(row.get("message", "")),
            "payload": json_value(row.get("payload_json"), {}),
            "result": json_value(row.get("result_json"), {}),
            "error": str(row.get("error_text", "")),
            "created_at": iso_from_db(row.get("created_at")),
            "updated_at": iso_from_db(row.get("updated_at")),
        }

    def _usage_from_row(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "usage_id": str(row.get("usage_id", "")),
            "job_id": str(row.get("job_id", "")),
            "document_id": str(row.get("document_id", "")),
            "job_type": str(row.get("job_type", "")),
            "mode": nullable_str(row.get("mode")),
            "section_id": nullable_str(row.get("section_id")),
            "owner_user_id": str(row.get("owner_user_id", "")),
            "owner_email": str(row.get("owner_email", "")),
            "owner_auth_provider": str(row.get("owner_auth_provider", "")),
            "source_language": nullable_str(row.get("source_language")),
            "target_language": nullable_str(row.get("target_language")),
            "document_type": nullable_str(row.get("document_type")),
            "content_form": nullable_str(row.get("content_form")),
            "word_count": int(row.get("word_count", 0) or 0),
            "chunk_count": int(row.get("chunk_count", 0) or 0),
            "chunk_size_words": int(row.get("chunk_size_words", 0) or 0),
            "translated_block_count": int(row.get("translated_block_count", 0) or 0),
            "estimated_input_tokens": int(row.get("estimated_input_tokens", 0) or 0),
            "estimated_output_tokens": int(row.get("estimated_output_tokens", 0) or 0),
            "estimated_prompt_overhead_tokens": int(
                row.get("estimated_prompt_overhead_tokens", 0) or 0
            ),
            "estimated_total_tokens": int(row.get("estimated_total_tokens", 0) or 0),
            "estimated_cost_usd": decimal_to_float(row.get("estimated_cost_usd")),
            "estimated_cost_per_word_usd": decimal_to_float(
                row.get("estimated_cost_per_word_usd")
            ),
            "token_price_per_1m_usd": decimal_to_float(
                row.get("token_price_per_1m_usd")
            ),
            "created_at": iso_from_db(row.get("created_at")),
        }

    def _payment_order_from_row(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "order_id": str(row.get("order_id", "")),
            "owner_user_id": str(row.get("owner_user_id", "")),
            "owner_email": str(row.get("owner_email", "")),
            "owner_auth_provider": str(row.get("owner_auth_provider", "")),
            "package_id": str(row.get("package_id", "")),
            "credits": int(row.get("credits", 0) or 0),
            "amount_cents": int(row.get("amount_cents", 0) or 0),
            "currency": str(row.get("currency", "USD")),
            "provider": str(row.get("provider", "")),
            "status": str(row.get("status", "")),
            "checkout_url": nullable_str(row.get("checkout_url")),
            "external_payment_id": nullable_str(row.get("external_payment_id")),
            "metadata": json_value(row.get("metadata_json"), {}),
            "created_at": iso_from_db(row.get("created_at")),
            "updated_at": iso_from_db(row.get("updated_at")),
        }

    def _credit_entry_from_row(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "entry_id": str(row.get("entry_id", "")),
            "owner_user_id": str(row.get("owner_user_id", "")),
            "owner_email": str(row.get("owner_email", "")),
            "owner_auth_provider": str(row.get("owner_auth_provider", "")),
            "entry_type": str(row.get("entry_type", "")),
            "credit_delta": int(row.get("credit_delta", 0) or 0),
            "credits": int(row.get("credits", 0) or 0),
            "status": str(row.get("status", "")),
            "job_id": nullable_str(row.get("job_id")),
            "document_id": nullable_str(row.get("document_id")),
            "order_id": nullable_str(row.get("order_id")),
            "model_tier": nullable_str(row.get("model_tier")),
            "metadata": json_value(row.get("metadata_json"), {}),
            "created_at": iso_from_db(row.get("created_at")),
            "updated_at": iso_from_db(row.get("updated_at")),
        }


def mysql_config_from_env() -> MySqlConfig:
    return MySqlConfig(
        host=os.environ.get("MYSQL_HOST", "localhost"),
        port=int_env("MYSQL_PORT", 3306),
        database=os.environ.get("MYSQL_DATABASE", "translator_backend"),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        ssl_disabled=bool_env("MYSQL_SSL_DISABLED", False),
        auto_create_database=bool_env("TRANSLATOR_MYSQL_AUTO_CREATE_DATABASE", True),
        auto_init_schema=bool_env("TRANSLATOR_MYSQL_AUTO_INIT_SCHEMA", True),
    )


def int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        return int(raw_value)
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_value(value: object, default: object) -> Any:
    if value is None:
        return json_clone(default)
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return json_clone(default)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return json_clone(default)
    return json_clone(default)


def json_clone(value: object) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def iso_from_db(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
    if value:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
    return utc_now_iso()


def db_datetime_from_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC).replace(microsecond=0, tzinfo=None)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0, tzinfo=None)


def decimal_to_float(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return 0.0
    return float(value)


def nullable_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def mysql_lock_name(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:48]
    return f"billing:{digest}"
