from __future__ import annotations

from dataclasses import dataclass
import hmac
import hashlib
import json
import os
from typing import Any


try:
    import razorpay
except ImportError:  # pragma: no cover - exercised only when dependency is absent.
    razorpay = None


class RazorpayIntegrationError(RuntimeError):
    """Base error for Razorpay integration failures."""


class RazorpayConfigurationError(RazorpayIntegrationError):
    """Raised when Razorpay is requested but not configured."""


class RazorpayWebhookPayloadError(RazorpayIntegrationError):
    """Raised when Razorpay sends an invalid webhook payload."""


class RazorpayWebhookSignatureError(RazorpayIntegrationError):
    """Raised when a webhook signature cannot be verified."""


@dataclass(frozen=True)
class RazorpayOrderResult:
    razorpay_order_id: str


def razorpay_orders_configured() -> bool:
    return bool(razorpay_key_id()) and bool(razorpay_key_secret()) and razorpay is not None


def razorpay_webhook_configured() -> bool:
    return bool(razorpay_webhook_secret())


def razorpay_payments_configured() -> bool:
    return razorpay_orders_configured() and razorpay_webhook_configured()


def razorpay_key_id() -> str:
    return os.getenv("RAZORPAY_KEY_ID", "").strip()


def razorpay_key_secret() -> str:
    return os.getenv("RAZORPAY_KEY_SECRET", "").strip()


def razorpay_webhook_secret() -> str:
    return os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()


def razorpay_checkout_key_id() -> str:
    return razorpay_key_id()


def create_razorpay_order(order: dict[str, object]) -> RazorpayOrderResult:
    client = require_razorpay_client()
    notes = razorpay_notes(order)
    result = client.order.create(
        data={
            "amount": int(order.get("amount_cents", 0) or 0),
            "currency": str(order.get("currency", "INR")).upper(),
            "receipt": str(order.get("order_id", "")),
            "notes": notes,
        }
    )
    razorpay_order_id = str(razorpay_object_value(result, "id") or "")
    if not razorpay_order_id:
        raise RazorpayIntegrationError("Razorpay did not return an order ID.")
    return RazorpayOrderResult(razorpay_order_id=razorpay_order_id)


def construct_razorpay_webhook_event(payload: bytes, signature: str | None) -> dict[str, Any]:
    verify_razorpay_webhook_signature(payload=payload, signature=signature)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RazorpayWebhookPayloadError("Invalid Razorpay webhook payload.") from exc
    if not isinstance(event, dict):
        raise RazorpayWebhookPayloadError("Razorpay webhook payload was not an object.")
    return event


def verify_razorpay_webhook_signature(payload: bytes, signature: str | None) -> None:
    if not signature:
        raise RazorpayWebhookSignatureError("Missing X-Razorpay-Signature header.")
    secret = razorpay_webhook_secret()
    if not secret:
        raise RazorpayConfigurationError("RAZORPAY_WEBHOOK_SECRET is not configured.")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, signature):
        raise RazorpayWebhookSignatureError("Invalid Razorpay webhook signature.")


def razorpay_event_type(event: dict[str, Any]) -> str:
    return str(event.get("event") or "")


def razorpay_event_id(event: dict[str, Any]) -> str:
    return str(event.get("id") or event.get("entity") or "")


def razorpay_payment_entity(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return {}
    payment = payload.get("payment")
    if not isinstance(payment, dict):
        return {}
    entity = payment.get("entity")
    return entity if isinstance(entity, dict) else {}


def razorpay_object_value(value: object, key: str, default: object | None = None) -> object | None:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def razorpay_notes(order: dict[str, object]) -> dict[str, str]:
    return {
        "order_id": str(order.get("order_id", "")),
        "owner_user_id": str(order.get("owner_user_id", "")),
        "package_id": str(order.get("package_id", "")),
        "credits": str(int(order.get("credits", 0) or 0)),
    }


def razorpay_payment_notes(payment: dict[str, Any]) -> dict[str, str]:
    notes = payment.get("notes") or {}
    if isinstance(notes, dict):
        return {str(key): str(value) for key, value in notes.items()}
    return {}


def require_razorpay_client() -> Any:
    if razorpay is None:
        raise RazorpayConfigurationError(
            "The razorpay package is not installed. Install requirements.txt first."
        )
    if not razorpay_key_id() or not razorpay_key_secret():
        raise RazorpayConfigurationError(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be configured."
        )
    return razorpay.Client(auth=(razorpay_key_id(), razorpay_key_secret()))
