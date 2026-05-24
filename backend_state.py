from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
from typing import Any

from document_adapters import load_document
from document_model import DocumentBlock, ParsedDocument
from document_writers import BlockTranslation, default_translated_document_path, write_translated_document


SUPPORTED_UPLOAD_SUFFIXES = {".txt", ".docx", ".epub"}
DEFAULT_OWNER_USER_ID = "dev-local-user"
DEFAULT_OWNER_EMAIL = "dev@example.local"
DEFAULT_OWNER_AUTH_PROVIDER = "dev"
DEFAULT_SOURCE_LANGUAGE = "English"
DEFAULT_SECTION_TARGET_WORDS = 600
MIN_SECTION_TARGET_WORDS = 100
MAX_SECTION_TARGET_WORDS = 1500
REST_TRANSLATION_CHUNK_WORDS = 1500
DEFAULT_TOKEN_PRICE_PER_1M_USD = 1.0
ESTIMATED_PROMPT_OVERHEAD_TOKENS = 350
TRANSLATION_JOB_TYPES = {"translate_next", "retranslate_last", "translate_rest"}
INLINE_PLACEHOLDER_PATTERN = re.compile(r"\[\[INLINE_\d{4}\]\]")


class InsufficientCreditsError(ValueError):
    def __init__(self, required_credits: int, available_credits: int) -> None:
        self.required_credits = max(0, int(required_credits))
        self.available_credits = max(0, int(available_credits))
        super().__init__(
            f"Insufficient credits: required {self.required_credits}, "
            f"available {self.available_credits}."
        )


@dataclass(frozen=True)
class DocumentSection:
    section_id: str
    index: int
    block_ids: list[str]
    word_count: int
    preview: str

    def to_dict(self) -> dict[str, object]:
        return {
            "section_id": self.section_id,
            "index": self.index,
            "block_ids": self.block_ids,
            "word_count": self.word_count,
            "block_count": len(self.block_ids),
            "preview": self.preview,
        }


class LocalDocumentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.documents_root = root / "documents"
        self.documents_root.mkdir(parents=True, exist_ok=True)
        self._billing_lock = threading.RLock()

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

        write_json(self.metadata_path(document_id), metadata)
        write_json(self.parsed_document_path(document_id), parsed_document.to_dict())
        write_json(
            self.sections_path(document_id),
            [section.to_dict() for section in sections],
        )
        write_json(self.translations_path(document_id), {})
        write_json(self.section_translations_path(document_id), {})
        write_json(self.glossary_path(document_id), [])
        return self.document_summary(document_id)

    def document_summary(self, document_id: str) -> dict[str, object]:
        metadata = self.load_metadata(document_id)
        parsed_document = self.load_parsed_document(document_id)
        sections = self.load_sections(document_id)
        translations = self.load_translations(document_id)
        cursor = translation_cursor(sections, translations)
        next_section = next_section_id(sections, cursor)
        metadata.setdefault("source_language", DEFAULT_SOURCE_LANGUAGE)
        return {
            **metadata,
            "translatable_block_count": len(translatable_blocks(parsed_document)),
            "section_count": len(sections),
            "translation_cursor": cursor,
            "next_section_id": next_section,
            "last_translated_section_id": last_translated_section_id(sections, cursor),
            "next_section_estimate": next_section_estimate(sections, translations, cursor),
            "remaining_estimate": remaining_translation_estimate(sections, translations),
        }

    def sections_response(self, document_id: str) -> dict[str, object]:
        sections = self.load_sections(document_id)
        translations = self.load_translations(document_id)
        cursor = translation_cursor(sections, translations)
        return {
            "document_id": document_id,
            "section_count": len(sections),
            "translation_cursor": cursor,
            "next_section_id": next_section_id(sections, cursor),
            "last_translated_section_id": last_translated_section_id(sections, cursor),
            "token_price_per_1m_usd": token_price_per_1m_usd(),
            "next_section_estimate": next_section_estimate(sections, translations, cursor),
            "remaining_estimate": remaining_translation_estimate(sections, translations),
            "sections": [
                section_response(section, translations, cursor)
                for section in sections
            ],
        }

    def preview_response(
        self,
        document_id: str,
        section_id: str | None = None,
        offset: int = 0,
        limit: int = 40,
    ) -> dict[str, object]:
        parsed_document = self.load_parsed_document(document_id)
        sections = self.load_sections(document_id)
        translations = self.load_translations(document_id)
        section_by_block_id = block_to_section_map(sections)
        selected_block_ids = None
        if section_id:
            selected_section = find_section(sections, section_id)
            if selected_section is None:
                raise KeyError(f"Unknown section_id: {section_id}")
            selected_block_ids = set(section_block_ids(selected_section))

        offset = max(0, int(offset))
        limit = min(100, max(1, int(limit)))

        blocks = []
        for block in translatable_blocks(parsed_document):
            if selected_block_ids is not None and block.block_id not in selected_block_ids:
                continue
            translated_text = translation_text(translations.get(block.block_id))
            is_translated = bool(translated_text)
            source_display_text = preview_display_text(block.text, block.metadata)
            target_display_text = preview_display_text(
                translated_text if is_translated else block.text,
                block.metadata,
            )
            blocks.append(
                {
                    "block_id": block.block_id,
                    "section_id": section_by_block_id.get(block.block_id),
                    "type": block.type,
                    "level": block.level,
                    "source_text": source_display_text,
                    "display_text": target_display_text,
                    "status": "translated" if is_translated else "source",
                }
            )

        total_blocks = len(blocks)
        paged_blocks = blocks[offset : offset + limit]
        page = (offset // limit) + 1 if total_blocks else 0
        page_count = ((total_blocks - 1) // limit) + 1 if total_blocks else 0

        return {
            "document_id": document_id,
            "section_id": section_id,
            "offset": offset,
            "limit": limit,
            "total_blocks": total_blocks,
            "page": page,
            "page_count": page_count,
            "has_previous": offset > 0,
            "has_next": offset + limit < total_blocks,
            "blocks": paged_blocks,
        }

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
        write_json(self.latest_export_path(document_id), export_metadata)
        return export_metadata

    def latest_export_file(self, document_id: str) -> Path:
        metadata = read_json(self.latest_export_path(document_id))
        output_path = Path(str(metadata.get("output_path", "")))
        if not output_path.exists():
            raise FileNotFoundError(f"No export file found for document {document_id}.")
        return output_path

    def record_usage(
        self,
        job: dict[str, object],
        usage: dict[str, object],
    ) -> dict[str, object]:
        existing_record = self.usage_record_for_job(str(job.get("job_id", "")))
        if existing_record is not None:
            return existing_record

        records = self.load_usage_records()
        usage_id = self._new_usage_id(records)
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
        records[usage_id] = record
        self.save_usage_records(records)
        return record

    def usage_record_for_job(self, job_id: str) -> dict[str, object] | None:
        if not job_id:
            return None
        for record in self.load_usage_records().values():
            if record.get("job_id") == job_id:
                return record
        return None

    def load_usage_records(self) -> dict[str, dict[str, object]]:
        path = self.usage_path()
        if not path.exists():
            return {}
        raw_records = read_json(path)
        if not isinstance(raw_records, dict):
            return {}
        return {
            str(usage_id): dict(record)
            for usage_id, record in raw_records.items()
            if isinstance(record, dict)
        }

    def save_usage_records(self, records: dict[str, dict[str, object]]) -> None:
        write_json(self.usage_path(), records)

    def usage_summary(
        self,
        owner_user_id: str,
        document_id: str | None = None,
    ) -> dict[str, object]:
        records = [
            record
            for record in self.load_usage_records().values()
            if record.get("owner_user_id") == owner_user_id
            and (document_id is None or record.get("document_id") == document_id)
        ]
        records.sort(key=lambda record: str(record.get("created_at", "")), reverse=True)
        return {
            "owner_user_id": owner_user_id,
            "document_id": document_id,
            "record_count": len(records),
            "total_word_count": sum_int_field(records, "word_count"),
            "total_chunk_count": sum_int_field(records, "chunk_count"),
            "estimated_input_tokens": sum_int_field(records, "estimated_input_tokens"),
            "estimated_output_tokens": sum_int_field(records, "estimated_output_tokens"),
            "estimated_prompt_overhead_tokens": sum_int_field(
                records,
                "estimated_prompt_overhead_tokens",
            ),
            "estimated_total_tokens": sum_int_field(records, "estimated_total_tokens"),
            "estimated_cost_usd": round(sum_float_field(records, "estimated_cost_usd"), 6),
            "records": records,
        }

    def load_credit_ledger(self) -> dict[str, dict[str, object]]:
        path = self.credit_ledger_path()
        if not path.exists():
            return {}
        raw_entries = read_json(path)
        if not isinstance(raw_entries, dict):
            return {}
        return {
            str(entry_id): dict(entry)
            for entry_id, entry in raw_entries.items()
            if isinstance(entry, dict)
        }

    def save_credit_ledger(self, entries: dict[str, dict[str, object]]) -> None:
        write_json(self.credit_ledger_path(), entries)

    def credit_ledger_for_owner(self, owner_user_id: str) -> list[dict[str, object]]:
        entries = [
            dict(entry)
            for entry in self.load_credit_ledger().values()
            if entry.get("owner_user_id") == owner_user_id
        ]
        entries.sort(key=lambda entry: str(entry.get("created_at", "")), reverse=True)
        return entries

    def credit_balance(self, owner_user_id: str) -> int:
        return sum(
            int(entry.get("credit_delta", 0) or 0)
            for entry in self.credit_ledger_for_owner(owner_user_id)
        )

    def ensure_signup_credit_grant(
        self,
        owner_user_id: str,
        owner_email: str,
        owner_auth_provider: str,
        credits: int,
    ) -> dict[str, object] | None:
        with self._billing_lock:
            credit_count = max(0, int(credits))
            if credit_count <= 0:
                return None
            for entry in self.credit_ledger_for_owner(owner_user_id):
                if entry.get("entry_type") == "signup_grant":
                    return entry
            return self.create_credit_ledger_entry(
                owner_user_id=owner_user_id,
                owner_email=owner_email,
                owner_auth_provider=owner_auth_provider,
                entry_type="signup_grant",
                credit_delta=credit_count,
                credits=credit_count,
                status="posted",
                metadata={"reason": "one_time_signup_credit"},
            )

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
        with self._billing_lock:
            entries = self.load_credit_ledger()
            entry_id = self._new_credit_entry_id(entries)
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
            entries[entry_id] = entry
            self.save_credit_ledger(entries)
            return entry

    def create_payment_order(
        self,
        owner_user_id: str,
        owner_email: str,
        owner_auth_provider: str,
        package: dict[str, object],
        provider: str,
    ) -> dict[str, object]:
        with self._billing_lock:
            orders = self.load_payment_orders()
            order_id = self._new_payment_order_id(orders)
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
            orders[order_id] = order
            self.save_payment_orders(orders)
            return order

    def update_payment_order_checkout(
        self,
        order_id: str,
        checkout_url: str,
        external_payment_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with self._billing_lock:
            orders = self.load_payment_orders()
            order = orders.get(order_id)
            if order is None:
                raise KeyError(f"Unknown order_id: {order_id}")
            merged_metadata = dict(order.get("metadata", {}))
            if metadata:
                merged_metadata.update(metadata)
            order.update(
                {
                    "checkout_url": checkout_url,
                    "metadata": merged_metadata,
                    "updated_at": utc_now_iso(),
                }
            )
            if external_payment_id:
                order["external_payment_id"] = external_payment_id
            orders[order_id] = order
            self.save_payment_orders(orders)
            return dict(order)

    def load_payment_orders(self) -> dict[str, dict[str, object]]:
        path = self.payment_orders_path()
        if not path.exists():
            return {}
        raw_orders = read_json(path)
        if not isinstance(raw_orders, dict):
            return {}
        return {
            str(order_id): dict(order)
            for order_id, order in raw_orders.items()
            if isinstance(order, dict)
        }

    def save_payment_orders(self, orders: dict[str, dict[str, object]]) -> None:
        write_json(self.payment_orders_path(), orders)

    def load_payment_order(self, order_id: str) -> dict[str, object]:
        orders = self.load_payment_orders()
        order = orders.get(order_id)
        if order is None:
            raise KeyError(f"Unknown order_id: {order_id}")
        return dict(order)

    def payment_order_for_external_id(self, external_payment_id: str) -> dict[str, object]:
        for order in self.load_payment_orders().values():
            if order.get("external_payment_id") == external_payment_id:
                return dict(order)
            metadata = order.get("metadata", {})
            if isinstance(metadata, dict) and external_payment_id in {
                metadata.get("razorpay_order_id"),
            }:
                return dict(order)
        raise KeyError(f"Unknown external_payment_id: {external_payment_id}")

    def complete_payment_order(
        self,
        order_id: str,
        external_payment_id: str,
    ) -> dict[str, object]:
        with self._billing_lock:
            orders = self.load_payment_orders()
            order = orders.get(order_id)
            if order is None:
                raise KeyError(f"Unknown order_id: {order_id}")
            if order.get("status") == "paid":
                return dict(order)
            if order.get("status") != "pending":
                raise ValueError("Payment order is not payable.")

            now = utc_now_iso()
            order["status"] = "paid"
            order["external_payment_id"] = external_payment_id
            order["updated_at"] = now
            orders[order_id] = order
            self.save_payment_orders(orders)

            existing_purchase = next(
                (
                    entry
                    for entry in self.load_credit_ledger().values()
                    if entry.get("order_id") == order_id
                    and entry.get("entry_type") == "purchase"
                ),
                None,
            )
            if existing_purchase is None:
                self.create_credit_ledger_entry(
                    owner_user_id=str(order.get("owner_user_id", "")),
                    owner_email=str(order.get("owner_email", "")),
                    owner_auth_provider=str(order.get("owner_auth_provider", "")),
                    entry_type="purchase",
                    credit_delta=int(order.get("credits", 0) or 0),
                    credits=int(order.get("credits", 0) or 0),
                    status="posted",
                    order_id=order_id,
                    metadata={
                        "provider": order.get("provider"),
                        "external_payment_id": external_payment_id,
                        "amount_cents": order.get("amount_cents"),
                        "currency": order.get("currency"),
                    },
                )
            return dict(order)

    def credit_reservation_for_job(self, job_id: str) -> dict[str, object] | None:
        for entry in self.load_credit_ledger().values():
            if entry.get("job_id") == job_id and entry.get("entry_type") == "reserve":
                return dict(entry)
        return None

    def reserve_credits_for_job(
        self,
        job: dict[str, object],
        credits: int,
        model_tier: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        with self._billing_lock:
            credit_count = max(0, int(credits))
            if credit_count <= 0:
                return None
            existing = self.credit_reservation_for_job(str(job.get("job_id", "")))
            if existing is not None:
                return existing
            owner_user_id = str(job.get("owner_user_id", ""))
            available = self.credit_balance(owner_user_id)
            if available < credit_count:
                raise InsufficientCreditsError(credit_count, available)
            return self.create_credit_ledger_entry(
                owner_user_id=owner_user_id,
                owner_email=str(job.get("owner_email", "")),
                owner_auth_provider=str(job.get("owner_auth_provider", "")),
                entry_type="reserve",
                credit_delta=-credit_count,
                credits=credit_count,
                status="active",
                job_id=str(job.get("job_id", "")),
                document_id=str(job.get("document_id", "")),
                model_tier=model_tier,
                metadata=metadata or {},
            )

    def capture_credit_reservation(
        self,
        job: dict[str, object],
        actual_credits: int,
        usage_record: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        with self._billing_lock:
            reservation = self.credit_reservation_for_job(str(job.get("job_id", "")))
            if reservation is None or reservation.get("status") != "active":
                return reservation
            entries = self.load_credit_ledger()
            entry_id = str(reservation.get("entry_id", ""))
            if entry_id not in entries:
                return None
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
            entries[entry_id].update(
                {
                    "status": "captured",
                    "metadata": metadata,
                    "updated_at": utc_now_iso(),
                }
            )
            self.save_credit_ledger(entries)
            self.create_credit_ledger_entry(
                owner_user_id=str(job.get("owner_user_id", "")),
                owner_email=str(job.get("owner_email", "")),
                owner_auth_provider=str(job.get("owner_auth_provider", "")),
                entry_type="charge",
                credit_delta=0,
                credits=charged_credits,
                status="posted",
                job_id=str(job.get("job_id", "")),
                document_id=str(job.get("document_id", "")),
                model_tier=str(reservation.get("model_tier", "")),
                metadata={"reservation_id": entry_id, "usage_id": metadata.get("usage_id")},
            )
            if refunded_credits:
                self.create_credit_ledger_entry(
                    owner_user_id=str(job.get("owner_user_id", "")),
                    owner_email=str(job.get("owner_email", "")),
                    owner_auth_provider=str(job.get("owner_auth_provider", "")),
                    entry_type="refund",
                    credit_delta=refunded_credits,
                    credits=refunded_credits,
                    status="posted",
                    job_id=str(job.get("job_id", "")),
                    document_id=str(job.get("document_id", "")),
                    model_tier=str(reservation.get("model_tier", "")),
                    metadata={
                        "reservation_id": entry_id,
                        "reason": "unused_reserved_credits",
                    },
                )
            return entries[entry_id]

    def release_credit_reservation(
        self,
        job: dict[str, object],
        reason: str,
    ) -> dict[str, object] | None:
        with self._billing_lock:
            reservation = self.credit_reservation_for_job(str(job.get("job_id", "")))
            if reservation is None or reservation.get("status") != "active":
                return reservation
            entries = self.load_credit_ledger()
            entry_id = str(reservation.get("entry_id", ""))
            if entry_id not in entries:
                return None
            reserved_credits = int(reservation.get("credits", 0) or 0)
            metadata = dict(reservation.get("metadata", {}))
            metadata.update({"released_credits": reserved_credits, "release_reason": reason})
            entries[entry_id].update(
                {
                    "status": "released",
                    "metadata": metadata,
                    "updated_at": utc_now_iso(),
                }
            )
            self.save_credit_ledger(entries)
            if reserved_credits:
                self.create_credit_ledger_entry(
                    owner_user_id=str(job.get("owner_user_id", "")),
                    owner_email=str(job.get("owner_email", "")),
                    owner_auth_provider=str(job.get("owner_auth_provider", "")),
                    entry_type="refund",
                    credit_delta=reserved_credits,
                    credits=reserved_credits,
                    status="posted",
                    job_id=str(job.get("job_id", "")),
                    document_id=str(job.get("document_id", "")),
                    model_tier=str(reservation.get("model_tier", "")),
                    metadata={"reservation_id": entry_id, "reason": reason},
                )
            return entries[entry_id]

    def document_metadata_for_owner(self, owner_user_id: str) -> list[dict[str, object]]:
        if not self.documents_root.exists():
            return []
        documents: list[dict[str, object]] = []
        for document_dir in self.documents_root.iterdir():
            if not document_dir.is_dir():
                continue
            metadata_path = document_dir / "metadata.json"
            if not metadata_path.exists():
                continue
            try:
                metadata = read_json(metadata_path)
            except (KeyError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            if metadata.get("owner_user_id") == owner_user_id:
                documents.append(dict(metadata))
        return documents

    def upload_count_since(self, owner_user_id: str, since: datetime) -> int:
        return sum(
            1
            for metadata in self.document_metadata_for_owner(owner_user_id)
            if datetime_from_iso(str(metadata.get("created_at", ""))) >= since
        )

    def active_translation_jobs_for_user(
        self,
        owner_user_id: str,
    ) -> list[dict[str, object]]:
        jobs = [
            job
            for job in self.load_jobs().values()
            if job.get("owner_user_id") == owner_user_id
            and job.get("type") in TRANSLATION_JOB_TYPES
            and job.get("status") in {"queued", "running"}
        ]
        jobs.sort(key=lambda job: str(job.get("created_at", "")))
        return jobs

    def load_metadata(self, document_id: str) -> dict[str, object]:
        return read_json(self.metadata_path(document_id))

    def load_parsed_document(self, document_id: str) -> ParsedDocument:
        return parsed_document_from_dict(read_json(self.parsed_document_path(document_id)))

    def load_sections(self, document_id: str) -> list[dict[str, object]]:
        raw_sections = read_json(self.sections_path(document_id))
        if not isinstance(raw_sections, list):
            raise ValueError(f"Invalid section data for document {document_id}.")
        return [dict(section) for section in raw_sections if isinstance(section, dict)]

    def load_translations(self, document_id: str) -> dict[str, BlockTranslation]:
        raw_translations = read_json(self.translations_path(document_id))
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
        write_json(self.translations_path(document_id), translations)

    def load_section_translations(self, document_id: str) -> dict[str, dict[str, object]]:
        path = self.section_translations_path(document_id)
        if not path.exists():
            return {}
        raw_items = read_json(path)
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
        write_json(self.section_translations_path(document_id), section_translations)

    def load_glossary(self, document_id: str) -> list[dict[str, object]]:
        path = self.glossary_path(document_id)
        if not path.exists():
            return []
        raw_glossary = read_json(path)
        if not isinstance(raw_glossary, list):
            return []
        return [dict(entry) for entry in raw_glossary if isinstance(entry, dict)]

    def save_glossary(
        self,
        document_id: str,
        glossary: list[dict[str, object]],
    ) -> None:
        write_json(self.glossary_path(document_id), glossary)

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
        jobs = self.load_jobs()
        job_id = self._new_job_id(jobs)
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
        jobs[job_id] = job
        self.save_jobs(jobs)
        return job

    def load_jobs(self) -> dict[str, dict[str, object]]:
        path = self.jobs_path()
        if not path.exists():
            return {}
        raw_jobs = read_json(path)
        if not isinstance(raw_jobs, dict):
            return {}
        return {
            str(job_id): dict(job)
            for job_id, job in raw_jobs.items()
            if isinstance(job, dict)
        }

    def save_jobs(self, jobs: dict[str, dict[str, object]]) -> None:
        write_json(self.jobs_path(), jobs)

    def load_job(self, job_id: str) -> dict[str, object]:
        if not re.fullmatch(r"job_[a-f0-9]{12}", job_id):
            raise KeyError(f"Invalid job_id: {job_id}")
        jobs = self.load_jobs()
        job = jobs.get(job_id)
        if job is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        return job

    def update_job(self, job_id: str, **updates: object) -> dict[str, object]:
        jobs = self.load_jobs()
        if job_id not in jobs:
            raise KeyError(f"Unknown job_id: {job_id}")
        jobs[job_id].update(updates)
        jobs[job_id]["updated_at"] = utc_now_iso()
        self.save_jobs(jobs)
        return jobs[job_id]

    def active_job_for_document(
        self,
        document_id: str,
        job_type: str,
    ) -> dict[str, object] | None:
        for job in self.load_jobs().values():
            if (
                job.get("document_id") == document_id
                and job.get("type") == job_type
                and job.get("status") in {"queued", "running"}
            ):
                return job
        return None

    def job_for_idempotency_key(
        self,
        document_id: str,
        job_type: str,
        idempotency_key: str | None,
    ) -> dict[str, object] | None:
        key = (idempotency_key or "").strip()
        if not key:
            return None
        for job in reversed(list(self.load_jobs().values())):
            payload = job.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if (
                job.get("document_id") == document_id
                and job.get("type") == job_type
                and payload.get("idempotency_key") == key
            ):
                return job
        return None

    def document_dir(self, document_id: str) -> Path:
        if not re.fullmatch(r"doc_[a-f0-9]{12}", document_id):
            raise KeyError(f"Invalid document_id: {document_id}")
        return self.documents_root / document_id

    def metadata_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "metadata.json"

    def parsed_document_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "parsed_document.json"

    def sections_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "sections.json"

    def translations_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "translations.json"

    def section_translations_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "section_translations.json"

    def glossary_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "glossary.json"

    def latest_export_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "latest_export.json"

    def jobs_path(self) -> Path:
        return self.root / "jobs.json"

    def usage_path(self) -> Path:
        return self.root / "usage.json"

    def credit_ledger_path(self) -> Path:
        return self.root / "credit_ledger.json"

    def payment_orders_path(self) -> Path:
        return self.root / "payment_orders.json"

    def _new_document_id(self) -> str:
        while True:
            document_id = f"doc_{secrets.token_hex(6)}"
            if not self.document_dir(document_id).exists():
                return document_id

    def _new_job_id(self, jobs: dict[str, dict[str, object]]) -> str:
        while True:
            job_id = f"job_{secrets.token_hex(6)}"
            if job_id not in jobs:
                return job_id

    def _new_usage_id(self, records: dict[str, dict[str, object]]) -> str:
        while True:
            usage_id = f"usage_{secrets.token_hex(6)}"
            if usage_id not in records:
                return usage_id

    def _new_credit_entry_id(self, entries: dict[str, dict[str, object]]) -> str:
        while True:
            entry_id = f"cred_{secrets.token_hex(6)}"
            if entry_id not in entries:
                return entry_id

    def _new_payment_order_id(self, orders: dict[str, dict[str, object]]) -> str:
        while True:
            order_id = f"pay_{secrets.token_hex(6)}"
            if order_id not in orders:
                return order_id


def build_document_sections(
    parsed_document: ParsedDocument,
    target_words_per_section: int = DEFAULT_SECTION_TARGET_WORDS,
) -> list[DocumentSection]:
    target_words = clamp_section_target(target_words_per_section)
    sections: list[DocumentSection] = []
    current_blocks: list[DocumentBlock] = []
    current_word_count = 0

    for block in translatable_blocks(parsed_document):
        block_word_count = count_words(block.text)
        if current_blocks and current_word_count + block_word_count > target_words:
            sections.append(make_section(len(sections) + 1, current_blocks))
            current_blocks = []
            current_word_count = 0

        current_blocks.append(block)
        current_word_count += block_word_count

    if current_blocks:
        sections.append(make_section(len(sections) + 1, current_blocks))

    return sections


def make_section(index: int, blocks: list[DocumentBlock]) -> DocumentSection:
    source_text = "\n\n".join(
        preview_display_text(block.text.strip(), block.metadata)
        for block in blocks
        if block.text.strip()
    )
    return DocumentSection(
        section_id=f"sec_{index:04d}",
        index=index,
        block_ids=[block.block_id for block in blocks],
        word_count=sum(count_words(block.text) for block in blocks),
        preview=compact_preview(source_text),
    )


def translatable_blocks(parsed_document: ParsedDocument) -> list[DocumentBlock]:
    return [
        block
        for block in parsed_document.blocks
        if block.translate and block.text.strip()
    ]


def section_response(
    section: dict[str, object],
    translations: dict[str, BlockTranslation],
    cursor: int,
) -> dict[str, object]:
    index = int(section.get("index", 0))
    status = section_status(section, translations)
    can_translate = index in {cursor, cursor + 1}
    action = None
    if can_translate and index > 0:
        if status == "translated":
            action = "retranslate_last" if index == cursor else None
        elif index == cursor + 1:
            action = "translate_next"
        else:
            action = "review"

    block_ids = section_block_ids(section)
    translated_block_count = sum(1 for block_id in block_ids if block_id in translations)
    word_count = int(section.get("word_count", 0))
    estimate = translation_cost_estimate(
        word_count=word_count,
        chunk_count=1,
        chunk_size_words=int(section.get("word_count", 0)) or DEFAULT_SECTION_TARGET_WORDS,
    )
    return {
        "section_id": section.get("section_id"),
        "index": index,
        "status": "locked" if index > cursor + 1 and status == "not_translated" else status,
        "can_translate": can_translate,
        "action": action,
        "word_count": word_count,
        "block_count": len(block_ids),
        "translated_block_count": translated_block_count,
        "estimated_input_tokens": estimate["estimated_input_tokens"],
        "estimated_output_tokens": estimate["estimated_output_tokens"],
        "estimated_prompt_overhead_tokens": estimate["estimated_prompt_overhead_tokens"],
        "estimated_total_tokens": estimate["estimated_total_tokens"],
        "estimated_cost_usd": estimate["estimated_cost_usd"],
        "preview": preview_display_text(str(section.get("preview", ""))),
    }


def next_section_estimate(
    sections: list[dict[str, object]],
    translations: dict[str, BlockTranslation],
    cursor: int,
) -> dict[str, object] | None:
    target_section_id = next_section_id(sections, cursor)
    if target_section_id is None:
        return None
    section = find_section(sections, target_section_id)
    if section is None:
        return None
    return {
        "section_id": target_section_id,
        **translation_cost_estimate(
            word_count=int(section.get("word_count", 0)),
            chunk_count=1,
            chunk_size_words=int(section.get("word_count", 0)) or DEFAULT_SECTION_TARGET_WORDS,
        ),
    }


def remaining_translation_estimate(
    sections: list[dict[str, object]],
    translations: dict[str, BlockTranslation],
) -> dict[str, object]:
    remaining_sections = [
        section
        for section in sorted(sections, key=lambda item: int(item.get("index", 0)))
        if section_status(section, translations) != "translated"
    ]
    remaining_word_counts = [
        int(section.get("word_count", 0))
        for section in remaining_sections
        if int(section.get("word_count", 0)) > 0
    ]
    remaining_block_count = sum(
        len(section_block_ids(section))
        - sum(1 for block_id in section_block_ids(section) if block_id in translations)
        for section in remaining_sections
    )
    remaining_word_count = sum(remaining_word_counts)
    bulk_chunk_count = estimate_bulk_chunk_count(
        remaining_word_counts,
        target_words=REST_TRANSLATION_CHUNK_WORDS,
    )
    estimate = translation_cost_estimate(
        word_count=remaining_word_count,
        chunk_count=bulk_chunk_count,
        chunk_size_words=REST_TRANSLATION_CHUNK_WORDS,
    )
    return {
        "mode": "translate_rest",
        "chunk_size_words": REST_TRANSLATION_CHUNK_WORDS,
        "remaining_section_count": len(remaining_sections),
        "remaining_block_count": max(0, remaining_block_count),
        **estimate,
    }


def estimate_bulk_chunk_count(word_counts: list[int], target_words: int) -> int:
    if not word_counts:
        return 0
    target = max(1, int(target_words))
    chunk_count = 0
    current_words = 0
    for word_count in word_counts:
        words = max(0, int(word_count))
        if words == 0:
            continue
        if current_words and current_words + words > target:
            chunk_count += 1
            current_words = 0
        current_words += words
    if current_words:
        chunk_count += 1
    return chunk_count


def translation_cost_estimate(
    word_count: int,
    chunk_count: int,
    chunk_size_words: int,
) -> dict[str, object]:
    words = max(0, int(word_count))
    chunks = max(0, int(chunk_count))
    estimated_prompt_overhead_tokens = chunks * ESTIMATED_PROMPT_OVERHEAD_TOKENS
    estimated_input_tokens = (
        estimate_tokens(words) + estimated_prompt_overhead_tokens if words else 0
    )
    estimated_output_tokens = estimate_tokens(words) if words else 0
    estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
    cost = estimate_cost_usd(estimated_total_tokens)
    return {
        "word_count": words,
        "chunk_count": chunks,
        "chunk_size_words": int(chunk_size_words),
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_prompt_overhead_tokens": estimated_prompt_overhead_tokens,
        "estimated_total_tokens": estimated_total_tokens,
        "estimated_cost_usd": cost,
        "estimated_cost_per_word_usd": round(cost / words, 8) if words else 0.0,
    }


def normalized_usage_fields(usage: dict[str, object]) -> dict[str, object]:
    word_count = int(usage.get("word_count", 0) or 0)
    estimated_cost_usd = float(usage.get("estimated_cost_usd", 0.0) or 0.0)
    return {
        "mode": usage.get("mode"),
        "section_id": usage.get("section_id"),
        "word_count": word_count,
        "chunk_count": int(usage.get("chunk_count", 0) or 0),
        "chunk_size_words": int(usage.get("chunk_size_words", 0) or 0),
        "translated_block_count": int(usage.get("translated_block_count", 0) or 0),
        "estimated_input_tokens": int(usage.get("estimated_input_tokens", 0) or 0),
        "estimated_output_tokens": int(usage.get("estimated_output_tokens", 0) or 0),
        "estimated_prompt_overhead_tokens": int(
            usage.get("estimated_prompt_overhead_tokens", 0) or 0
        ),
        "estimated_total_tokens": int(usage.get("estimated_total_tokens", 0) or 0),
        "estimated_cost_usd": round(estimated_cost_usd, 6),
        "estimated_cost_per_word_usd": round(estimated_cost_usd / word_count, 8)
        if word_count
        else 0.0,
        "token_price_per_1m_usd": token_price_per_1m_usd(),
    }


def sum_int_field(records: list[dict[str, object]], field: str) -> int:
    return sum(int(record.get(field, 0) or 0) for record in records)


def sum_float_field(records: list[dict[str, object]], field: str) -> float:
    return sum(float(record.get(field, 0.0) or 0.0) for record in records)


def section_status(
    section: dict[str, object],
    translations: dict[str, BlockTranslation],
) -> str:
    block_ids = section_block_ids(section)
    if not block_ids:
        return "empty"
    translated_count = sum(1 for block_id in block_ids if block_id in translations)
    if translated_count == len(block_ids):
        return "translated"
    if translated_count:
        return "partial"
    return "not_translated"


def translation_cursor(
    sections: list[dict[str, object]],
    translations: dict[str, BlockTranslation],
) -> int:
    cursor = 0
    for section in sorted(sections, key=lambda item: int(item.get("index", 0))):
        if section_status(section, translations) != "translated":
            break
        cursor = int(section.get("index", cursor))
    return cursor


def next_section_id(sections: list[dict[str, object]], cursor: int) -> str | None:
    next_index = cursor + 1
    for section in sections:
        if int(section.get("index", 0)) == next_index:
            return str(section.get("section_id", ""))
    return None


def last_translated_section_id(
    sections: list[dict[str, object]],
    cursor: int,
) -> str | None:
    if cursor < 1:
        return None
    for section in sections:
        if int(section.get("index", 0)) == cursor:
            return str(section.get("section_id", ""))
    return None


def section_block_ids(section: dict[str, object]) -> list[str]:
    raw_block_ids = section.get("block_ids", [])
    if not isinstance(raw_block_ids, list):
        return []
    return [str(block_id) for block_id in raw_block_ids if str(block_id).strip()]


def find_section(
    sections: list[dict[str, object]],
    section_id: str,
) -> dict[str, object] | None:
    for section in sections:
        if section.get("section_id") == section_id:
            return section
    return None


def block_to_section_map(sections: list[dict[str, object]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for section in sections:
        section_id = str(section.get("section_id", ""))
        for block_id in section_block_ids(section):
            mapping[block_id] = section_id
    return mapping


def parsed_document_from_dict(data: dict[str, Any]) -> ParsedDocument:
    raw_blocks = data.get("blocks", [])
    blocks = [
        DocumentBlock(
            block_id=str(block.get("block_id", "")),
            type=str(block.get("type", "paragraph")),
            text=str(block.get("text", "")),
            translate=bool(block.get("translate", True)),
            level=block.get("level") if isinstance(block.get("level"), int) else None,
            metadata=dict(block.get("metadata", {}))
            if isinstance(block.get("metadata"), dict)
            else {},
        )
        for block in raw_blocks
        if isinstance(block, dict)
    ]
    return ParsedDocument(
        source_path=str(data.get("source_path") or ""),
        source_format=str(data.get("source_format") or "txt"),
        blocks=blocks,
    )


def translation_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        translated_text = value.get("translated_text", "")
        return str(translated_text).strip()
    return ""


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def compact_preview(text: str, max_length: int = 220) -> str:
    preview = re.sub(r"\s+", " ", text).strip()
    if len(preview) <= max_length:
        return preview
    return preview[: max_length - 3].rstrip() + "..."


def preview_display_text(text: str, metadata: dict[str, Any] | None = None) -> str:
    if not INLINE_PLACEHOLDER_PATTERN.search(text or ""):
        return text

    placeholder_items = {}
    raw_placeholders = (metadata or {}).get("inline_placeholders")
    if isinstance(raw_placeholders, list):
        for item in raw_placeholders:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token", ""))
            if not INLINE_PLACEHOLDER_PATTERN.fullmatch(token):
                continue
            placeholder_items[token] = {
                "kind": str(item.get("kind", "")),
                "display_text": str(item.get("display_text", "")),
            }

    def replacement(match: re.Match[str]) -> str:
        item = placeholder_items.get(match.group(0), {})
        if item.get("kind") == "self":
            return str(item.get("display_text", ""))
        return ""

    return INLINE_PLACEHOLDER_PATTERN.sub(replacement, text)


def estimate_tokens(word_count: int) -> int:
    return max(1, int(round(word_count * 1.35)))


def estimate_cost_usd(token_count: int) -> float:
    return round((token_count / 1_000_000) * token_price_per_1m_usd(), 6)


def token_price_per_1m_usd() -> float:
    raw_value = os.getenv(
        "TRANSLATOR_TOKEN_PRICE_PER_1M_USD",
        str(DEFAULT_TOKEN_PRICE_PER_1M_USD),
    )
    try:
        value = float(raw_value)
    except ValueError:
        value = DEFAULT_TOKEN_PRICE_PER_1M_USD
    return max(0.0, value)


def clamp_section_target(value: int) -> int:
    return min(MAX_SECTION_TARGET_WORDS, max(MIN_SECTION_TARGET_WORDS, int(value)))


def normalize_language_name(value: object, default: str = DEFAULT_SOURCE_LANGUAGE) -> str:
    normalized = str(value or "").strip()
    return normalized or default


def read_json(path: Path) -> Any:
    if not path.exists():
        raise KeyError(f"Missing backend artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def datetime_from_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
