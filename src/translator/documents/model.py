from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    type: str
    text: str = ""
    translate: bool = True
    level: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedDocument:
    source_path: str | None
    source_format: str
    blocks: list[DocumentBlock]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    def to_translation_text(self) -> str:
        return blocks_to_translation_text(self.blocks)


def blocks_to_translation_text(blocks: list[DocumentBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if not block.translate:
            continue

        text = block.text.strip()
        if not text:
            continue

        parts.append(text)
    return "\n\n".join(parts)


def parsed_document_from_text(
    text: str,
    source_path: str | Path | None = None,
    source_format: str = "txt",
) -> ParsedDocument:
    blocks: list[DocumentBlock] = []
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    for index, paragraph in enumerate(paragraphs, start=1):
        blocks.append(
            DocumentBlock(
                block_id=f"b{index:04d}",
                type="paragraph",
                text=paragraph,
                translate=True,
            )
        )

    return ParsedDocument(
        source_path=str(source_path) if source_path else None,
        source_format=source_format,
        blocks=blocks,
    )

