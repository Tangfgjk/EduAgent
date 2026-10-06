"""Persistent, hybrid, OCR knowledge adapters with explicit limits."""
import sqlite3

import pytest
from pypdf import PdfWriter

from app.learning.retrieval import LocalRetrieval, ParseError, RetrievalContext


def test_persisted_index_reloads_and_rejects_changed_source(tmp_path):
    source = tmp_path / "lesson.md"
    source.write_text("方程的系数与验算", encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    first = LocalRetrieval(tmp_path, index_connection=conn)
    imported = first.import_document(source, source_id="lesson", kc_refs=["MATH.G7.EQ.SOLVE"])
    restarted = LocalRetrieval(tmp_path, index_connection=conn)
    result = restarted.retrieve("验算", RetrievalContext(mode="hybrid"))
    assert result and result[0].source_version == imported.source_version
    assert result[0].retrieval_version == "bm25-hashed-ngram-rrf-v1"
    assert restarted.sources()[0].source_id == "lesson"
    source.write_text("new version", encoding="utf-8")
    assert restarted.retrieve("验算", RetrievalContext()) == []


def test_ocr_adapter_only_used_for_empty_page(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    calls = []
    def ocr(content, page):
        calls.append(page)
        return "方程两边保持相等"
    imported = LocalRetrieval(tmp_path, ocr=ocr).import_document(source, source_id="scan", kc_refs=["MATH.G7.EQ.BALANCE"])
    assert calls == [1] and imported.ocr_used


def test_ocr_empty_result_remains_explicit_failure(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    with pytest.raises(ParseError, match="ocr_no_text"):
        LocalRetrieval(tmp_path, ocr=lambda content, page: "").import_document(source, source_id="scan")


def test_local_ocr_engine_renders_scan_without_network(tmp_path):
    # OCR engine is optional but installed in the project .venv; if model files
    # are unavailable this test should fail explicitly rather than claim text.
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    from app.learning.retrieval import LocalOCR
    with pytest.raises(ParseError, match="ocr_no_text|local_ocr_failed"):
        LocalRetrieval(tmp_path, ocr=LocalOCR()).import_document(source, source_id="scan")


def test_actual_chinese_scan_has_text_and_page_citation(tmp_path):
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont
    from app.learning.retrieval import LocalOCR
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.is_file():
        pytest.skip("Chinese system font unavailable; OCR portability fixture pending")
    image = Image.new("RGB", (1400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 70), "方程两边同时加上相同的数", font=ImageFont.truetype(str(font_path), 65), fill="black")
    source = tmp_path / "chinese-scan.pdf"
    image.save(source, "PDF", resolution=150)
    retrieval = LocalRetrieval(tmp_path, ocr=LocalOCR())
    imported = retrieval.import_document(source, source_id="chinese", kc_refs=["MATH.G7.EQ.BALANCE"])
    result = retrieval.retrieve("方程", RetrievalContext(kc_refs=["MATH.G7.EQ.BALANCE"]))
    assert imported.ocr_used and result
    assert "方程" in result[0].text and result[0].citation.page == 1


def test_import_size_limit_rejected_before_parser(tmp_path):
    source = tmp_path / "oversized.md"
    source.write_text("text" * 100, encoding="utf-8")
    with pytest.raises(ParseError, match="size_limit"):
        LocalRetrieval(tmp_path, max_source_bytes=100).import_document(source, source_id="large")
