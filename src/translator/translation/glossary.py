from __future__ import annotations

import json
import re
from typing import Any


VALID_PRIORITIES = {"low", "medium", "high"}
PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object, tolerating fences or short surrounding prose."""

    cleaned_text = text.strip()
    fence_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        cleaned_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    candidates = [cleaned_text]
    if fence_match:
        candidates.insert(0, fence_match.group(1).strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = _parse_embedded_json_object(candidate)
        if isinstance(parsed, dict):
            return parsed

    return None


def extract_glossary_entries(output: str, chunk_id: str) -> list[dict[str, Any]]:
    parsed = parse_json_object(output)
    if not parsed:
        return []

    raw_entries = parsed.get("glossary", [])
    if not isinstance(raw_entries, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        entry = sanitize_glossary_entry(raw_entry, chunk_id=chunk_id)
        if entry:
            entries.append(entry)
    return entries


def sanitize_glossary_entry(
    raw_entry: Any,
    chunk_id: str,
) -> dict[str, Any] | None:
    if not isinstance(raw_entry, dict):
        return None

    source_terms = _string_list(raw_entry.get("source_terms"))
    if not source_terms and isinstance(raw_entry.get("source_term"), str):
        source_terms = [raw_entry["source_term"]]

    target_terms = _string_list(raw_entry.get("target_terms"))
    if not target_terms and isinstance(raw_entry.get("target_term"), str):
        target_terms = [raw_entry["target_term"]]

    source_terms = _unique_terms(source_terms)
    target_terms = _unique_terms(target_terms)
    if not source_terms or not target_terms:
        return None

    preferred_target = str(raw_entry.get("preferred_target", "")).strip()
    if not preferred_target:
        preferred_target = target_terms[0]
    if preferred_target and not _contains_term(target_terms, preferred_target):
        target_terms.insert(0, preferred_target)

    priority = str(raw_entry.get("priority", "medium")).strip().lower()
    if priority not in VALID_PRIORITIES:
        priority = "medium"

    category = str(raw_entry.get("category", "other")).strip().lower() or "other"
    reason = str(raw_entry.get("reason", "")).strip()

    return {
        "source_terms": source_terms,
        "target_terms": target_terms,
        "preferred_target": preferred_target,
        "category": category,
        "priority": priority,
        "usage_count": 1,
        "reason": reason,
        "first_seen_chunk_id": chunk_id,
        "last_seen_chunk_id": chunk_id,
    }


def merge_glossary_entries(
    glossary: list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
    chunk_id: str,
) -> list[dict[str, Any]]:
    merged = [dict(entry) for entry in glossary]

    for incoming in new_entries:
        entry = sanitize_glossary_entry(incoming, chunk_id=chunk_id)
        if not entry:
            continue

        existing = _find_existing_entry(merged, entry["source_terms"])
        if existing is None:
            entry["entry_id"] = _next_entry_id(merged)
            merged.append(entry)
            continue

        existing["source_terms"] = _unique_terms(
            [*existing.get("source_terms", []), *entry["source_terms"]]
        )
        existing["target_terms"] = _unique_terms(
            [*existing.get("target_terms", []), *entry["target_terms"]]
        )

        if not existing.get("preferred_target"):
            existing["preferred_target"] = entry["preferred_target"]
        if not _contains_term(existing["target_terms"], existing["preferred_target"]):
            existing["target_terms"].insert(0, existing["preferred_target"])

        existing["priority"] = _higher_priority(
            str(existing.get("priority", "medium")),
            str(entry.get("priority", "medium")),
        )
        if existing.get("category") in (None, "", "other") and entry.get("category"):
            existing["category"] = entry["category"]
        if not existing.get("reason") and entry.get("reason"):
            existing["reason"] = entry["reason"]

        existing["usage_count"] = int(existing.get("usage_count", 0)) + 1
        existing["last_seen_chunk_id"] = chunk_id

    return merged


def glossary_for_chunk(
    glossary: list[dict[str, Any]],
    chunk_text: str,
) -> list[dict[str, Any]]:
    relevant_entries = []
    for entry in glossary:
        priority = str(entry.get("priority", "medium")).lower()
        source_terms = _string_list(entry.get("source_terms"))
        is_relevant = priority == "high" or any(
            term_appears_in_text(term, chunk_text) for term in source_terms
        )
        if is_relevant:
            relevant_entries.append(compact_glossary_entry(entry))
    return relevant_entries


def compact_glossary_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_terms": _string_list(entry.get("source_terms")),
        "target_terms": _string_list(entry.get("target_terms")),
        "preferred_target": str(entry.get("preferred_target", "")).strip(),
        "category": str(entry.get("category", "other")).strip() or "other",
        "priority": str(entry.get("priority", "medium")).strip() or "medium",
        "reason": str(entry.get("reason", "")).strip(),
    }


def term_appears_in_text(term: str, text: str) -> bool:
    term = _normalize_matching_text(term).strip()
    if not term:
        return False

    text = _normalize_matching_text(text)
    prefix = r"(?<!\w)" if term[0].isalnum() else ""
    suffix = r"(?!\w)" if term[-1].isalnum() else ""
    escaped_parts = [
        re.escape(part)
        for part in re.split(r"\s+", term)
        if part
    ]
    body = r"\s+".join(escaped_parts)
    pattern = f"{prefix}{body}{suffix}"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def normalize_term(term: str) -> str:
    term = _normalize_matching_text(term)
    normalized = re.sub(r"\s+", " ", term.strip().casefold())
    return normalized.strip(" \t\r\n\"'.,;:!?()[]{}")


def _parse_embedded_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_matching_text(text: str) -> str:
    return text.replace("\u00ad", "").replace("\u00a0", " ")


def _find_existing_entry(
    glossary: list[dict[str, Any]],
    source_terms: list[str],
) -> dict[str, Any] | None:
    incoming_terms = {normalize_term(term) for term in source_terms}
    for entry in glossary:
        existing_terms = {
            normalize_term(term) for term in _string_list(entry.get("source_terms"))
        }
        if incoming_terms & existing_terms:
            return entry
    return None


def _next_entry_id(glossary: list[dict[str, Any]]) -> str:
    return f"g{len(glossary) + 1:04d}"


def _higher_priority(left: str, right: str) -> str:
    left = left if left in VALID_PRIORITIES else "medium"
    right = right if right in VALID_PRIORITIES else "medium"
    return left if PRIORITY_RANK[left] >= PRIORITY_RANK[right] else right


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        values = []
    return [item.strip() for item in values if item.strip()]


def _unique_terms(terms: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = normalize_term(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(term.strip())
    return unique


def _contains_term(terms: list[str], term: str) -> bool:
    normalized_term = normalize_term(term)
    return any(normalize_term(existing_term) == normalized_term for existing_term in terms)
