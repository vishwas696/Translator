from __future__ import annotations

from dataclasses import asdict, dataclass
import re


SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]*(?:\s+|$)")
WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class TranslationChunk:
    chunk_id: str
    paragraph_ids: list[str]
    text: str
    source_word_count: int
    contains_partial_paragraph: bool
    starts_paragraph: bool
    continues_paragraph: bool
    ends_paragraph: bool
    split_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def chunk_manuscript(manuscript: str, max_words: int = 1500) -> list[TranslationChunk]:
    """Split manuscript text into paragraph-aware translation chunks."""
    if max_words < 1:
        raise ValueError("max_words must be at least 1")

    paragraphs = _extract_paragraphs(manuscript)
    chunks: list[TranslationChunk] = []
    pending_paragraphs: list[tuple[str, str]] = []
    pending_word_count = 0

    def flush_pending() -> None:
        nonlocal pending_paragraphs, pending_word_count
        if not pending_paragraphs:
            return

        text = "\n\n".join(paragraph for _, paragraph in pending_paragraphs)
        chunks.append(
            TranslationChunk(
                chunk_id=_chunk_id(len(chunks)),
                paragraph_ids=[paragraph_id for paragraph_id, _ in pending_paragraphs],
                text=text,
                source_word_count=pending_word_count,
                contains_partial_paragraph=False,
                starts_paragraph=True,
                continues_paragraph=False,
                ends_paragraph=True,
            )
        )
        pending_paragraphs = []
        pending_word_count = 0

    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        paragraph_id = f"p{paragraph_index:04d}"
        paragraph_word_count = count_words(paragraph)

        if paragraph_word_count > max_words:
            flush_pending()
            chunks.extend(
                _split_long_paragraph(
                    paragraph_id=paragraph_id,
                    paragraph=paragraph,
                    chunk_offset=len(chunks),
                    max_words=max_words,
                )
            )
            continue

        if pending_paragraphs and pending_word_count + paragraph_word_count > max_words:
            flush_pending()

        pending_paragraphs.append((paragraph_id, paragraph))
        pending_word_count += paragraph_word_count

    flush_pending()
    return chunks


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _extract_paragraphs(manuscript: str) -> list[str]:
    normalized = manuscript.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    return [
        _normalize_paragraph(paragraph)
        for paragraph in re.split(r"\n\s*\n", normalized)
        if paragraph.strip()
    ]


def _normalize_paragraph(paragraph: str) -> str:
    return re.sub(r"[ \t]*\n[ \t]*", " ", paragraph.strip())


def _split_long_paragraph(
    paragraph_id: str,
    paragraph: str,
    chunk_offset: int,
    max_words: int,
) -> list[TranslationChunk]:
    parts: list[str] = []
    remaining = paragraph.strip()

    while count_words(remaining) > max_words:
        split_at = _last_sentence_boundary_under_word_limit(remaining, max_words)
        if split_at is None:
            split_at = _word_boundary_after_n_words(remaining, max_words)

        part = remaining[:split_at].strip()
        if not part:
            break

        parts.append(part)
        remaining = remaining[split_at:].strip()

    if remaining:
        parts.append(remaining)

    chunks: list[TranslationChunk] = []
    last_part_index = len(parts) - 1

    for part_index, part in enumerate(parts):
        chunks.append(
            TranslationChunk(
                chunk_id=_chunk_id(chunk_offset + part_index),
                paragraph_ids=[paragraph_id],
                text=part,
                source_word_count=count_words(part),
                contains_partial_paragraph=len(parts) > 1,
                starts_paragraph=part_index == 0,
                continues_paragraph=part_index > 0,
                ends_paragraph=part_index == last_part_index,
                split_reason="paragraph_too_long",
            )
        )

    return chunks


def _last_sentence_boundary_under_word_limit(text: str, max_words: int) -> int | None:
    best_boundary: int | None = None

    for match in SENTENCE_END_RE.finditer(text):
        candidate = match.end()
        if count_words(text[:candidate]) <= max_words:
            best_boundary = candidate
        else:
            break

    return best_boundary


def _word_boundary_after_n_words(text: str, word_count: int) -> int:
    matches = list(WORD_RE.finditer(text))
    if len(matches) <= word_count:
        return len(text)
    return matches[word_count - 1].end()


def _chunk_id(index: int) -> str:
    return f"chunk_{index + 1:04d}"

