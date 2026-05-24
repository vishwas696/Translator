from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from typing import Callable, Protocol

from dotenv import load_dotenv

from translator.storage.local import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_SECTION_TARGET_WORDS,
    LocalDocumentStore,
    REST_TRANSLATION_CHUNK_WORDS,
    compact_preview,
    count_words,
    find_section,
    last_translated_section_id,
    next_section_id,
    section_block_ids,
    section_status,
    translatable_blocks,
    translation_cost_estimate,
    translation_cursor,
)
from translator.translation.glossary import extract_glossary_entries, glossary_for_chunk, merge_glossary_entries
from translator.translation.chunker import TranslationChunk
from translator.translation.core import (
    block_items_for_chunk,
    build_glossary_prompt,
    build_static_translation_brief,
    build_translation_prompt,
    collect_block_translation_payloads,
    paragraph_id_to_block_map,
    parse_translation_output,
)


DEFAULT_MODEL_RETRIES = 0
DEFAULT_RETRY_BASE_DELAY_SECONDS = 2.0
DEFAULT_MODEL_TIMEOUT_SECONDS = 0.0
DEFAULT_GEMINI_MODEL = "gemini/gemini-3.0-flash"
DEFAULT_NVIDIA_MODEL = "nvidia_nim/nvidia/nemotron-3-super-120b-a12b"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NoNextSectionError(RuntimeError):
    pass


class TranslationClient(Protocol):
    async def translate(self, prompt: str) -> str:
        ...

    async def curate_glossary(self, prompt: str) -> str:
        ...


@dataclass(frozen=True)
class TranslateNextSettings:
    target_language: str
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    document_type: str = "general"
    content_form: str = "book"
    context_sections: int = 3


class DevFakeSectionTranslator:
    """Dev-only translator for frontend smoke tests without model calls."""

    async def translate(self, prompt: str) -> str:
        target_language = dev_fake_target_language(prompt)
        chunk = dev_fake_json_section(prompt, "Current chunk metadata:", "Current document blocks:")
        blocks = dev_fake_json_section(prompt, "Current document blocks:", "Current chunk text:")
        if not isinstance(chunk, dict):
            chunk = {}
        if not isinstance(blocks, list):
            blocks = []

        block_translations = []
        translated_parts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            source_text = str(block.get("source_text") or block.get("text") or "").strip()
            translated_text = f"[DEV {target_language}] {source_text}".strip()
            translated_parts.append(translated_text)
            block_translation: dict[str, object] = {
                "block_id": block.get("block_id"),
                "paragraph_id": block.get("paragraph_id"),
                "translated_text": translated_text,
            }
            if (
                block.get("block_type") == "table"
                and block.get("is_layout_table") is not True
                and isinstance(block.get("table_rows"), list)
            ):
                table_rows = dev_fake_translate_table_rows(
                    rows=block["table_rows"],
                    target_language=target_language,
                )
                block_translation["table_rows"] = table_rows
                block_translation["translated_text"] = dev_fake_render_table_rows(table_rows)
            block_translations.append(block_translation)

        return json.dumps(
            {
                "chunk_id": chunk.get("chunk_id", "dev_fake_chunk"),
                "paragraph_ids": chunk.get("paragraph_ids", []),
                "translated_text": "\n\n".join(translated_parts),
                "block_translations": block_translations,
                "notes": ["Dev fake translator output. Do not use for production translation."],
            },
            ensure_ascii=False,
        )

    async def curate_glossary(self, prompt: str) -> str:
        return json.dumps({"glossary": [], "rejected_terms": []}, ensure_ascii=False)


def dev_fake_target_language(prompt: str) -> str:
    marker = " into "
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("Translate the current chunk") and marker in stripped:
            return stripped.rsplit(marker, 1)[1].rstrip(".").strip() or "target"
    return "target"


def dev_fake_json_section(prompt: str, marker: str, next_marker: str) -> object:
    try:
        start = prompt.index(marker) + len(marker)
        end = prompt.index(next_marker, start)
        return json.loads(prompt[start:end].strip())
    except (ValueError, json.JSONDecodeError):
        return {}


