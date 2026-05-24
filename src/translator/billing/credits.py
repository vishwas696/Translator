from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Any

from translator.services.translation_jobs import DEFAULT_GEMINI_MODEL


DEFAULT_MODEL_TIER_ID = "balanced"
DEFAULT_CREDITS_PER_1K_TOKENS = 10.0
DEFAULT_SIGNUP_CREDITS = 200


@dataclass(frozen=True)
class ModelTier:
    tier_id: str
    name: str
    description: str
    credit_multiplier: float
    model_name: str
    recommended: bool = False

    def public_dict(self) -> dict[str, object]:
        return {
            "tier_id": self.tier_id,
            "name": self.name,
            "description": self.description,
            "credit_multiplier": self.credit_multiplier,
            "recommended": self.recommended,
        }


def configured_model_tiers() -> list[ModelTier]:
    default_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    return [
        ModelTier(
            tier_id="quick_draft",
            name="Quick Draft",
            description="Lowest credit use for fast first-pass translation.",
            credit_multiplier=float_env("TRANSLATOR_QUICK_DRAFT_CREDIT_MULTIPLIER", 0.75),
            model_name=model_env("TRANSLATOR_QUICK_DRAFT_MODEL", default_model),
        ),
        ModelTier(
            tier_id="balanced",
            name="Balanced",
            description="Recommended quality and credit balance for most documents.",
            credit_multiplier=float_env("TRANSLATOR_BALANCED_CREDIT_MULTIPLIER", 1.0),
            model_name=model_env("TRANSLATOR_BALANCED_MODEL", default_model),
            recommended=True,
        ),
        ModelTier(
            tier_id="precision",
            name="Precision",
            description="Highest quality pass for sensitive or publication-ready content.",
            credit_multiplier=float_env("TRANSLATOR_PRECISION_CREDIT_MULTIPLIER", 1.6),
            model_name=model_env("TRANSLATOR_PRECISION_MODEL", default_model),
        ),
    ]


def model_env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def model_tier_by_id(tier_id: str | None) -> ModelTier:
    normalized = normalize_model_tier_id(tier_id)
    for tier in configured_model_tiers():
        if tier.tier_id == normalized:
            return tier
    raise ValueError(f"Unsupported model tier: {tier_id}")


def normalize_model_tier_id(value: str | None) -> str:
    normalized = str(value or DEFAULT_MODEL_TIER_ID).strip().lower().replace("-", "_")
    aliases = {
        "cheap": "quick_draft",
        "draft": "quick_draft",
        "fast": "quick_draft",
        "standard": "balanced",
        "moderate": "balanced",
        "best": "precision",
        "pro": "precision",
        "premium": "precision",
    }
    return aliases.get(normalized, normalized or DEFAULT_MODEL_TIER_ID)


def public_model_tiers() -> list[dict[str, object]]:
    return [tier.public_dict() for tier in configured_model_tiers()]


def credits_per_1k_tokens() -> float:
    return max(
        0.0,
        float_env("TRANSLATOR_CREDITS_PER_1K_TOKENS", DEFAULT_CREDITS_PER_1K_TOKENS),
    )


def signup_credits() -> int:
    return max(0, int_env("TRANSLATOR_SIGNUP_CREDITS", DEFAULT_SIGNUP_CREDITS))


def credits_for_token_estimate(estimated_total_tokens: int, tier_id: str | None) -> int:
    tokens = max(0, int(estimated_total_tokens or 0))
    if tokens <= 0:
        return 0
    tier = model_tier_by_id(tier_id)
    raw_credits = (tokens / 1000.0) * credits_per_1k_tokens() * tier.credit_multiplier
    return max(1, int(math.ceil(raw_credits)))


def quote_from_estimate(
    estimate: object,
    tier_id: str | None,
) -> dict[str, object] | None:
    if not isinstance(estimate, dict):
        return None
    tier = model_tier_by_id(tier_id)
    estimated_credits = credits_for_token_estimate(
        int(estimate.get("estimated_total_tokens", 0) or 0),
        tier.tier_id,
    )
    return {
        "model_tier": tier.public_dict(),
        "estimated_credits": estimated_credits,
        "word_count": int(estimate.get("word_count", 0) or 0),
        "chunk_count": int(estimate.get("chunk_count", 0) or 0),
        "estimated_total_tokens": int(estimate.get("estimated_total_tokens", 0) or 0),
    }


def credits_for_usage(usage: object, tier_id: str | None) -> int:
    if not isinstance(usage, dict):
        return 0
    return credits_for_token_estimate(
        int(usage.get("estimated_total_tokens", 0) or 0),
        tier_id,
    )


def credit_packages() -> list[dict[str, object]]:
    return [
        {
            "package_id": "starter_500",
            "name": "Starter",
            "credits": 500,
            "amount_cents": 500,
            "currency": "USD",
        },
        {
            "package_id": "builder_1200",
            "name": "Builder",
            "credits": 1200,
            "amount_cents": 1000,
            "currency": "USD",
        },
        {
            "package_id": "studio_3000",
            "name": "Studio",
            "credits": 3000,
            "amount_cents": 2500,
            "currency": "USD",
        },
    ]


def credit_package_by_id(package_id: str) -> dict[str, object]:
    for package in credit_packages():
        if package["package_id"] == package_id:
            return dict(package)
    raise ValueError(f"Unknown credit package: {package_id}")


def float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return max(0.0, value)


def int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError:
        return default


def compact_ledger_entry(entry: dict[str, Any]) -> dict[str, object]:
    return {
        "entry_id": entry.get("entry_id"),
        "entry_type": entry.get("entry_type"),
        "credit_delta": int(entry.get("credit_delta", 0) or 0),
        "credits": int(entry.get("credits", 0) or 0),
        "status": entry.get("status"),
        "job_id": entry.get("job_id"),
        "order_id": entry.get("order_id"),
        "model_tier": entry.get("model_tier"),
        "created_at": entry.get("created_at"),
    }
