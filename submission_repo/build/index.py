"""Build the local Chroma text index from ``data/pages.json``.

This is a build-time command. The runtime answer path only opens the finished
local index and never calls a website or a build step.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bot.store import add_to_store, get_store


CHUNK_SIZE, OVERLAP = 800, 100


def _normalise_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def chunk(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    """Split text at paragraph/line boundaries, then fall back to characters.

    The fixed-size fallback keeps long tables and paragraphs searchable while
    the normal path avoids cutting every heading away from its first facts.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and overlap must be smaller than size")

    lines = _normalise_lines(text)
    if not lines:
        return []

    pieces: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(line) <= size:
            current = line
            continue

        # A single long paragraph/table row needs the same overlap guarantee
        # as the original workshop baseline.
        step = size - overlap
        pieces.extend(line[i:i + size] for i in range(0, len(line), step))
        current = ""

    if current:
        pieces.append(current)

    # Add a small tail from the prior piece when a paragraph boundary would
    # otherwise make adjacent facts impossible to retrieve together.
    with_overlap: list[str] = []
    for i, piece in enumerate(pieces):
        if i and len(piece) < size:
            tail = pieces[i - 1][-overlap:]
            piece = f"{tail}\n{piece}".strip()
        with_overlap.append(piece)
    return [piece for piece in with_overlap if piece.strip()]


def _chunk_id(url: str, position: int, text: str) -> str:
    raw = f"{url}\0{position}\0{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_index(pages: list[dict], reset: bool = True):
    """Create a fresh local index and write the inspectable chunk manifest."""
    texts: list[str] = []
    metas: list[dict] = []
    manifest: list[dict] = []

    for page in pages:
        page_text = page.get("text", "").strip()
        if not page_text:
            continue
        title = page.get("title", "").strip()
        source_url = page.get("url", "")
        for position, piece in enumerate(chunk(page_text)):
            text_for_index = f"{title}\n{piece}".strip() if title else piece
            metadata = {
                "url": source_url,
                "title": title,
                "position": position,
                "kind": "text",
                "source_type": page.get("source_type", "website"),
                "page_type": page.get("page_type", "unknown"),
            }
            chunk_id = _chunk_id(source_url, position, text_for_index)
            texts.append(text_for_index)
            metas.append(metadata)
            manifest.append({"id": chunk_id, "text": text_for_index, "metadata": metadata})

    if not texts:
        raise RuntimeError("pages.json contains no non-empty page text; build the corpus first")

    # Persist the human-readable artifact before the embedding call so a
    # failed/limited gateway run still leaves an inspectable build manifest.
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    (data_dir / "chunks.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    store = get_store(reset=reset)
    add_to_store(store, texts, metas, ids=[entry["id"] for entry in manifest])
    print(f"indexed {len(texts)} chunks from {len(pages)} pages")
    print(f"chunk manifest: {data_dir / 'chunks.json'}")
    return store


if __name__ == "__main__":
    pages_path = Path("data/pages.json")
    if not pages_path.exists():
        raise SystemExit("data/pages.json is missing; run python -m build.scrape first")
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    build_index(pages)