def dev_fake_translate_table_rows(rows: list[object], target_language: str) -> list[list[str]]:
    translated_rows = []
    for row in rows:
        if not isinstance(row, list):
            translated_rows.append([f"[DEV {target_language}] {row}"])
            continue
        translated_rows.append([f"[DEV {target_language}] {cell}" for cell in row])
    return translated_rows


def dev_fake_render_table_rows(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(str(cell) for cell in row) for row in rows)


class AgentSectionTranslator:
    def __init__(
        self,
        retries: int | None = None,
        base_delay_seconds: float | None = None,
        timeout_seconds: float | None = None,
        model_name: str | None = None,
    ) -> None:
        self.retries = (
            retries
            if retries is not None
            else int_env("TRANSLATOR_MODEL_RETRIES", DEFAULT_MODEL_RETRIES)
        )
        self.base_delay_seconds = (
            base_delay_seconds
            if base_delay_seconds is not None
            else float_env(
                "TRANSLATOR_RETRY_BASE_DELAY_SECONDS",
                DEFAULT_RETRY_BASE_DELAY_SECONDS,
            )
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float_env(
                "TRANSLATOR_MODEL_TIMEOUT_SECONDS",
                DEFAULT_MODEL_TIMEOUT_SECONDS,
            )
        )
        self.model_name = model_name
        self._agents: tuple[object, object] | None = None

    async def translate(self, prompt: str) -> str:
        translator, _ = self._ensure_agents()
        result = await run_agent_with_retries(
            agent=translator,
            prompt=prompt,
            label="backend section translation",
            retries=self.retries,
            base_delay_seconds=self.base_delay_seconds,
            timeout_seconds=self.timeout_seconds,
        )
        return str(result.final_output)

    async def curate_glossary(self, prompt: str) -> str:
        _, glossary_curator = self._ensure_agents()
        result = await run_agent_with_retries(
            agent=glossary_curator,
            prompt=prompt,
            label="backend glossary update",
            retries=self.retries,
            base_delay_seconds=self.base_delay_seconds,
            timeout_seconds=self.timeout_seconds,
        )
        return str(result.final_output)

    def _ensure_agents(self) -> tuple[object, object]:
        if self._agents is not None:
            return self._agents

        load_dotenv()
        from agents import Agent, set_tracing_disabled
        from agents.extensions.models.litellm_model import LitellmModel

        model_name, api_key, base_url = configured_model_settings(self.model_name)
        set_tracing_disabled(True)

        model = LitellmModel(model=model_name, api_key=api_key, base_url=base_url)
        translator = Agent(
            name="Backend Section Translator",
            instructions=(
                "You translate source content faithfully for publication. Preserve "
                "meaning, structure, tone, names, formatting markers, and requested "
                "JSON schemas. Do not summarize. Return only valid JSON."
            ),
            model=model,
        )
        glossary_curator = Agent(
            name="Backend Glossary Curator",
            instructions=(
                "You create compact translation glossaries from source and translated "
                "text pairs. Keep only consistency-critical terms. Return only valid JSON."
            ),
            model=model,
        )
        self._agents = (translator, glossary_curator)
        return self._agents


async def translate_next_section(
    store: LocalDocumentStore,
    document_id: str,
    settings: TranslateNextSettings,
    translator: TranslationClient,
) -> dict[str, object]:
    sections = store.load_sections(document_id)
    translations = store.load_translations(document_id)
    cursor = translation_cursor(sections, translations)
    target_section_id = next_section_id(sections, cursor)
    if target_section_id is None:
        raise NoNextSectionError("All sections are already translated.")

    section = find_section(sections, target_section_id)
    if section is None:
        raise NoNextSectionError("The next section could not be found.")
    if section_status(section, translations) == "partial":
        raise NoNextSectionError(
            "The next section is partially translated; resolve or reset it before continuing."
        )

    return await translate_section(
        store=store,
        document_id=document_id,
        section=section,
        settings=settings,
        translator=translator,
        mode="translate_next",
    )


