from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
import json
import os
from pathlib import Path
import sys
from textwrap import dedent
from typing import Any

from dotenv import load_dotenv

from agents import Agent, Runner, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel

from document_adapters import load_document
from document_model import DocumentBlock, ParsedDocument, parsed_document_from_text
from document_writers import default_translated_document_path, write_translated_document
from docx2python_enrichment import enrich_docx_with_docx2python
from glossary import extract_glossary_entries, glossary_for_chunk, merge_glossary_entries
from prompt_guidance import (
    CONTENT_FORM_GUIDANCE,
    DOCUMENT_TYPE_GUIDANCE,
)
from translation_chunker import TranslationChunk, chunk_manuscript
from translation_core import (
    block_items_for_chunk,
    build_glossary_prompt,
    build_review_prompt,
    build_revision_prompt,
    build_static_review_brief,
    build_static_translation_brief,
    build_translation_prompt,
    collect_block_translation_payloads,
    collect_block_translations,
    parse_translation_output,
    paragraph_id_to_block_map,
    reconstruct_translation,
)

load_dotenv()

DEFAULT_MODEL_RETRIES = 2
DEFAULT_RETRY_BASE_DELAY_SECONDS = 2.0
DEFAULT_MODEL_TIMEOUT_SECONDS = 180.0
DEFAULT_GEMINI_MODEL = "gemini/gemini-3.1-pro"
DEFAULT_NVIDIA_MODEL = "nvidia_nim/nvidia/nemotron-3-super-120b-a12b"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

SAMPLE_MANUSCRIPT = dedent(
    """
    Chapter 1: The Map in the Attic

    Mira had promised herself she would never open the attic door again.
    It was the sort of promise people make when they are ten years old and
    still believe a door can remember betrayal.

    But the rain had found a way through the roof, and by midnight a thin
    silver line of water was running down the hallway wall. So Mira climbed
    the ladder with a candle in one hand and the old brass key in the other.

    Under a folded winter coat, she found the map.
    """
).strip()


def configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def safe_print(*values: object, sep: str = " ", end: str = "\n") -> None:
    text = sep.join(str(value) for value in values)
    try:
        print(text, end=end)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="backslashreplace").decode(
            encoding,
            errors="replace",
        )
        safe_end = end.encode(encoding, errors="backslashreplace").decode(
            encoding,
            errors="replace",
        )
        print(safe_text, end=safe_end)


def compact_error_message(exc: Exception, max_length: int = 240) -> str:
    message = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    if len(message) <= max_length:
        return message
    return f"{message[: max_length - 3]}..."


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
        "invalid request",
        "model not found",
        "not found",
        "permission denied",
        "quota",
        "unauthorized",
    ]
    if any(marker in message for marker in permanent_markers):
        return False

    transient_markers = [
        "429",
        "502",
        "503",
        "504",
        "500",
        "api connection",
        "apiconnectionerror",
        "connection reset",
        "connecterror",
        "dns",
        "getaddrinfo",
        "internal server error",
        "internalservererror",
        "internal error",
        "rate limit",
        "resource exhausted",
        "server disconnected",
        "service unavailable",
        "temporarily",
        "timeout",
        "timed out",
        "timeouterror",
        "try again",
    ]
    return any(marker in message for marker in transient_markers)


async def run_agent_with_retries(
    agent: Agent,
    prompt: str,
    label: str,
    retries: int = DEFAULT_MODEL_RETRIES,
    base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
    timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
):
    attempts = max(1, int(retries) + 1)
    delay = max(0.0, float(base_delay_seconds))
    timeout = max(1.0, float(timeout_seconds))

    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(Runner.run(agent, prompt), timeout=timeout)
        except Exception as exc:
            if attempt >= attempts or not is_retryable_model_error(exc):
                raise

            safe_print(
                f"{label} failed with a transient model/provider error "
                f"({compact_error_message(exc)}). "
                f"Retrying in {delay:.1f}s ({attempt}/{attempts - 1})..."
            )
            if delay:
                await asyncio.sleep(delay)
            delay *= 2

    raise RuntimeError(f"{label} failed unexpectedly without returning a result.")


configure_standard_streams()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
REQUESTED_MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "").strip().lower()
MODEL_PROVIDER = REQUESTED_MODEL_PROVIDER or ("nvidia" if NVIDIA_API_KEY else "gemini")

