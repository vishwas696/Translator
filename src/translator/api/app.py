from __future__ import annotations

from datetime import UTC, datetime, time
import os
from pathlib import Path
import shutil
import tempfile

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv()

from translator.billing.credits import (
    compact_ledger_entry,
    credit_package_by_id,
    credit_packages,
    credits_per_1k_tokens,
    credits_for_usage,
    model_tier_by_id,
    public_model_tiers,
    quote_from_estimate,
    signup_credits,
)
from translator.api.auth import AuthenticatedUser, get_current_user
from translator.billing.razorpay import (
    RazorpayConfigurationError,
    RazorpayIntegrationError,
    RazorpayWebhookPayloadError,
    RazorpayWebhookSignatureError,
    construct_razorpay_webhook_event,
    create_razorpay_order,
    razorpay_checkout_key_id,
    razorpay_event_id,
    razorpay_event_type,
    razorpay_orders_configured,
    razorpay_payment_entity,
    razorpay_payment_notes,
    razorpay_payments_configured,
    razorpay_webhook_configured,
)
from translator.storage.local import (
    DEFAULT_SECTION_TARGET_WORDS,
    DEFAULT_SOURCE_LANGUAGE,
    InsufficientCreditsError,
    LocalDocumentStore,
    SUPPORTED_UPLOAD_SUFFIXES,
    find_section,
    normalize_language_name,
    translation_cost_estimate,
)
from translator.services.translation_jobs import (
    AgentSectionTranslator,
    DevFakeSectionTranslator,
    NoNextSectionError,
    TranslateNextSettings,
    translate_next_section,
    translate_rest_of_document,
    retranslate_last_section,
)

DEFAULT_MAX_UPLOAD_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_ACTIVE_TRANSLATION_JOBS_PER_USER = 2
DEFAULT_DAILY_UPLOAD_LIMIT_PER_USER = 5
DEFAULT_FREE_TRANSLATION_WORDS_PER_USER = 2000
DEFAULT_ERROR_MESSAGES = {
    400: "The request could not be processed. Please check the details and try again.",
    401: "Please sign in with Google before continuing.",
    402: "You do not have enough free words remaining for this translation.",
    403: "You do not have access to this action.",
    404: "We could not find that item. It may have been deleted or belongs to another account.",
    409: "This action cannot be completed right now.",
    413: "This file is too large.",
    422: "Some request fields are invalid. Please check them and try again.",
    429: "You have reached a usage limit. Please try again later.",
    500: "Something went wrong on our side. Please try again.",
    502: "A payment provider request failed. Please try again.",
}
DEFAULT_ERROR_ACTIONS = {
    401: "Sign in again and retry.",
    402: "Translate a smaller section or upgrade when billing is available.",
    404: "Refresh the page and choose the document again.",
    409: "Refresh the workspace and retry the action.",
    413: "Upload a smaller DOCX, EPUB, or TXT file.",
    422: "Refresh the page if the form looks out of sync.",
    429: "Wait for the current jobs to finish or try again later.",
    500: "If this keeps happening, contact support with the time of the error.",
    502: "Retry in a moment. If this keeps happening, contact support.",
}


def error_detail(
    message: str,
    *,
    code: str | None = None,
    action: str | None = None,
    **extra: object,
) -> dict[str, object]:
    detail: dict[str, object] = {"message": message}
    if code:
        detail["code"] = code
    if action:
        detail["action"] = action
    detail.update(extra)
    return detail


def raise_api_error(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    action: str | None = None,
    **extra: object,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=error_detail(
            message,
            code=code or default_error_code(status_code, extra),
            action=action or DEFAULT_ERROR_ACTIONS.get(status_code),
            **extra,
        ),
    )


def default_error_code(status_code: int, extra: dict[str, object] | None = None) -> str:
    if extra and "quota" in extra:
        return "quota_exceeded"
    return {
        400: "bad_request",
        401: "authentication_required",
        402: "payment_required",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "file_too_large",
        422: "validation_error",
        429: "rate_limited",
        500: "server_error",
        502: "provider_error",
    }.get(status_code, "request_failed")


def normalized_error_detail(detail: object, status_code: int) -> dict[str, object]:
    if isinstance(detail, dict):
        normalized = dict(detail)
        normalized.setdefault(
            "message",
            DEFAULT_ERROR_MESSAGES.get(status_code, "Request failed."),
        )
        normalized.setdefault(
            "code",
            default_error_code(status_code, normalized),
        )
        normalized.setdefault("action", DEFAULT_ERROR_ACTIONS.get(status_code))
        return {key: value for key, value in normalized.items() if value is not None}
    message = str(detail).strip() if detail is not None else ""
    return error_detail(
        message or DEFAULT_ERROR_MESSAGES.get(status_code, "Request failed."),
        code=default_error_code(status_code),
        action=DEFAULT_ERROR_ACTIONS.get(status_code),
    )


def backend_storage_root() -> Path:
    return Path(os.environ.get("TRANSLATOR_BACKEND_STORAGE", "backend_storage"))


def backend_store() -> LocalDocumentStore:
    store_name = os.environ.get("BACKEND_STORE", "mysql").strip().lower()
    if store_name in {"json", "local", "filesystem"}:
        return LocalDocumentStore(backend_storage_root())
    if store_name in {"mysql", "mariadb"}:
        from translator.storage.mysql import MySqlDocumentStore

        return MySqlDocumentStore(backend_storage_root())
    raise RuntimeError(
        "Unsupported BACKEND_STORE value. Use 'mysql' for production state or "
        "'json' for local filesystem state."
    )


def max_upload_bytes() -> int:
    raw_value = os.environ.get("TRANSLATOR_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES))
    try:
        value = int(raw_value)
    except ValueError:
        value = DEFAULT_MAX_UPLOAD_BYTES
    return max(1, value)


def max_active_translation_jobs_per_user() -> int:
    return positive_int_env(
        name="TRANSLATOR_MAX_ACTIVE_JOBS_PER_USER",
        default=DEFAULT_MAX_ACTIVE_TRANSLATION_JOBS_PER_USER,
    )


def daily_upload_limit_per_user() -> int:
    return positive_int_env(
        name="TRANSLATOR_DAILY_UPLOAD_LIMIT_PER_USER",
        default=DEFAULT_DAILY_UPLOAD_LIMIT_PER_USER,
    )


def free_translation_words_per_user() -> int:
    raw_value = os.environ.get(
        "TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER",
        str(DEFAULT_FREE_TRANSLATION_WORDS_PER_USER),
    )
    try:
        value = int(raw_value)
    except ValueError:
        value = DEFAULT_FREE_TRANSLATION_WORDS_PER_USER
    return value


def positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError:
        value = default
    return max(1, value)


store = backend_store()
translator_factory = DevFakeSectionTranslator if os.environ.get(
    "TRANSLATOR_USE_FAKE_TRANSLATOR", ""
).strip().lower() in {"1", "true", "yes", "on"} else AgentSectionTranslator
app = FastAPI(title="Translator Backend", version="0.1.0")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = PROJECT_ROOT / "apps" / "frontend"
if FRONTEND_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIR),
        name="frontend_assets",
    )