async def retranslate_last_section(
    store: LocalDocumentStore,
    document_id: str,
    settings: TranslateNextSettings,
    translator: TranslationClient,
) -> dict[str, object]:
    sections = store.load_sections(document_id)
    translations = store.load_translations(document_id)
    cursor = translation_cursor(sections, translations)
    target_section_id = last_translated_section_id(sections, cursor)
    if target_section_id is None:
        raise NoNextSectionError("No translated section is available to retranslate.")

    section = find_section(sections, target_section_id)
    if section is None:
        raise NoNextSectionError("The last translated section could not be found.")

    return await translate_section(
        store=store,
        document_id=document_id,
        section=section,
        settings=settings,
        translator=translator,
        mode="retranslate_last",
    )


async def translate_rest_of_document(
    store: LocalDocumentStore,
    document_id: str,
    settings: TranslateNextSettings,
    translator: TranslationClient,
    target_words_per_chunk: int = REST_TRANSLATION_CHUNK_WORDS,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    parsed_document = store.load_parsed_document(document_id)
    translations = store.load_translations(document_id)
    bulk_sections = build_remaining_bulk_sections(
        parsed_document=parsed_document,
        translations=translations,
        target_words_per_chunk=target_words_per_chunk,
    )
    if not bulk_sections:
        raise NoNextSectionError("All sections are already translated.")

    sections = store.load_sections(document_id)
    cursor = translation_cursor(sections, translations)
    section_translations = store.load_section_translations(document_id)
    context_chunks = previous_context_chunks(
        sections=sections,
        section_translations=section_translations,
        before_index=cursor + 1,
        limit=max(0, settings.context_sections),
    )

    translated_chunks: list[dict[str, object]] = []
    usage_items: list[dict[str, object]] = []
    glossary_errors: list[str] = []
    total_blocks = 0
    for index, bulk_section in enumerate(bulk_sections, start=1):
        if progress_callback is not None:
            progress = 10 + int(((index - 1) / len(bulk_sections)) * 80)
            progress_callback(
                progress,
                f"Translating rest chunk {index} of {len(bulk_sections)}",
            )

        result = await translate_section(
            store=store,
            document_id=document_id,
            section=bulk_section,
            settings=settings,
            translator=translator,
            mode="translate_rest",
            previous_chunks_override=context_chunks[-settings.context_sections :]
            if settings.context_sections > 0
            else [],
            include_translated_chunk=True,
            usage_chunk_size_words=target_words_per_chunk,
        )
        translated_chunk = result.get("translated_chunk")
        if isinstance(translated_chunk, dict):
            context_chunks.append(translated_chunk)
            translated_chunks.append(translated_chunk)
        glossary_error = str(result.get("glossary_error", ""))
        if glossary_error:
            glossary_errors.append(glossary_error)
        total_blocks += int(result.get("translated_block_count", 0))
        usage = result.get("usage")
        if isinstance(usage, dict):
            usage_items.append(usage)

    updated_sections = store.sections_response(document_id)
    if progress_callback is not None:
        progress_callback(95, "Finishing rest-of-document translation")

    return {
        "document_id": document_id,
        "mode": "translate_rest",
        "status": "translated",
        "translated_chunk_count": len(translated_chunks),
        "translated_block_count": total_blocks,
        "translation_cursor": updated_sections["translation_cursor"],
        "next_section_id": updated_sections["next_section_id"],
        "remaining_estimate": updated_sections["remaining_estimate"],
        "usage": combine_usage_estimates(
            usage_items=usage_items,
            mode="translate_rest",
            translated_block_count=total_blocks,
            chunk_size_words=target_words_per_chunk,
        ),
        "glossary_error_count": len(glossary_errors),
        "glossary_errors": glossary_errors,
    }


async def translate_section(
    store: LocalDocumentStore,
    document_id: str,
    section: dict[str, object],
    settings: TranslateNextSettings,
    translator: TranslationClient,
    mode: str,
    previous_chunks_override: list[dict[str, object]] | None = None,
    include_translated_chunk: bool = False,
    usage_chunk_size_words: int | None = None,
) -> dict[str, object]:
    parsed_document = store.load_parsed_document(document_id)
    sections = store.load_sections(document_id)
    translations = store.load_translations(document_id)
    target_section_id = str(section.get("section_id", ""))
    paragraph_blocks = paragraph_id_to_block_map(parsed_document)
    chunk = chunk_for_section(section, paragraph_blocks)
    chunk_blocks = block_items_for_chunk(chunk, paragraph_blocks)
    if not chunk_blocks:
        raise NoNextSectionError("The selected section has no translatable blocks.")

    brief = build_static_translation_brief(
        target_language=settings.target_language,
        source_language=settings.source_language,
        document_type=settings.document_type,
        content_form=settings.content_form,
    )
    glossary = store.load_glossary(document_id)
    relevant_glossary = glossary_for_chunk(glossary, chunk.text)
    previous_chunks = (
        previous_chunks_override
        if previous_chunks_override is not None
        else previous_context_chunks(
            sections=sections,
            section_translations=store.load_section_translations(document_id),
            before_index=int(section.get("index", 0)),
            limit=max(0, settings.context_sections),
        )
    )

    prompt = build_translation_prompt(
        target_language=settings.target_language,
        brief=brief,
        chunk=chunk,
        chunk_blocks=chunk_blocks,
        previous_chunks=previous_chunks,
        glossary_entries=relevant_glossary,
        source_language=settings.source_language,
    )
    raw_translation = await translator.translate(prompt)
    parsed_translation = parse_translation_output(
        output=raw_translation,
        chunk=chunk,
        chunk_blocks=chunk_blocks,
    )
    translated_chunk = {
        "section_id": target_section_id,
        "mode": mode,
        "chunk": chunk.to_dict(),
        "glossary_used": relevant_glossary,
        "translation": parsed_translation,
        "raw_model_output": raw_translation,
    }

    block_payloads = collect_block_translation_payloads([translated_chunk])
    translations.update(block_payloads)
    store.save_translations(document_id, translations)
    store.save_section_translation(document_id, target_section_id, translated_chunk)

    glossary_error = ""
    new_glossary_entries: list[dict[str, object]] = []
    try:
        glossary_prompt = build_glossary_prompt(
            target_language=settings.target_language,
            brief=brief,
            chunk=chunk,
            translated_text=str(parsed_translation.get("translated_text", "")),
            source_language=settings.source_language,
        )
        raw_glossary = await translator.curate_glossary(glossary_prompt)
        new_glossary_entries = extract_glossary_entries(
            output=raw_glossary,
            chunk_id=chunk.chunk_id,
        )
        glossary = merge_glossary_entries(
            glossary=glossary,
            new_entries=new_glossary_entries,
            chunk_id=chunk.chunk_id,
        )
        store.save_glossary(document_id, glossary)
    except Exception as exc:
        glossary_error = f"{type(exc).__name__}: {exc}"

    updated_sections = store.sections_response(document_id)
    result = {
        "document_id": document_id,
        "section_id": target_section_id,
        "mode": mode,
        "status": "translated",
        "translation_cursor": updated_sections["translation_cursor"],
        "next_section_id": updated_sections["next_section_id"],
        "translated_block_count": len(block_payloads),
        "usage": usage_estimate_for_section(
            mode=mode,
            section_id=target_section_id,
            section=section,
            chunk=chunk,
            translated_block_count=len(block_payloads),
            chunk_size_words=usage_chunk_size_words,
        ),
        "translation": parsed_translation,
        "new_glossary_entries": new_glossary_entries,
        "glossary_error": glossary_error,
    }
    if include_translated_chunk:
        result["translated_chunk"] = translated_chunk
    return result


def build_remaining_bulk_sections(
    parsed_document,
    translations: dict[str, object],
    target_words_per_chunk: int = REST_TRANSLATION_CHUNK_WORDS,
) -> list[dict[str, object]]:
    target_words = max(1, int(target_words_per_chunk))
    sections: list[dict[str, object]] = []
    current_blocks = []
    current_word_count = 0

    for block in translatable_blocks(parsed_document):
        if block.block_id in translations:
            continue
        block_word_count = count_words(block.text)
        if current_blocks and current_word_count + block_word_count > target_words:
            sections.append(make_bulk_section(len(sections) + 1, current_blocks))
            current_blocks = []
            current_word_count = 0

        current_blocks.append(block)
        current_word_count += block_word_count

    if current_blocks:
        sections.append(make_bulk_section(len(sections) + 1, current_blocks))

    return sections


def make_bulk_section(index: int, blocks: list[object]) -> dict[str, object]:
    source_text = "\n\n".join(
        str(getattr(block, "text", "")).strip()
        for block in blocks
        if str(getattr(block, "text", "")).strip()
    )
    return {
        "section_id": f"rest_{index:04d}",
        "index": 10_000 + index,
        "block_ids": [str(getattr(block, "block_id", "")) for block in blocks],
        "word_count": sum(count_words(str(getattr(block, "text", ""))) for block in blocks),
        "block_count": len(blocks),
        "preview": compact_preview(source_text),
    }


def usage_estimate_for_section(
    mode: str,
    section_id: str,
    section: dict[str, object],
    chunk: TranslationChunk,
    translated_block_count: int,
    chunk_size_words: int | None = None,
) -> dict[str, object]:
    effective_chunk_size = chunk_size_words
    if effective_chunk_size is None:
        effective_chunk_size = int(section.get("word_count", 0)) or DEFAULT_SECTION_TARGET_WORDS
    estimate = translation_cost_estimate(
        word_count=chunk.source_word_count,
        chunk_count=1,
        chunk_size_words=effective_chunk_size,
    )
    return {
        "mode": mode,
        "section_id": section_id,
        "translated_block_count": translated_block_count,
        **estimate,
    }


def combine_usage_estimates(
    usage_items: list[dict[str, object]],
    mode: str,
    translated_block_count: int,
    chunk_size_words: int,
) -> dict[str, object]:
    word_count = sum(int(item.get("word_count", 0) or 0) for item in usage_items)
    estimated_cost_usd = round(
        sum(float(item.get("estimated_cost_usd", 0.0) or 0.0) for item in usage_items),
        6,
    )
    return {
        "mode": mode,
        "word_count": word_count,
        "chunk_count": sum(int(item.get("chunk_count", 0) or 0) for item in usage_items),
        "chunk_size_words": chunk_size_words,
        "translated_block_count": translated_block_count,
        "estimated_input_tokens": sum(
            int(item.get("estimated_input_tokens", 0) or 0) for item in usage_items
        ),
        "estimated_output_tokens": sum(
            int(item.get("estimated_output_tokens", 0) or 0) for item in usage_items
        ),
        "estimated_prompt_overhead_tokens": sum(
            int(item.get("estimated_prompt_overhead_tokens", 0) or 0)
            for item in usage_items
        ),
        "estimated_total_tokens": sum(
            int(item.get("estimated_total_tokens", 0) or 0) for item in usage_items
        ),
        "estimated_cost_usd": estimated_cost_usd,
        "estimated_cost_per_word_usd": round(estimated_cost_usd / word_count, 8)
        if word_count
        else 0.0,
    }


def chunk_for_section(
    section: dict[str, object],
    paragraph_blocks: dict[str, object],
) -> TranslationChunk:
    block_id_to_paragraph_id = {
        block.block_id: paragraph_id
        for paragraph_id, block in paragraph_blocks.items()
    }
    paragraph_ids = [
        block_id_to_paragraph_id[block_id]
        for block_id in section_block_ids(section)
        if block_id in block_id_to_paragraph_id
    ]
    section_text = "\n\n".join(
        paragraph_blocks[paragraph_id].text.strip()
        for paragraph_id in paragraph_ids
        if paragraph_blocks[paragraph_id].text.strip()
    )
    return TranslationChunk(
        chunk_id=str(section.get("section_id", "")),
        paragraph_ids=paragraph_ids,
        text=section_text,
        source_word_count=count_words(section_text),
        contains_partial_paragraph=False,
        starts_paragraph=True,
        continues_paragraph=False,
        ends_paragraph=True,
    )


def previous_context_chunks(
    sections: list[dict[str, object]],
    section_translations: dict[str, dict[str, object]],
    before_index: int,
    limit: int,
) -> list[dict[str, object]]:
    if limit <= 0:
        return []

    previous_sections = [
        section
        for section in sections
        if int(section.get("index", 0)) < before_index
    ]
    previous_sections.sort(key=lambda section: int(section.get("index", 0)))
    context: list[dict[str, object]] = []
    for section in previous_sections[-limit:]:
        section_id = str(section.get("section_id", ""))
        translated_chunk = section_translations.get(section_id)
        if translated_chunk:
            context.append(translated_chunk)
    return context


async def run_agent_with_retries(
    agent: object,
    prompt: str,
    label: str,
    retries: int,
    base_delay_seconds: float,
    timeout_seconds: float,
):
    from agents import Runner

    attempts = max(1, int(retries) + 1)
    delay = max(0.0, float(base_delay_seconds))
    timeout = float(timeout_seconds)

    for attempt in range(1, attempts + 1):
        try:
            if timeout > 0:
                return await asyncio.wait_for(Runner.run(agent, prompt), timeout=timeout)
            return await Runner.run(agent, prompt)
        except Exception as exc:
            if attempt >= attempts or not is_retryable_model_error(exc):
                raise
            if delay:
                await asyncio.sleep(delay)
            delay *= 2

    raise RuntimeError(f"{label} failed unexpectedly without returning a result.")


def is_retryable_model_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    permanent_markers = [
        "401",
        "403",
        "404",
        "api key not valid",
        "authentication",
        "bad request",
        "invalid api key",
        "invalid model",
        "model not found",
        "permission denied",
        "quota",
        "unauthorized",
    ]
    if any(marker in message for marker in permanent_markers):
        return False
    transient_markers = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "api connection",
        "connection reset",
        "dns",
        "getaddrinfo",
        "internal server error",
        "rate limit",
        "resource exhausted",
        "service unavailable",
        "timeout",
        "timed out",
        "try again",
    ]
    return any(marker in message for marker in transient_markers)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def configured_model_settings(model_name_override: str | None = None) -> tuple[str, str, str | None]:
    google_api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    nvidia_api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    requested_provider = os.getenv("MODEL_PROVIDER", "").strip().lower()
    override = (model_name_override or "").strip()
    if override.startswith("gemini/"):
        provider = "gemini"
    elif override.startswith("nvidia_nim/") or override.startswith("nvidia/"):
        provider = "nvidia"
    else:
        provider = requested_provider or ("gemini" if google_api_key else "nvidia")

    if provider in {"nvidia", "nvidia_nim", "nim"}:
        model_name = override or os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL).strip()
        base_url = os.getenv("NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL).strip()
        if not nvidia_api_key or nvidia_api_key == "put-your-nvidia-api-key-here":
            raise RuntimeError("NVIDIA_API_KEY is required for backend translation.")
        os.environ.setdefault("NVIDIA_NIM_API_KEY", nvidia_api_key)
        return model_name, nvidia_api_key, base_url

    if provider in {"gemini", "google"}:
        model_name = override or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        if not google_api_key or google_api_key == "put-your-google-api-key-here":
            raise RuntimeError("GOOGLE_API_KEY is required for backend translation.")
        os.environ.setdefault("GEMINI_API_KEY", google_api_key)
        return model_name, google_api_key, None

    raise RuntimeError("Unsupported MODEL_PROVIDER. Use MODEL_PROVIDER=nvidia or MODEL_PROVIDER=gemini.")