if MODEL_PROVIDER in {"nvidia", "nvidia_nim", "nim"}:
    MODEL_PROVIDER = "nvidia"
    MODEL_NAME = os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL).strip()
    MODEL_API_KEY = NVIDIA_API_KEY
    MODEL_BASE_URL = os.getenv("NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL).strip()
    if not MODEL_API_KEY or MODEL_API_KEY == "put-your-nvidia-api-key-here":
        raise RuntimeError(
            "Add your NVIDIA API key to the .env file first: NVIDIA_API_KEY=..."
        )
elif MODEL_PROVIDER in {"gemini", "google"}:
    MODEL_PROVIDER = "gemini"
    MODEL_NAME = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    MODEL_API_KEY = GOOGLE_API_KEY
    MODEL_BASE_URL = None
    if not MODEL_API_KEY or MODEL_API_KEY == "put-your-google-api-key-here":
        raise RuntimeError(
            "Add your Google API key to the .env file first: GOOGLE_API_KEY=..."
        )
else:
    raise RuntimeError(
        "Unsupported MODEL_PROVIDER. Use MODEL_PROVIDER=nvidia or MODEL_PROVIDER=gemini."
    )

if MODEL_PROVIDER == "gemini":
    os.environ.setdefault("GEMINI_API_KEY", GOOGLE_API_KEY)
elif MODEL_PROVIDER == "nvidia":
    os.environ.setdefault("NVIDIA_NIM_API_KEY", NVIDIA_API_KEY)

set_tracing_disabled(True)


def configured_model() -> LitellmModel:
    return LitellmModel(
        model=MODEL_NAME,
        api_key=MODEL_API_KEY,
        base_url=MODEL_BASE_URL,
    )


translator = Agent(
    name="Book Translator",
    instructions=(
        "You translate source content faithfully for publication. Preserve meaning, "
        "structure, tone, names, formatting markers, and requested JSON schemas. "
        "Do not summarize. Return only valid JSON."
    ),
    model=configured_model(),
)

glossary_curator = Agent(
    name="Glossary Curator",
    instructions=(
        "You create compact translation glossaries from source and translated text "
        "pairs. Keep only consistency-critical names, places, specialized terms, "
        "recurring phrases, invented terms, and protected terms. Return only valid JSON."
    ),
    model=configured_model(),
)

reviewer = Agent(
    name="Translation Reviewer",
    instructions=(
        "You review translated content for faithfulness, tone, formatting, glossary "
        "consistency, and untranslated leftovers. Return concise concrete feedback."
    ),
    model=configured_model(),
)


async def call_run_agent_with_compat(
    *,
    agent: Agent,
    prompt: str,
    label: str,
    retries: int,
    base_delay_seconds: float,
    timeout_seconds: float,
):
    signature = inspect.signature(run_agent_with_retries)
    kwargs: dict[str, object] = {
        "agent": agent,
        "prompt": prompt,
        "label": label,
        "retries": retries,
        "base_delay_seconds": base_delay_seconds,
    }
    if "timeout_seconds" in signature.parameters:
        kwargs["timeout_seconds"] = timeout_seconds
    return await run_agent_with_retries(**kwargs)


async def revise_translated_chunks(
    chunks: list[TranslationChunk],
    first_pass_chunks: list[dict[str, object]],
    target_language: str,
    brief: str,
    reviewer_feedback: str,
    paragraph_blocks: dict[str, DocumentBlock],
    context_chunk_count: int,
    model_retries: int = DEFAULT_MODEL_RETRIES,
    retry_base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
    model_timeout_seconds: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
    glossary: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    revised_chunks: list[dict[str, object]] = []
    revision_errors: list[dict[str, str]] = []

    for index, (chunk, first_pass_chunk) in enumerate(
        zip(chunks, first_pass_chunks, strict=False),
        start=1,
    ):
        safe_print(f"Revising {chunk.chunk_id} ({index}/{len(chunks)})...")
        chunk_blocks = block_items_for_chunk(chunk, paragraph_blocks)
        first_translation = first_pass_chunk.get("translation", {})
        if not isinstance(first_translation, dict):
            first_translation = {}
        if glossary is None:
            glossary_entries = first_pass_chunk.get("glossary_used", [])
        else:
            glossary_entries = glossary_for_chunk(glossary, chunk.text)
        if not isinstance(glossary_entries, list):
            glossary_entries = []

        previous_revised_chunks = (
            revised_chunks[-context_chunk_count:] if context_chunk_count else []
        )
        revision_prompt = build_revision_prompt(
            target_language=target_language,
            brief=brief,
            chunk=chunk,
            chunk_blocks=chunk_blocks,
            first_translation=first_translation,
            reviewer_feedback=reviewer_feedback,
            previous_revised_chunks=previous_revised_chunks,
            glossary_entries=glossary_entries,
        )

        try:
            revision_result = await call_run_agent_with_compat(
                agent=translator,
                prompt=revision_prompt,
                label=f"revision for {chunk.chunk_id}",
                retries=model_retries,
                base_delay_seconds=retry_base_delay_seconds,
                timeout_seconds=model_timeout_seconds,
            )
            parsed_revision = parse_translation_output(
                output=revision_result.final_output,
                chunk=chunk,
                chunk_blocks=chunk_blocks,
                fallback_translations_by_block_id={
                    str(item.get("block_id")): item
                    for item in first_translation.get("block_translations", [])
                    if isinstance(item, dict) and str(item.get("block_id", "")).strip()
                },
            )
            revised_chunk = {
                "chunk": chunk.to_dict(),
                "glossary_used": glossary_entries,
                "translation": parsed_revision,
                "first_pass_translation": first_translation,
                "raw_model_output": first_pass_chunk.get("raw_model_output", ""),
                "raw_revision_model_output": revision_result.final_output,
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            revision_errors.append({"chunk_id": chunk.chunk_id, "error": error})
            revised_chunk = copy.deepcopy(first_pass_chunk)
            revised_chunk["revision_error"] = error
        revised_chunks.append(revised_chunk)

    return revised_chunks, {
        "enabled": True,
        "status": "completed_with_errors" if revision_errors else "completed",
        "revision_errors": revision_errors,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a sample document-translation agent workflow."
    )
    parser.add_argument("--target-language", default="Spanish")
    parser.add_argument("--source-language", default="English")
    parser.add_argument(
        "--document-type",
        choices=sorted(DOCUMENT_TYPE_GUIDANCE),
        default="general",
    )
    parser.add_argument(
        "--content-form",
        choices=sorted(CONTENT_FORM_GUIDANCE),
        default="book",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--docx2python-enrichment", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--chunk-size", type=int, default=1500)
    parser.add_argument("--context-chunks", type=int, default=3)
    parser.add_argument(
        "--revision-pass",
        dest="revision_pass",
        action="store_true",
        default=True,
        help="Run the reviewer-guided revision pass. Enabled by default.",
    )
    parser.add_argument(
        "--no-revision-pass",
        dest="revision_pass",
        action="store_false",
        help="Skip the reviewer-guided revision pass for quick/debug runs.",
    )
    parser.add_argument("--model-retries", type=int, default=DEFAULT_MODEL_RETRIES)
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=DEFAULT_RETRY_BASE_DELAY_SECONDS,
    )
    parser.add_argument(
        "--model-timeout",
        type=float,
        default=DEFAULT_MODEL_TIMEOUT_SECONDS,
    )
    args = parser.parse_args()

    parsed_document = parsed_document_from_text(
        SAMPLE_MANUSCRIPT,
        source_format="sample",
    )
    if args.input:
        parsed_document = load_document(args.input)
        if not parsed_document.blocks:
            raise RuntimeError(f"No supported content blocks found in {args.input}")

    manuscript = parsed_document.to_translation_text().strip()
    if args.input and not manuscript:
        raise RuntimeError(f"No text found in {args.input}")
    paragraph_blocks = paragraph_id_to_block_map(parsed_document)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    docx2python_enrichment_report: dict[str, object] | None = None
    if args.docx2python_enrichment:
        enrichment_result = enrich_docx_with_docx2python(parsed_document)
        parsed_document = enrichment_result.parsed_document
        docx2python_enrichment_report = enrichment_result.report
        paragraph_blocks = paragraph_id_to_block_map(parsed_document)
        (args.output_dir / "docx2python_enrichment_report.json").write_text(
            json.dumps(docx2python_enrichment_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    safe_print(f"Model provider: {MODEL_PROVIDER}")
    safe_print(f"Using model: {MODEL_NAME}")
    safe_print(f"Source language: {args.source_language}")
    safe_print(f"Target language: {args.target_language}")
    safe_print(f"Content form: {args.content_form}")
    safe_print(f"Document type: {args.document_type}")
    safe_print(f"Input format: {parsed_document.source_format}")
    safe_print(f"Document blocks: {len(parsed_document.blocks)}")
    safe_print(f"Chunk size: {args.chunk_size} source words")
    safe_print(f"Revision pass: {'enabled' if args.revision_pass else 'disabled'}")
    safe_print(
        "docx2python enrichment: "
        + (
            str(docx2python_enrichment_report.get("status", "unknown"))
            if docx2python_enrichment_report
            else "disabled"
        )
    )
    safe_print(f"Model retries: {max(0, args.model_retries)}")
    safe_print(f"Model timeout: {max(1.0, args.model_timeout):.1f}s")
    safe_print(f"Output folder: {args.output_dir.resolve()}\n")

    (args.output_dir / "document_blocks.json").write_text(
        json.dumps(parsed_document.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    chunks = chunk_manuscript(manuscript, max_words=args.chunk_size)
    if not chunks:
        raise RuntimeError("No chunks were created from the manuscript.")
    (args.output_dir / "chunk_plan.json").write_text(
        json.dumps([chunk.to_dict() for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    brief = build_static_translation_brief(
        target_language=args.target_language,
        source_language=args.source_language,
        document_type=args.document_type,
        content_form=args.content_form,
    )
    review_brief = build_static_review_brief(
        target_language=args.target_language,
        source_language=args.source_language,
        document_type=args.document_type,
        content_form=args.content_form,
    )
    (args.output_dir / "translation_brief.md").write_text(brief, encoding="utf-8")
    (args.output_dir / "review_brief.md").write_text(review_brief, encoding="utf-8")
    safe_print("=== Translation Brief ===")
    safe_print(brief)
    safe_print()
    safe_print("=== Review Brief ===")
    safe_print(review_brief)
    safe_print()

    translated_chunks: list[dict[str, object]] = []
    glossary: list[dict[str, object]] = []
    glossary_updates: list[dict[str, object]] = []
    context_chunk_count = max(0, args.context_chunks)

    for index, chunk in enumerate(chunks, start=1):
        safe_print(f"Translating {chunk.chunk_id} ({index}/{len(chunks)})...")
        previous_chunks = (
            translated_chunks[-context_chunk_count:] if context_chunk_count else []
        )
        chunk_blocks = block_items_for_chunk(chunk, paragraph_blocks)
        relevant_glossary = glossary_for_chunk(glossary, chunk.text)
        translate_prompt = build_translation_prompt(
            target_language=args.target_language,
            brief=brief,
            chunk=chunk,
            chunk_blocks=chunk_blocks,
            previous_chunks=previous_chunks,
            glossary_entries=relevant_glossary,
            source_language=args.source_language,
        )
        translation_result = await run_agent_with_retries(
            agent=translator,
            prompt=translate_prompt,
            label=f"translation for {chunk.chunk_id}",
            retries=args.model_retries,
            base_delay_seconds=args.retry_base_delay,
            timeout_seconds=args.model_timeout,
        )
        parsed_translation = parse_translation_output(
            output=translation_result.final_output,
            chunk=chunk,
            chunk_blocks=chunk_blocks,
        )
        translated_chunk = {
            "chunk": chunk.to_dict(),
            "glossary_used": relevant_glossary,
            "translation": parsed_translation,
            "raw_model_output": translation_result.final_output,
        }
        translated_chunks.append(translated_chunk)

        glossary_prompt = build_glossary_prompt(
            target_language=args.target_language,
            brief=brief,
            chunk=chunk,
            translated_text=str(parsed_translation.get("translated_text", "")),
            source_language=args.source_language,
        )
        try:
            glossary_result = await run_agent_with_retries(
                agent=glossary_curator,
                prompt=glossary_prompt,
                label=f"glossary update for {chunk.chunk_id}",
                retries=args.model_retries,
                base_delay_seconds=args.retry_base_delay,
                timeout_seconds=args.model_timeout,
            )
            new_glossary_entries = extract_glossary_entries(
                output=glossary_result.final_output,
                chunk_id=chunk.chunk_id,
            )
            glossary = merge_glossary_entries(
                glossary=glossary,
                new_entries=new_glossary_entries,
                chunk_id=chunk.chunk_id,
            )
            glossary_updates.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "raw_model_output": glossary_result.final_output,
                    "entries": new_glossary_entries,
                }
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            safe_print(
                f"Glossary update for {chunk.chunk_id} failed; continuing without "
                f"new glossary entries ({compact_error_message(exc)})."
            )
            glossary_updates.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "raw_model_output": "",
                    "entries": [],
                    "error": error,
                }
            )

    (args.output_dir / "translated_chunks.json").write_text(
        json.dumps(translated_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "glossary.json").write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "glossary_updates.json").write_text(
        json.dumps(glossary_updates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    first_pass_translation = reconstruct_translation(translated_chunks)
    first_pass_translations_by_block_id = collect_block_translations(translated_chunks)

    review_prompt = build_review_prompt(
        target_language=args.target_language,
        review_brief=review_brief,
        manuscript=manuscript,
        translation=first_pass_translation,
    )
    review_error = ""
    try:
        review_result = await run_agent_with_retries(
            agent=reviewer,
            prompt=review_prompt,
            label="review",
            retries=args.model_retries,
            base_delay_seconds=args.retry_base_delay,
            timeout_seconds=args.model_timeout,
        )
        review = review_result.final_output
    except Exception as exc:
        review_error = f"{type(exc).__name__}: {exc}"
        review = (
            "Review unavailable.\n\n"
            "The translation and glossary files were generated, but the reviewer "
            f"agent failed with: {review_error}"
        )
    (args.output_dir / "review_report.md").write_text(review, encoding="utf-8")

    final_translated_chunks = translated_chunks
    revision_report_data: dict[str, object] = {"enabled": args.revision_pass}
    if args.revision_pass:
        (args.output_dir / "first_pass_translated_chunks.json").write_text(
            json.dumps(translated_chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (args.output_dir / "first_pass_translated_sample.md").write_text(
            first_pass_translation,
            encoding="utf-8",
        )
        (args.output_dir / "first_pass_block_translations.json").write_text(
            json.dumps(first_pass_translations_by_block_id, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if review_error:
            revision_report_data.update(
                {
                    "status": "skipped",
                    "reason": "Reviewer feedback was unavailable.",
                    "review_error": review_error,
                }
            )
        else:
            final_translated_chunks, revision_report_data = await revise_translated_chunks(
                chunks=chunks,
                first_pass_chunks=translated_chunks,
                glossary=glossary,
                target_language=args.target_language,
                brief=brief,
                reviewer_feedback=review,
                paragraph_blocks=paragraph_blocks,
                context_chunk_count=context_chunk_count,
                model_retries=args.model_retries,
                retry_base_delay_seconds=args.retry_base_delay,
                model_timeout_seconds=args.model_timeout,
            )
            (args.output_dir / "revised_chunks.json").write_text(
                json.dumps(final_translated_chunks, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        (args.output_dir / "revision_report.json").write_text(
            json.dumps(revision_report_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    final_translation = reconstruct_translation(final_translated_chunks)
    translations_by_block_id = collect_block_translations(final_translated_chunks)
    translation_payloads_by_block_id = collect_block_translation_payloads(
        final_translated_chunks
    )
    (args.output_dir / "translated_sample.md").write_text(
        final_translation,
        encoding="utf-8",
    )
    (args.output_dir / "block_translations.json").write_text(
        json.dumps(translations_by_block_id, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    translated_document_path = default_translated_document_path(
        args.output_dir,
        parsed_document.source_format,
    )
    export_report = write_translated_document(
        parsed_document=parsed_document,
        output_path=translated_document_path,
        translated_text=final_translation,
        translations_by_block_id=translation_payloads_by_block_id,
    )
    (args.output_dir / "export_report.json").write_text(
        json.dumps(export_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    safe_print("\n=== Translated Sample ===")
    safe_print(final_translation)
    safe_print("\n=== Review Report ===")
    safe_print(review)
    safe_print(f"\nExported document: {translated_document_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