@app.exception_handler(StarletteHTTPException)
async def api_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": normalized_error_detail(exc.detail, exc.status_code)},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def api_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    fields = []
    for item in exc.errors()[:5]:
        location = " / ".join(
            str(part)
            for part in item.get("loc", [])
            if part not in {"body", "query", "path"}
        )
        fields.append(
            {
                "field": location or "request",
                "message": str(item.get("msg", "Invalid value.")),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "detail": error_detail(
                "Some request fields are invalid. Please check them and try again.",
                code="validation_error",
                action=DEFAULT_ERROR_ACTIONS[422],
                fields=fields,
            )
        },
    )


@app.on_event("startup")
def fail_interrupted_translation_jobs() -> None:
    for job in store.load_jobs().values():
        if (
            job.get("type") in {"translate_next", "retranslate_last", "translate_rest"}
            and job.get("status") in {"queued", "running"}
        ):
            store.update_job(
                str(job["job_id"]),
                status="failed",
                progress=100,
                message="Translation interrupted",
                error="Translation job was interrupted by a backend restart. Please try again.",
            )


class TranslateNextRequest(BaseModel):
    source_language: str | None = Field(default=None, max_length=128)
    target_language: str = Field(default="Spanish", min_length=1)
    document_type: str = "general"
    content_form: str = "book"
    context_sections: int = Field(default=3, ge=0, le=10)
    model_tier: str = Field(default="balanced", max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source_language")
    @classmethod
    def normalize_source_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("model_tier")
    @classmethod
    def normalize_model_tier(cls, value: str) -> str:
        return value.strip().lower().replace("-", "_") or "balanced"


class CheckoutSessionRequest(BaseModel):
    package_id: str = Field(min_length=1, max_length=64)
    provider: str = Field(default="", max_length=64)


class MockPaymentCompleteRequest(BaseModel):
    external_payment_id: str | None = Field(default=None, max_length=128)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def frontend_index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/app")
def frontend_app() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


def supported_upload_message() -> str:
    supported = [suffix.upper().lstrip(".") for suffix in sorted(SUPPORTED_UPLOAD_SUFFIXES)]
    if len(supported) <= 1:
        supported_text = "".join(supported)
    else:
        supported_text = f"{', '.join(supported[:-1])}, or {supported[-1]}"
    return f"Upload a {supported_text} file."


def human_file_size(byte_count: int) -> str:
    if byte_count >= 1024 * 1024:
        value = byte_count / (1024 * 1024)
        return f"{value:g} MB"
    if byte_count >= 1024:
        value = byte_count / 1024
        return f"{value:g} KB"
    return f"{byte_count} bytes"


def friendly_upload_error(exc: ValueError) -> dict[str, object]:
    raw_message = str(exc)
    comparable = raw_message.lower()
    if "empty" in comparable:
        return error_detail(
            "This file is empty.",
            code="empty_upload",
            action="Upload a document that contains selectable text.",
        )
    if "no translatable text" in comparable:
        return error_detail(
            "We could not find readable text in this document.",
            code="no_translatable_text",
            action="If this is a scanned document, run OCR first and upload the readable file.",
        )
    if "too large" in comparable or "size limit" in comparable:
        return error_detail(
            f"This file is too large. The current upload limit is {human_file_size(max_upload_bytes())}.",
            code="file_too_large",
            action="Upload a smaller DOCX, EPUB, or TXT file.",
            max_upload_bytes=max_upload_bytes(),
        )
    if "unsupported file type" in comparable:
        return error_detail(
            "Unsupported file type.",
            code="unsupported_file_type",
            action=supported_upload_message(),
            supported_extensions=sorted(SUPPORTED_UPLOAD_SUFFIXES),
        )
    return error_detail(
        "We could not analyze this document.",
        code="document_upload_failed",
        action="Upload a DOCX, EPUB, or TXT document that contains selectable text.",
    )


def raise_not_found_from_key_error(exc: KeyError) -> None:
    raw_message = str(exc).lower()
    if "job" in raw_message:
        raise_api_error(status_code=404, message="Job not found.", code="job_not_found")
    if "section" in raw_message:
        raise_api_error(status_code=404, message="Section not found.", code="section_not_found")
    raise_api_error(status_code=404, message="Document not found.", code="document_not_found")


def raise_bad_request_from_value_error(exc: ValueError) -> None:
    raw_message = str(exc).lower()
    if "invalid section data" in raw_message:
        raise_api_error(
            status_code=500,
            message="This document's section data could not be loaded.",
            code="document_state_invalid",
            action="Re-upload the document or contact support if this keeps happening.",
        )
    raise_api_error(
        status_code=400,
        message="The request could not be completed for this document.",
        code="document_request_invalid",
        action="Refresh the workspace and try again.",
    )


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    target_words_per_section: int = Form(DEFAULT_SECTION_TARGET_WORDS),
    source_language: str = Form(DEFAULT_SOURCE_LANGUAGE),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    enforce_daily_upload_quota(user)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=error_detail(
                "Unsupported file type.",
                code="unsupported_file_type",
                action=supported_upload_message(),
                supported_extensions=sorted(SUPPORTED_UPLOAD_SUFFIXES),
            ),
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / f"upload{suffix}"
        try:
            copied_bytes = copy_upload_file_with_limit(
                upload=file,
                output_path=temp_path,
                max_bytes=max_upload_bytes(),
            )
            if copied_bytes == 0:
                raise ValueError("Uploaded file is empty.")
            return store.create_document(
                source_path=temp_path,
                original_filename=file.filename or temp_path.name,
                target_words_per_section=target_words_per_section,
                source_language=source_language,
                owner_user_id=user.user_id,
                owner_email=user.email,
                owner_auth_provider=user.auth_provider,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=friendly_upload_error(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=error_detail(
                    "We could not analyze this document.",
                    code="document_upload_failed",
                    action=(
                        "Try another DOCX, EPUB, or TXT file. If the document is scanned "
                        "or password-protected, export a readable copy first."
                    ),
                ),
            ) from exc


def copy_upload_file_with_limit(
    upload: UploadFile,
    output_path: Path,
    max_bytes: int,
) -> int:
    copied_bytes = 0
    chunk_size = 1024 * 1024
    with output_path.open("wb") as output:
        while True:
            chunk = upload.file.read(chunk_size)
            if not chunk:
                break
            copied_bytes += len(chunk)
            if copied_bytes > max_bytes:
                raise ValueError(
                    f"File is too large. Maximum upload size is {human_file_size(max_bytes)}."
                )
            output.write(chunk)
    return copied_bytes


def authorize_document(document_id: str, user: AuthenticatedUser) -> None:
    metadata = store.load_metadata(document_id)
    owner_user_id = str(metadata.get("owner_user_id", "")).strip()
    if not owner_user_id:
        if user.auth_provider == "dev":
            return
        raise_api_error(
            status_code=404,
            message="Document not found.",
            code="document_not_found",
        )
    if owner_user_id != user.user_id:
        raise_api_error(
            status_code=404,
            message="Document not found.",
            code="document_not_found",
        )


def authorize_job(job: dict[str, object], user: AuthenticatedUser) -> None:
    owner_user_id = str(job.get("owner_user_id", "")).strip()
    if owner_user_id:
        if owner_user_id != user.user_id:
            raise_api_error(status_code=404, message="Job not found.", code="job_not_found")
        return

    document_id = str(job.get("document_id", ""))
    authorize_document(document_id, user)


def enforce_daily_upload_quota(user: AuthenticatedUser) -> None:
    limit = daily_upload_limit_per_user()
    used = store.upload_count_since(user.user_id, utc_day_start())
    if used >= limit:
        raise_quota_error(
            status_code=429,
            message="Daily upload limit reached.",
            action="You can upload more documents tomorrow.",
            quota={
                "type": "daily_uploads",
                "limit": limit,
                "used": used,
                "remaining": 0,
            },
        )


def enforce_active_translation_job_quota(user: AuthenticatedUser) -> None:
    limit = max_active_translation_jobs_per_user()
    active_jobs = store.active_translation_jobs_for_user(user.user_id)
    used = len(active_jobs)
    if used >= limit:
        raise_quota_error(
            status_code=429,
            message="Too many active translation jobs.",
            action="Wait for one of your current translations to finish, then try again.",
            quota={
                "type": "active_translation_jobs",
                "limit": limit,
                "used": used,
                "remaining": 0,
                "active_job_ids": [job.get("job_id") for job in active_jobs],
            },
        )


def enforce_free_translation_word_quota(
    user: AuthenticatedUser,
    requested_word_count: int,
) -> None:
    limit = free_translation_words_per_user()
    if limit < 0:
        return
    used = int(store.usage_summary(owner_user_id=user.user_id)["total_word_count"])
    requested = max(0, int(requested_word_count))
    remaining = max(0, limit - used)
    if requested > remaining:
        raise_quota_error(
            status_code=402,
            message="Free translation word limit reached.",
            action="Translate a smaller section or upgrade when billing is available.",
            quota={
                "type": "lifetime_free_translation_words",
                "limit_words": limit,
                "used_words": used,
                "requested_words": requested,
                "remaining_words": remaining,
            },
        )


def requested_words_for_next_section(summary: dict[str, object]) -> int:
    return estimate_word_count(summary.get("next_section_estimate"))


def requested_words_for_translate_rest(summary: dict[str, object]) -> int:
    return estimate_word_count(summary.get("remaining_estimate"))


def requested_words_for_last_section(document_id: str, summary: dict[str, object]) -> int:
    section_id = str(summary.get("last_translated_section_id") or "")
    if not section_id:
        return 0
    section = find_section(store.load_sections(document_id), section_id)
    if section is None:
        return 0
    return int(section.get("word_count", 0) or 0)


def last_section_estimate(
    document_id: str,
    summary: dict[str, object],
) -> dict[str, object] | None:
    word_count = requested_words_for_last_section(document_id, summary)
    if word_count <= 0:
        return None
    return translation_cost_estimate(
        word_count=word_count,
        chunk_count=1,
        chunk_size_words=max(1, word_count),
    )


def estimate_word_count(value: object) -> int:
    if not isinstance(value, dict):
        return 0
    return int(value.get("word_count", 0) or 0)


def raise_quota_error(
    status_code: int,
    message: str,
    action: str,
    quota: dict[str, object],
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=error_detail(
            message,
            code="quota_exceeded",
            action=action,
            quota=quota,
        ),
    )


def translation_word_quota_payload(user: AuthenticatedUser) -> dict[str, object]:
    limit = free_translation_words_per_user()
    used = int(store.usage_summary(owner_user_id=user.user_id)["total_word_count"])
    if limit < 0:
        return {
            "type": "lifetime_free_translation_words",
            "limit_words": None,
            "used_words": used,
            "remaining_words": None,
        }
    return {
        "type": "lifetime_free_translation_words",
        "limit_words": limit,
        "used_words": used,
        "remaining_words": max(0, limit - used),
    }


def utc_day_start() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)


def ensure_signup_credits(user: AuthenticatedUser) -> None:
    store.ensure_signup_credit_grant(
        owner_user_id=user.user_id,
        owner_email=user.email,
        owner_auth_provider=user.auth_provider,
        credits=signup_credits(),
    )


def wallet_response(user: AuthenticatedUser) -> dict[str, object]:
    ensure_signup_credits(user)
    ledger = store.credit_ledger_for_owner(user.user_id)
    return {
        "owner_user_id": user.user_id,
        "balance_credits": store.credit_balance(user.user_id),
        "signup_credits": signup_credits(),
        "recent_ledger": [compact_ledger_entry(entry) for entry in ledger[:25]],
    }


def mock_payments_enabled() -> bool:
    return os.getenv("TRANSLATOR_ENABLE_MOCK_PAYMENTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def default_payment_provider() -> str:
    if razorpay_payments_configured():
        return "razorpay"
    if mock_payments_enabled():
        return "mock"
    return "razorpay"


def payment_provider_status() -> dict[str, object]:
    return {
        "razorpay": {
            "enabled": razorpay_payments_configured(),
            "orders_configured": razorpay_orders_configured(),
            "webhook_configured": razorpay_webhook_configured(),
        },
        "mock": {
            "enabled": mock_payments_enabled(),
        },
    }


@app.get("/billing/model-tiers")
def get_model_tiers() -> dict[str, object]:
    return {
        "default_model_tier": "balanced",
        "credits_per_1k_tokens": credits_per_1k_tokens(),
        "model_tiers": public_model_tiers(),
    }


@app.get("/billing/wallet")
def get_wallet(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    return wallet_response(user)


@app.get("/billing/credit-packages")
def get_credit_packages() -> dict[str, object]:
    provider = default_payment_provider()
    return {
        "provider": provider,
        "checkout_enabled": provider == "razorpay" and razorpay_payments_configured()
        or provider == "mock" and mock_payments_enabled(),
        "providers": payment_provider_status(),
        "razorpay_key_id": razorpay_checkout_key_id() if razorpay_payments_configured() else None,
        "currency": "USD",
        "packages": credit_packages(),
    }


@app.post("/billing/checkout-session")
def create_checkout_session(
    checkout_request: CheckoutSessionRequest,
    http_request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    provider = checkout_request.provider.strip().lower() or default_payment_provider()
    if provider not in {"mock", "razorpay"}:
        raise_api_error(
            status_code=400,
            message="This payment provider is not configured yet.",
            code="payment_provider_not_configured",
            action="Refresh the billing page and choose a listed payment option.",
        )
    if provider == "mock" and not mock_payments_enabled():
        raise_api_error(
            status_code=403,
            message="Mock checkout is disabled.",
            code="mock_payments_disabled",
            action="Enable TRANSLATOR_ENABLE_MOCK_PAYMENTS only in local development.",
        )
    if provider == "razorpay" and not razorpay_payments_configured():
        raise_api_error(
            status_code=400,
            message="Razorpay checkout is not configured.",
            code="razorpay_not_configured",
            action="Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, and RAZORPAY_WEBHOOK_SECRET before using Razorpay checkout.",
        )
    try:
        package = credit_package_by_id(checkout_request.package_id)
    except ValueError:
        raise_api_error(
            status_code=400,
            message="Unknown credit package.",
            code="credit_package_not_found",
            action="Refresh the billing page and choose a listed package.",
        )
    order = store.create_payment_order(
        owner_user_id=user.user_id,
        owner_email=user.email,
        owner_auth_provider=user.auth_provider,
        package=package,
        provider=provider,
    )
    if provider == "razorpay":
        try:
            razorpay_order = create_razorpay_order(order)
            order = store.update_payment_order_checkout(
                order_id=str(order["order_id"]),
                checkout_url="",
                external_payment_id=razorpay_order.razorpay_order_id,
                metadata={
                    "razorpay_order_id": razorpay_order.razorpay_order_id,
                    "package_name": package.get("name"),
                },
            )
        except RazorpayConfigurationError as exc:
            raise_api_error(
                status_code=400,
                message="Razorpay checkout is not configured.",
                code="razorpay_not_configured",
                action="Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, and RAZORPAY_WEBHOOK_SECRET before using Razorpay checkout.",
            )
        except RazorpayIntegrationError as exc:
            raise_api_error(
                status_code=502,
                message="Razorpay could not create an order.",
                code="razorpay_order_failed",
                action="Retry checkout in a moment.",
            )
        order["razorpay_key_id"] = razorpay_checkout_key_id()
        order["razorpay_order_id"] = razorpay_order.razorpay_order_id
    return {
        **order,
        "message": "Checkout session created. Credits are added only after verified payment completion.",
    }


@app.post("/billing/razorpay/webhook")
async def razorpay_webhook(
    http_request: Request,
    razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
) -> dict[str, object]:
    payload = await http_request.body()
    try:
        event = construct_razorpay_webhook_event(payload, razorpay_signature)
    except RazorpayWebhookSignatureError:
        raise_api_error(
            status_code=400,
            message="Razorpay webhook signature could not be verified.",
            code="razorpay_webhook_signature_invalid",
            action="Check the Razorpay webhook signing secret.",
        )
    except RazorpayWebhookPayloadError:
        raise_api_error(
            status_code=400,
            message="Razorpay webhook payload was invalid.",
            code="razorpay_webhook_payload_invalid",
            action="Retry the webhook from Razorpay.",
        )
    except RazorpayConfigurationError:
        raise_api_error(
            status_code=500,
            message="Razorpay webhook is not configured.",
            code="razorpay_webhook_not_configured",
            action="Set RAZORPAY_WEBHOOK_SECRET on the backend.",
        )

    event_type = razorpay_event_type(event)
    if event_type != "payment.captured":
        return {"received": True, "ignored": True, "event_type": event_type}

    payment = razorpay_payment_entity(event)
    paid_order = complete_razorpay_payment(payment=payment, event_id=razorpay_event_id(event))
    return {
        "received": True,
        "event_type": event_type,
        "order_id": paid_order["order_id"],
        "status": paid_order["status"],
    }


def public_app_base_url(http_request: Request) -> str:
    configured = (
        os.getenv("TRANSLATOR_PUBLIC_APP_URL", "").strip()
        or os.getenv("FRONTEND_BASE_URL", "").strip()
    )
    if configured:
        return configured.rstrip("/")
    return str(http_request.base_url).rstrip("/")


def complete_razorpay_payment(
    payment: dict[str, object],
    event_id: str,
) -> dict[str, object]:
    razorpay_order_id = str(payment.get("order_id") or "")
    if not razorpay_order_id:
        raise_api_error(
            status_code=400,
            message="Razorpay payment was missing an order reference.",
            code="razorpay_order_reference_missing",
            action="Check the Razorpay webhook payload and retry the webhook.",
        )
    try:
        order = store.payment_order_for_external_id(razorpay_order_id)
    except KeyError:
        raise_api_error(
            status_code=404,
            message="Payment order not found.",
            code="payment_order_not_found",
        )
    validate_razorpay_payment(order=order, payment=payment)
    external_payment_id = str(payment.get("id") or "") or event_id
    try:
        return store.complete_payment_order(
            order_id=str(order["order_id"]),
            external_payment_id=external_payment_id,
        )
    except ValueError:
        raise_api_error(
            status_code=409,
            message="This payment order cannot be completed.",
            code="payment_order_not_payable",
            action="Create a new checkout session.",
        )


def validate_razorpay_payment(
    *,
    order: dict[str, object],
    payment: dict[str, object],
) -> None:
    if order.get("provider") != "razorpay":
        raise_api_error(
            status_code=400,
            message="Razorpay webhook referenced a non-Razorpay order.",
            code="razorpay_order_provider_mismatch",
            action="Check the payment order and webhook metadata.",
        )
    order_metadata = order.get("metadata", {})
    order_metadata = order_metadata if isinstance(order_metadata, dict) else {}
    stored_razorpay_order_id = str(order_metadata.get("razorpay_order_id") or "")
    expected_razorpay_order_id = stored_razorpay_order_id or str(order.get("external_payment_id") or "")
    payment_razorpay_order_id = str(payment.get("order_id") or "")
    if expected_razorpay_order_id != payment_razorpay_order_id:
        raise_api_error(
            status_code=400,
            message="Razorpay payment order does not match this order.",
            code="razorpay_order_mismatch",
            action="Retry the correct Razorpay webhook event.",
        )
    payment_status = str(payment.get("status") or "").lower()
    if payment_status != "captured":
        raise_api_error(
            status_code=409,
            message="Razorpay payment is not captured yet.",
            code="razorpay_payment_not_captured",
            action="Wait for a captured Razorpay payment event.",
        )
    amount_paid = razorpay_payment_int_field(payment, "amount")
    if amount_paid != int(order.get("amount_cents", 0) or 0):
        raise_api_error(
            status_code=400,
            message="Razorpay payment amount did not match the server order.",
            code="razorpay_amount_mismatch",
            action="Do not grant credits for this webhook. Review it in Razorpay.",
        )
    currency = str(payment.get("currency") or "").upper()
    if currency != str(order.get("currency", "")).upper():
        raise_api_error(
            status_code=400,
            message="Razorpay payment currency did not match the server order.",
            code="razorpay_currency_mismatch",
            action="Do not grant credits for this webhook. Review it in Razorpay.",
        )
    notes = razorpay_payment_notes(payment)
    if notes.get("owner_user_id") != order.get("owner_user_id"):
        raise_api_error(
            status_code=400,
            message="Razorpay payment user metadata did not match the server order.",
            code="razorpay_owner_mismatch",
            action="Do not grant credits for this webhook. Review it in Razorpay.",
        )
    if notes.get("package_id") != order.get("package_id"):
        raise_api_error(
            status_code=400,
            message="Razorpay payment package metadata did not match the server order.",
            code="razorpay_package_mismatch",
            action="Do not grant credits for this webhook. Review it in Razorpay.",
        )


def razorpay_payment_int_field(payment: dict[str, object], field_name: str) -> int:
    raw_value = payment.get(field_name)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        raise_api_error(
            status_code=400,
            message=f"Razorpay payment field {field_name} was invalid.",
            code=f"razorpay_{field_name}_invalid",
            action="Do not grant credits for this webhook. Review it in Razorpay.",
        )


@app.post("/billing/mock-payments/{order_id}/complete")
def complete_mock_payment(
    order_id: str,
    request: MockPaymentCompleteRequest,
    x_mock_payment_secret: str | None = Header(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    if not mock_payments_enabled():
        raise_api_error(
            status_code=403,
            message="Mock payment completion is disabled.",
            code="mock_payments_disabled",
            action="Enable TRANSLATOR_ENABLE_MOCK_PAYMENTS only in local development.",
        )
    expected_secret = os.getenv("TRANSLATOR_MOCK_PAYMENT_SECRET", "").strip()
    if not expected_secret or x_mock_payment_secret != expected_secret:
        raise_api_error(
            status_code=403,
            message="Mock payment completion was not authorized.",
            code="mock_payment_secret_invalid",
            action="Use the server-side mock payment secret in local development.",
        )
    try:
        order = store.load_payment_order(order_id)
    except KeyError:
        raise_api_error(status_code=404, message="Payment order not found.", code="payment_order_not_found")
    if order.get("owner_user_id") != user.user_id:
        raise_api_error(status_code=404, message="Payment order not found.", code="payment_order_not_found")
    try:
        paid_order = store.complete_payment_order(
            order_id=order_id,
            external_payment_id=request.external_payment_id
            or f"mock_{order_id}",
        )
    except ValueError:
        raise_api_error(
            status_code=409,
            message="This payment order cannot be completed.",
            code="payment_order_not_payable",
            action="Create a new checkout session.",
        )
    return {
        "order": paid_order,
        "wallet": wallet_response(user),
    }


@app.get("/documents")
def list_documents(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    documents = []
    for metadata in store.document_metadata_for_owner(user.user_id):
        document_id = str(metadata.get("document_id", ""))
        if not document_id:
            continue
        try:
            documents.append(store.document_summary(document_id))
        except (KeyError, ValueError):
            documents.append(dict(metadata))
    documents.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return {
        "documents": documents,
        "document_count": len(documents),
    }


@app.get("/documents/{document_id}")
def get_document(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        return store.document_summary(document_id)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.get("/documents/{document_id}/sections")
def get_sections(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        return store.sections_response(document_id)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.get("/documents/{document_id}/preview")
def get_preview(
    document_id: str,
    section_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        return store.preview_response(
            document_id,
            section_id=section_id,
            offset=offset,
            limit=limit,
        )
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.get("/documents/{document_id}/glossary")
def get_glossary(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        glossary = store.load_glossary(document_id)
        return {
            "document_id": document_id,
            "entry_count": len(glossary),
            "glossary": glossary,
        }
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.get("/documents/{document_id}/quote")
def get_document_quote(
    document_id: str,
    model_tier: str = Query(default="balanced"),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        tier = model_tier_by_id(model_tier)
    except ValueError:
        raise_api_error(
            status_code=400,
            message="Unknown model tier.",
            code="model_tier_not_found",
            action="Choose Quick Draft, Balanced, or Precision.",
        )
    try:
        authorize_document(document_id, user)
        summary = store.document_summary(document_id)
        ensure_signup_credits(user)
        return {
            "document_id": document_id,
            "model_tier": tier.public_dict(),
            "wallet": {
                "balance_credits": store.credit_balance(user.user_id),
            },
            "next_section": quote_from_estimate(
                summary.get("next_section_estimate"),
                tier.tier_id,
            ),
            "remaining_document": quote_from_estimate(
                summary.get("remaining_estimate"),
                tier.tier_id,
            ),
            "retranslate_last": quote_from_estimate(
                last_section_estimate(document_id, summary),
                tier.tier_id,
            ),
        }
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.post("/documents/{document_id}/translate-next", status_code=202)
async def translate_next(
    document_id: str,
    request: TranslateNextRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        summary = store.document_summary(document_id)
        apply_document_language_defaults(request, summary)
        validate_language_pair(request)
        validate_model_tier(request)
        idempotent_job = idempotent_job_response(
            document_id=document_id,
            job_type="translate_next",
            request=request,
            summary=summary,
        )
        if idempotent_job is not None:
            return idempotent_job
        if summary.get("next_section_id") is None:
            raise_api_error(
                status_code=409,
                message="All sections are already translated.",
                code="document_already_translated",
                action="Export the document or choose Retranslate Last Section.",
            )
        active_job = active_translation_job(document_id)
        if active_job is not None:
            return job_start_response(
                job=active_job,
                document_id=document_id,
                summary=summary,
                reused_active_job=True,
            )
        enforce_active_translation_job_quota(user)
        enforce_free_translation_word_quota(
            user,
            requested_words_for_next_section(summary),
        )
        ensure_signup_credits(user)
        quote = quote_for_translation_action(
            document_id=document_id,
            action="translate_next",
            request=request,
            summary=summary,
        )
        job = store.create_job(
            document_id=document_id,
            job_type="translate_next",
            payload=translation_request_payload(request),
            owner_user_id=user.user_id,
            owner_email=user.email,
            owner_auth_provider=user.auth_provider,
        )
        reserve_credits_for_job(job, quote)
        background_tasks.add_task(
            run_translate_next_job,
            job_id=str(job["job_id"]),
            document_id=document_id,
            request=request,
        )
        return job_start_response(job=job, document_id=document_id, summary=summary)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)
    except ValueError as exc:
        raise_bad_request_from_value_error(exc)


@app.post("/documents/{document_id}/retranslate-last", status_code=202)
async def retranslate_last(
    document_id: str,
    request: TranslateNextRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        summary = store.document_summary(document_id)
        apply_document_language_defaults(request, summary)
        validate_language_pair(request)
        validate_model_tier(request)
        idempotent_job = idempotent_job_response(
            document_id=document_id,
            job_type="retranslate_last",
            request=request,
            summary=summary,
        )
        if idempotent_job is not None:
            return idempotent_job
        if summary.get("last_translated_section_id") is None:
            raise_api_error(
                status_code=409,
                message="No translated section is available to retranslate.",
                code="nothing_to_retranslate",
                action="Translate the next section first.",
            )
        active_job = active_translation_job(document_id)
        if active_job is not None:
            return job_start_response(
                job=active_job,
                document_id=document_id,
                summary=summary,
                reused_active_job=True,
            )
        enforce_active_translation_job_quota(user)
        enforce_free_translation_word_quota(
            user,
            requested_words_for_last_section(document_id, summary),
        )
        ensure_signup_credits(user)
        quote = quote_for_translation_action(
            document_id=document_id,
            action="retranslate_last",
            request=request,
            summary=summary,
        )
        job = store.create_job(
            document_id=document_id,
            job_type="retranslate_last",
            payload=translation_request_payload(request),
            owner_user_id=user.user_id,
            owner_email=user.email,
            owner_auth_provider=user.auth_provider,
        )
        reserve_credits_for_job(job, quote)
        background_tasks.add_task(
            run_retranslate_last_job,
            job_id=str(job["job_id"]),
            document_id=document_id,
            request=request,
        )
        return job_start_response(job=job, document_id=document_id, summary=summary)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)
    except ValueError as exc:
        raise_bad_request_from_value_error(exc)


@app.post("/documents/{document_id}/translate-rest", status_code=202)
async def translate_rest(
    document_id: str,
    request: TranslateNextRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        summary = store.document_summary(document_id)
        apply_document_language_defaults(request, summary)
        validate_language_pair(request)
        validate_model_tier(request)
        idempotent_job = idempotent_job_response(
            document_id=document_id,
            job_type="translate_rest",
            request=request,
            summary=summary,
        )
        if idempotent_job is not None:
            return idempotent_job
        remaining_estimate = summary.get("remaining_estimate", {})
        remaining_blocks = (
            int(remaining_estimate.get("remaining_block_count", 0))
            if isinstance(remaining_estimate, dict)
            else 0
        )
        if remaining_blocks <= 0:
            raise_api_error(
                status_code=409,
                message="All sections are already translated.",
                code="document_already_translated",
                action="Export the document or choose Retranslate Last Section.",
            )
        active_job = active_translation_job(document_id)
        if active_job is not None:
            return job_start_response(
                job=active_job,
                document_id=document_id,
                summary=summary,
                reused_active_job=True,
            )
        enforce_active_translation_job_quota(user)
        enforce_free_translation_word_quota(
            user,
            requested_words_for_translate_rest(summary),
        )
        ensure_signup_credits(user)
        quote = quote_for_translation_action(
            document_id=document_id,
            action="translate_rest",
            request=request,
            summary=summary,
        )
        job = store.create_job(
            document_id=document_id,
            job_type="translate_rest",
            payload=translation_request_payload(request),
            owner_user_id=user.user_id,
            owner_email=user.email,
            owner_auth_provider=user.auth_provider,
        )
        reserve_credits_for_job(job, quote)
        background_tasks.add_task(
            run_translate_rest_job,
            job_id=str(job["job_id"]),
            document_id=document_id,
            request=request,
        )
        return job_start_response(job=job, document_id=document_id, summary=summary)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)
    except ValueError as exc:
        raise_bad_request_from_value_error(exc)


def active_translation_job(document_id: str) -> dict[str, object] | None:
    return (
        store.active_job_for_document(document_id, "translate_next")
        or store.active_job_for_document(document_id, "retranslate_last")
        or store.active_job_for_document(document_id, "translate_rest")
    )


def apply_document_language_defaults(
    request: TranslateNextRequest,
    summary: dict[str, object],
) -> None:
    request.source_language = normalize_language_name(
        request.source_language or summary.get("source_language"),
        default=DEFAULT_SOURCE_LANGUAGE,
    )


def validate_language_pair(request: TranslateNextRequest) -> None:
    source = normalize_language_name(request.source_language).casefold()
    target = normalize_language_name(request.target_language).casefold()
    if source == target:
        raise HTTPException(
            status_code=400,
            detail=error_detail(
                "Choose different source and target languages.",
                code="same_language_pair",
                action="Change either language and try again.",
            ),
        )


def validate_model_tier(request: TranslateNextRequest) -> None:
    try:
        tier = model_tier_by_id(request.model_tier)
    except ValueError:
        raise_api_error(
            status_code=400,
            message="Unknown model tier.",
            code="model_tier_not_found",
            action="Choose Quick Draft, Balanced, or Precision.",
        )
    request.model_tier = tier.tier_id


def idempotent_job_response(
    document_id: str,
    job_type: str,
    request: TranslateNextRequest,
    summary: dict[str, object],
) -> dict[str, object] | None:
    if not request.idempotency_key:
        return None
    job = store.job_for_idempotency_key(
        document_id=document_id,
        job_type=job_type,
        idempotency_key=request.idempotency_key,
    )
    if job is None:
        return None
    existing_payload = job.get("payload", {})
    if existing_payload != translation_request_payload(request):
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "This translate request was already submitted with different options.",
                code="idempotency_key_conflict",
                action="Refresh the workspace and retry from the button.",
            ),
        )
    return job_start_response(
        job=job,
        document_id=document_id,
        summary=summary,
        idempotent_replay=True,
    )


def translation_request_payload(request: TranslateNextRequest) -> dict[str, object]:
    payload = request.model_dump(exclude_none=True)
    payload["source_language"] = normalize_language_name(
        payload.get("source_language"),
        default=DEFAULT_SOURCE_LANGUAGE,
    )
    return payload


def job_start_response(
    job: dict[str, object],
    document_id: str,
    summary: dict[str, object],
    idempotent_replay: bool = False,
    reused_active_job: bool = False,
) -> dict[str, object]:
    return {
        "job_id": job["job_id"],
        "document_id": document_id,
        "status": job["status"],
        "message": job["message"],
        "poll_url": f"/jobs/{job['job_id']}",
        "idempotent_replay": idempotent_replay,
        "reused_active_job": reused_active_job,
        "next_section_estimate": summary.get("next_section_estimate"),
        "remaining_estimate": summary.get("remaining_estimate"),
    }


def quote_for_translation_action(
    document_id: str,
    action: str,
    request: TranslateNextRequest,
    summary: dict[str, object],
) -> dict[str, object]:
    if action == "translate_next":
        estimate = summary.get("next_section_estimate")
    elif action == "translate_rest":
        estimate = summary.get("remaining_estimate")
    elif action == "retranslate_last":
        estimate = last_section_estimate(document_id, summary)
    else:
        estimate = None
    quote = quote_from_estimate(estimate, request.model_tier)
    if quote is None:
        return {
            "model_tier": model_tier_by_id(request.model_tier).public_dict(),
            "estimated_credits": 0,
            "word_count": 0,
            "chunk_count": 0,
            "estimated_total_tokens": 0,
        }
    return quote


def reserve_credits_for_job(
    job: dict[str, object],
    quote: dict[str, object],
) -> None:
    estimated_credits = int(quote.get("estimated_credits", 0) or 0)
    model_tier = str(job.get("payload", {}).get("model_tier", "balanced")) if isinstance(job.get("payload"), dict) else "balanced"
    try:
        store.reserve_credits_for_job(
            job=job,
            credits=estimated_credits,
            model_tier=model_tier,
            metadata={
                "quote": quote,
                "reservation_reason": job.get("type"),
            },
        )
    except InsufficientCreditsError as exc:
        store.update_job(
            str(job["job_id"]),
            status="failed",
            progress=100,
            message="Insufficient credits",
            error="Not enough credits were available to start this translation.",
        )
        raise_api_error(
            status_code=402,
            message="Not enough credits available.",
            code="insufficient_credits",
            action="Add credits to your wallet or choose a lower-credit model tier.",
            wallet={
                "available_credits": exc.available_credits,
                "required_credits": exc.required_credits,
            },
        )


def record_usage_for_job(
    job: dict[str, object],
    result: dict[str, object],
) -> dict[str, object] | None:
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return None
    return store.record_usage(job=job, usage=usage)


def capture_credits_for_job(
    job: dict[str, object],
    result: dict[str, object],
    usage_record: dict[str, object] | None,
) -> None:
    payload = job.get("payload", {})
    payload = payload if isinstance(payload, dict) else {}
    actual_credits = credits_for_usage(
        result.get("usage"),
        str(payload.get("model_tier", "balanced")),
    )
    store.capture_credit_reservation(
        job=job,
        actual_credits=actual_credits,
        usage_record=usage_record,
    )


def release_credits_for_job(job: dict[str, object] | None, reason: str) -> None:
    if not job:
        return
    store.release_credit_reservation(job=job, reason=reason)


def translator_for_request(request: TranslateNextRequest) -> object:
    tier = model_tier_by_id(request.model_tier)
    try:
        return translator_factory(model_name=tier.model_name)
    except TypeError:
        return translator_factory()


async def run_translate_next_job(
    job_id: str,
    document_id: str,
    request: TranslateNextRequest,
) -> None:
    store.update_job(
        job_id,
        status="running",
        progress=10,
        message="Translating next section",
    )
    try:
        result = await translate_next_section(
            store=store,
            document_id=document_id,
            settings=TranslateNextSettings(
                target_language=request.target_language,
                source_language=request.source_language or DEFAULT_SOURCE_LANGUAGE,
                document_type=request.document_type,
                content_form=request.content_form,
                context_sections=request.context_sections,
            ),
            translator=translator_for_request(request),
        )
        succeeded_job = store.update_job(
            job_id,
            status="succeeded",
            progress=100,
            message="Translation complete",
            result=result,
            error="",
        )
        usage_record = record_usage_for_job(succeeded_job, result)
        capture_credits_for_job(succeeded_job, result, usage_record)
    except NoNextSectionError as exc:
        failed_job = store.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Translation skipped",
            error=str(exc),
        )
        release_credits_for_job(failed_job, "translation_skipped")
    except Exception as exc:
        failed_job = store.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Translation failed",
            error=format_translation_error(exc),
        )
        release_credits_for_job(failed_job, "translation_failed")


async def run_retranslate_last_job(
    job_id: str,
    document_id: str,
    request: TranslateNextRequest,
) -> None:
    store.update_job(
        job_id,
        status="running",
        progress=10,
        message="Retranslating last translated section",
    )
    try:
        result = await retranslate_last_section(
            store=store,
            document_id=document_id,
            settings=TranslateNextSettings(
                target_language=request.target_language,
                source_language=request.source_language or DEFAULT_SOURCE_LANGUAGE,
                document_type=request.document_type,
                content_form=request.content_form,
                context_sections=request.context_sections,
            ),
            translator=translator_for_request(request),
        )
        succeeded_job = store.update_job(
            job_id,
            status="succeeded",
            progress=100,
            message="Retranslation complete",
            result=result,
            error="",
        )
        usage_record = record_usage_for_job(succeeded_job, result)
        capture_credits_for_job(succeeded_job, result, usage_record)
    except NoNextSectionError as exc:
        failed_job = store.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Retranslation skipped",
            error=str(exc),
        )
        release_credits_for_job(failed_job, "retranslation_skipped")
    except Exception as exc:
        failed_job = store.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Retranslation failed",
            error=format_translation_error(exc),
        )
        release_credits_for_job(failed_job, "retranslation_failed")


async def run_translate_rest_job(
    job_id: str,
    document_id: str,
    request: TranslateNextRequest,
) -> None:
    store.update_job(
        job_id,
        status="running",
        progress=10,
        message="Translating rest of document",
    )

    def update_progress(progress: int, message: str) -> None:
        store.update_job(
            job_id,
            status="running",
            progress=progress,
            message=message,
        )

    try:
        result = await translate_rest_of_document(
            store=store,
            document_id=document_id,
            settings=TranslateNextSettings(
                target_language=request.target_language,
                source_language=request.source_language or DEFAULT_SOURCE_LANGUAGE,
                document_type=request.document_type,
                content_form=request.content_form,
                context_sections=request.context_sections,
            ),
            translator=translator_for_request(request),
            progress_callback=update_progress,
        )
        succeeded_job = store.update_job(
            job_id,
            status="succeeded",
            progress=100,
            message="Rest-of-document translation complete",
            result=result,
            error="",
        )
        usage_record = record_usage_for_job(succeeded_job, result)
        capture_credits_for_job(succeeded_job, result, usage_record)
    except NoNextSectionError as exc:
        failed_job = store.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Rest-of-document translation skipped",
            error=str(exc),
        )
        release_credits_for_job(failed_job, "rest_translation_skipped")
    except Exception as exc:
        failed_job = store.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Rest-of-document translation failed",
            error=format_translation_error(exc),
        )
        release_credits_for_job(failed_job, "rest_translation_failed")


def format_translation_error(exc: Exception) -> str:
    raw_message = f"{type(exc).__name__}: {exc}"
    comparable = raw_message.lower()
    if "504" in comparable or "timeout" in comparable or "timed out" in comparable:
        return (
            "The translation provider timed out before returning a result. "
            "Please retry; if it keeps happening, use Translate Next Section before translating the rest."
        )
    if (
        "api key" in comparable
        or "authentication" in comparable
        or "unauthorized" in comparable
        or "permission denied" in comparable
        or "401" in comparable
        or "403" in comparable
    ):
        return (
            "The translation provider rejected our credentials. "
            "Please ask support to check the provider API key and permissions."
        )
    if (
        "invalid model" in comparable
        or "model not found" in comparable
        or "unsupported model_provider" in comparable
        or "404" in comparable
    ):
        return (
            "The configured translation model is not available right now. "
            "Please ask support to check the model setting."
        )
    if (
        "429" in comparable
        or "rate limit" in comparable
        or "resource exhausted" in comparable
        or "quota" in comparable
    ):
        return (
            "The translation provider is busy or has reached a quota limit. "
            "Please wait a minute and retry."
        )
    if (
        "context length" in comparable
        or "maximum context" in comparable
        or "too many tokens" in comparable
        or "token limit" in comparable
    ):
        return (
            "This section is too large for the translation provider. "
            "Please retry with the next section workflow."
        )
    if "safety" in comparable or "blocked" in comparable or "policy" in comparable:
        return (
            "The translation provider blocked this section. "
            "Please review the source text and retry."
        )
    return "Translation failed before a result was returned. Please retry in a moment."


@app.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        job = store.load_job(job_id)
        authorize_job(job, user)
        return job
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.get("/usage/me")
def get_my_usage(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    summary = store.usage_summary(owner_user_id=user.user_id)
    summary["quota"] = translation_word_quota_payload(user)
    upload_limit = daily_upload_limit_per_user()
    upload_used = store.upload_count_since(user.user_id, utc_day_start())
    active_jobs = store.active_translation_jobs_for_user(user.user_id)
    summary["daily_upload_quota"] = {
        "type": "daily_uploads",
        "limit": upload_limit,
        "used": upload_used,
        "remaining": max(0, upload_limit - upload_used),
    }
    summary["active_translation_jobs"] = {
        "type": "active_translation_jobs",
        "limit": max_active_translation_jobs_per_user(),
        "used": len(active_jobs),
        "active_job_ids": [job.get("job_id") for job in active_jobs],
    }
    return summary


@app.get("/documents/{document_id}/usage")
def get_document_usage(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        summary = store.usage_summary(owner_user_id=user.user_id, document_id=document_id)
        summary["quota"] = translation_word_quota_payload(user)
        return summary
    except KeyError as exc:
        raise_not_found_from_key_error(exc)


@app.post("/documents/{document_id}/export")
def export_document(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, object]:
    try:
        authorize_document(document_id, user)
        return store.export_document(document_id)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)
    except FileNotFoundError as exc:
        raise_api_error(
            status_code=404,
            message="No export file is available yet.",
            code="export_not_found",
            action="Create an export first, then download it.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=error_detail(
                "We could not create the export file.",
                code="export_failed",
                action="Retry the export. If it keeps failing, contact support.",
            ),
        ) from exc


@app.get("/documents/{document_id}/exports/latest/download")
def download_latest_export(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> FileResponse:
    try:
        authorize_document(document_id, user)
    except KeyError as exc:
        raise_not_found_from_key_error(exc)

    try:
        output_path = store.latest_export_file(document_id)
    except (KeyError, FileNotFoundError) as exc:
        raise_api_error(
            status_code=404,
            message="No export file is available yet.",
            code="export_not_found",
            action="Create an export first, then download it.",
        )

    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/octet-stream",
    )
