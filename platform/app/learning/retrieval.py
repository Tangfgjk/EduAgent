"""Local Markdown/PDF BM25 adapter. Returned text is evidence, never a verdict.

Sources are confined to an explicit knowledge root. Content hashes pin source
versions; changed/deleted files cannot produce stale citations. Unmapped or
conflicting KC references remain inspectable but cannot enter teaching output.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Callable, Literal, Protocol

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader


class ParseError(ValueError):
    pass


class RetrievalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kc_refs: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=50)
    mode: Literal["bm25", "vector", "hybrid"] = "bm25"


class Citation(BaseModel):
    path: str
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None


class RetrievedEvidence(BaseModel):
    source_id: str
    source_version: str
    chunk_id: str
    text: str
    citation: Citation
    score: float = 0
    grounding_status: Literal["grounded", "ungrounded", "conflict"] = "ungrounded"
    kc_refs: list[str] = Field(default_factory=list)
    parser_version: str = "markdown-it-pypdf-v1"
    retrieval_version: str = "bm25-local-v1"

    @property
    def usable_for_teaching(self) -> bool:
        # Necessary, not sufficient: the downstream Verifier/Governor is still required.
        return self.grounding_status == "grounded"


class ImportedSource(BaseModel):
    source_id: str
    source_version: str
    path: str
    chunk_count: int
    parser_version: str = "markdown-it-pypdf-v1"
    ocr_used: bool = False
    kc_refs: list[str] = Field(default_factory=list)


class RetrievalPort(Protocol):
    def retrieve(self, query: str, context: RetrievalContext) -> list[RetrievedEvidence]: ...


def _terms(text: str) -> list[str]:
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    # Chinese characters and adjacent bigrams provide deterministic lexical search.
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend(run)
        terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    return terms


class LocalRetrieval:
    def __init__(self, knowledge_root: str | Path, *, chunk_chars: int = 1200,
                 index_connection=None, lock=None, ocr: Callable[[bytes, int], str] | None = None,
                 max_source_bytes: int = 20_000_000, max_pdf_pages: int = 50):
        self.root = Path(knowledge_root).resolve()
        if chunk_chars < 100:
            raise ValueError("chunk_chars must be at least 100")
        self.chunk_chars = chunk_chars
        self.connection = index_connection
        self.lock = lock or threading.RLock()
        self.ocr = ocr
        self.max_source_bytes = max_source_bytes
        self.max_pdf_pages = max_pdf_pages
        self._sources: dict[str, ImportedSource] = {}
        self._chunks: dict[str, list[RetrievedEvidence]] = {}
        if self.connection is not None:
            with self.lock:
                self.connection.execute("CREATE TABLE IF NOT EXISTS knowledge_sources(source_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
                self.connection.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks(source_id TEXT NOT NULL,chunk_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
                self.connection.commit()
                for row in self.connection.execute("SELECT source_id,payload FROM knowledge_sources ORDER BY source_id").fetchall():
                    imported = ImportedSource.model_validate_json(row[1])
                    if Path(imported.path).resolve().is_relative_to(self.root):
                        self._sources[row[0]] = imported
                        self._chunks[row[0]] = []
                for row in self.connection.execute("SELECT source_id,payload FROM knowledge_chunks ORDER BY chunk_id").fetchall():
                    if row[0] in self._sources:
                        self._chunks[row[0]].append(RetrievedEvidence.model_validate_json(row[1]))

    def sources(self) -> list[ImportedSource]:
        with self.lock:
            return [source.model_copy(deep=True) for _, source in sorted(self._sources.items())]

    def _confined(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ParseError("source outside knowledge root")
        if not resolved.is_file():
            raise ParseError("source missing or not a regular file")
        return resolved

    def import_document(self, path: str | Path, *, source_id: str,
                        kc_refs: list[str] | None = None, before_publish: Callable[[], None] | None = None) -> ImportedSource:
        if not source_id.strip():
            raise ParseError("source_id required")
        source = self._confined(path)
        if source.stat().st_size > self.max_source_bytes:
            raise ParseError("source_size_limit")
        content = source.read_bytes()
        version = hashlib.sha256(content).hexdigest()
        segments: list[tuple[str, Citation]] = []
        ocr_used = False
        if source.suffix.lower() == ".md":
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ParseError("markdown must use UTF-8") from exc
            lines = text.splitlines()
            for token in MarkdownIt("commonmark").parse(text):
                if token.type not in {"inline", "fence", "code_block", "html_block"} or not token.map:
                    continue
                start, end = token.map
                for offset in range(start, end):
                    line = lines[offset]
                    for char in range(0, len(line), self.chunk_chars):
                        if line[char:char + self.chunk_chars].strip():
                            segments.append((line[char:char + self.chunk_chars], Citation(path=str(source), line_start=offset + 1, line_end=offset + 1)))
        elif source.suffix.lower() == ".pdf":
            try:
                reader = PdfReader(BytesIO(content))
                if reader.is_encrypted:
                    raise ParseError("encrypted_pdf_unsupported")
                if len(reader.pages) > self.max_pdf_pages:
                    raise ParseError("pdf_page_limit")
                for page_num, page in enumerate(reader.pages, start=1):
                    text = (page.extract_text() or "").strip()
                    if not text:
                        if self.ocr is None:
                            raise ParseError(f"ocr_required: page {page_num} has no extractable text")
                        text = self.ocr(content, page_num).strip()
                        if not text:
                            raise ParseError(f"ocr_no_text: page {page_num}")
                        ocr_used = True
                    for offset in range(0, len(text), self.chunk_chars):
                        segments.append((text[offset:offset + self.chunk_chars], Citation(path=str(source), page=page_num)))
            except ParseError:
                raise
            except Exception as exc:
                raise ParseError(f"pdf_parse_failed: {type(exc).__name__}") from exc
        else:
            raise ParseError("unsupported_format: only Markdown/PDF")
        if not segments:
            raise ParseError("empty_document")
        refs = sorted(set(kc_refs or []))
        chunks = [RetrievedEvidence(source_id=source_id, source_version=version,
                  chunk_id=hashlib.sha256(f"{source_id}:{version}:{i}".encode()).hexdigest()[:24],
                  text=text, citation=citation, kc_refs=refs)
                  for i, (text, citation) in enumerate(segments)]
        imported = ImportedSource(source_id=source_id, source_version=version,
                                  path=str(source), chunk_count=len(chunks), ocr_used=ocr_used, kc_refs=refs)
        # Publish only a fully parsed source; a failure cannot erase a previous index.
        with self.lock:
            if before_publish is not None:
                before_publish()
            if self.connection is not None:
                try:
                    self.connection.execute("BEGIN IMMEDIATE")
                    self.connection.execute("INSERT INTO knowledge_sources VALUES (?,?) ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload", (source_id, imported.model_dump_json()))
                    self.connection.execute("DELETE FROM knowledge_chunks WHERE source_id=?", (source_id,))
                    for chunk in chunks:
                        self.connection.execute("INSERT INTO knowledge_chunks VALUES (?,?,?)", (source_id, chunk.chunk_id, chunk.model_dump_json()))
                    self.connection.commit()
                except Exception:
                    self.connection.rollback()
                    raise
            self._sources[source_id] = imported
            self._chunks[source_id] = chunks
        return imported

    def retrieve(self, query: str, context: RetrievalContext) -> list[RetrievedEvidence]:
        chunks = []
        with self.lock:
            active_sources = list(self._sources.items())
            active_chunks = dict(self._chunks)
        for source_id, source in active_sources:
            try:
                path = self._confined(source.path)
                if hashlib.sha256(path.read_bytes()).hexdigest() == source.source_version:
                    chunks.extend(active_chunks[source_id])
            except (ParseError, OSError):
                continue
        query_terms = set(_terms(query))
        if not chunks or not query_terms:
            return []
        docs = [Counter(_terms(chunk.text)) for chunk in chunks]
        lengths = [sum(doc.values()) for doc in docs]
        average = sum(lengths) / len(lengths) or 1
        df = {term: sum(term in doc for doc in docs) for term in query_terms}
        results = []
        query_vector = _hashed_vector(query)
        for chunk, doc, length in zip(chunks, docs, lengths):
            score = 0.0
            for term in sorted(query_terms):
                frequency = doc[term]
                if frequency:
                    idf = math.log(1 + (len(docs) - df[term] + .5) / (df[term] + .5))
                    score += idf * frequency * 2.5 / (frequency + 1.5 * (1 - .75 + .75 * length / average))
            vector_score = _cosine(query_vector, _hashed_vector(chunk.text))
            if (context.mode == "bm25" and score <= 0) or (context.mode == "vector" and vector_score <= 0) or (context.mode == "hybrid" and score <= 0 and vector_score <= 0):
                continue
            status = "ungrounded" if not chunk.kc_refs else "grounded"
            if context.kc_refs and chunk.kc_refs and not set(context.kc_refs).intersection(chunk.kc_refs):
                status = "conflict"
            results.append((chunk.model_copy(update={"grounding_status": status}, deep=True), score, vector_score))
        lexical = sorted(results, key=lambda result: (-result[1], result[0].source_id, result[0].chunk_id))
        vectors = sorted(results, key=lambda result: (-result[2], result[0].source_id, result[0].chunk_id))
        lexical_rank = {result[0].chunk_id: rank for rank, result in enumerate((r for r in lexical if r[1] > 0), 1)}
        vector_rank = {result[0].chunk_id: rank for rank, result in enumerate((r for r in vectors if r[2] > 0), 1)}
        scored = []
        for result, lexical_score, vector_score in results:
            score = lexical_score if context.mode == "bm25" else vector_score if context.mode == "vector" else (
                1 / (60 + lexical_rank[result.chunk_id]) if result.chunk_id in lexical_rank else 0) + (
                1 / (60 + vector_rank[result.chunk_id]) if result.chunk_id in vector_rank else 0)
            version = "bm25-local-v1" if context.mode == "bm25" else "hashed-ngram-v1" if context.mode == "vector" else "bm25-hashed-ngram-rrf-v1"
            scored.append(result.model_copy(update={"score": round(score, 12), "retrieval_version": version}, deep=True))
        return sorted(scored, key=lambda result: (-result.score, result.source_id, result.chunk_id))[:context.limit]


def _hashed_vector(text: str) -> dict[int, float]:
    """Offline lexical feature vectors; these are not semantic neural embeddings."""
    counts = Counter(int(hashlib.sha256(term.encode()).hexdigest()[:8], 16) % 512 for term in _terms(text))
    norm = math.sqrt(sum(count * count for count in counts.values())) or 1
    return {key: count / norm for key, count in counts.items()}


def _cosine(left: dict[int, float], right: dict[int, float]) -> float:
    return sum(value * right.get(key, 0) for key, value in left.items())


class LocalOCR:
    """Lazy Chinese/English RapidOCR on CPU, using bundled ONNX models only."""
    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()

    def __call__(self, content: bytes, page_number: int) -> str:
        try:
            import pypdfium2 as pdfium
            from rapidocr_onnxruntime import RapidOCR
            with self._lock:
                if self._engine is None:
                    self._engine = RapidOCR()
                document = pdfium.PdfDocument(content)
                try:
                    page = document.get_page(page_number - 1)
                    try:
                        bitmap = page.render(scale=2)
                        try:
                            raster = bitmap.to_numpy().copy()
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                finally:
                    document.close()
                result, _ = self._engine(raster)
                return "\n".join(row[1] for row in result or [])
        except Exception as exc:
            raise ParseError(f"local_ocr_failed:{type(exc).__name__}") from exc
