import copy
from pathlib import Path
import runpy
import zipfile

import pytest

from deeptutor.learning import classroom as cr
from deeptutor.learning import slide_visuals as visual
from deeptutor.learning import teaching_deck as deck

room = runpy.run_path(str(Path(__file__).with_name("test_classroom.py")))["room"]


def slide():
    return {
        "title": "定义与反例",
        "layout": "comparison",
        "content": "比较含有两个未知数的整式方程，检查次数条件。",
        "panels": [
            {"heading": "符合定义", "text": "2x+3y=8，两个未知数且次数为1。"},
            {"heading": "不符合定义", "text": "xy=6中xy次数为2，不符合一次条件。"},
        ],
        "formula": r"\frac{1}{2}x+y=3",
        "notes": "完整解释来源与判断方法",
        "source_ids": [],
        "lines": [],
    }


def test_curriculum_has_no_second_outline_and_no_assessment(room):
    store, _ = room
    course = cr.create(store, "p", [], 15, True)
    assert course["mode"] == "curriculum_ppt" and course["state"] == "queued_pages"
    assert [x["objective_id"] for x in course["curriculum"]] == ["k"]
    assert course["outline"] == []
    assert cr.create(store, "p", [], 30, True)["id"] == course["id"]
    with cr.locked(store, "p", course["id"]) as (c, d):
        deck.commit(d, {"slides": [slide()], "objective_complete": False})
        assert d["state"] == "queued_pages"
        deck.commit(d, {"slides": [dict(slide(), title="进一步理解")], "objective_complete": True})
        cr.put(c, d)
    result = cr.load(store, "p", course["id"])
    assert result["state"] == "ready" and len(result["pages"]) == 2
    assert all(p["kind"] == "slide" and p["canvas"] for p in result["pages"])
    cr.tutor_handoff(store, "p", course["id"], 1, "check")
    from deeptutor.learning.project_continuity import project_handoff

    packet = project_handoff(store.load("p"), store)["reading_context"]
    assert packet["slide_id"] == "scene-1" and packet["objective_id"] == "k"
    assert store.load("p").learning_evidence == []


def test_export_contains_visuals_formula_and_speaker_notes(tmp_path):
    page = {"visual": slide(), "source_ids": []}
    assert any(e["type"] == "image" for e in visual.elements(page))
    visual.png(page, tmp_path / "preview.png")
    assert (tmp_path / "preview.png").stat().st_size > 5000
    for kind in ["pdf", "pptx"]:
        visual.export({"pages": [page], "sources": []}, tmp_path, kind)
    from pptx import Presentation

    presentation = Presentation(tmp_path / "lesson.pptx")
    assert len(presentation.slides[0].shapes) > 6
    assert any(s.shape_type == 13 for s in presentation.slides[0].shapes)
    assert "完整解释" in presentation.slides[0].notes_slide.notes_text_frame.text
    assert (tmp_path / "lesson.pdf").read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize(
    "formula",
    [
        r"\begin{cases}x+y=5\\2x-y=1\end{cases}",
        r"\begin{aligned}3(x+1)-y&=2y+1\\3x-3y&=-2\end{aligned}",
    ],
)
def test_real_equation_system_renders_and_exports(formula, tmp_path):
    import base64
    import io

    from PIL import Image

    data = base64.b64decode(visual.formula_png(formula))
    image = Image.open(io.BytesIO(data))
    assert image.width > 30 and image.height > 30
    page = {"visual": dict(slide(), formula=formula), "source_ids": []}
    for kind in ("pdf", "pptx"):
        visual.export({"pages": [page], "sources": []}, tmp_path, kind)
        assert (tmp_path / ("lesson." + kind)).stat().st_size > 1000


def test_real_chinese_formula_has_no_missing_glyph_warnings(caplog):
    visual.formula_png(r"Ax+By=C\quad(A,B\text{为常数},A\neq0,B\neq0)")
    assert not any("glyph" in record.message.lower() for record in caplog.records)


def test_bad_formula_is_rejected_instead_of_saved_as_error_image():
    with pytest.raises(visual.FormulaRenderError):
        visual.formula_png(r"\unknownWenjinCommand{z}")


def test_generation_uses_retry_feedback_to_repair_formula(room, monkeypatch):
    import asyncio
    import json

    store, _ = room
    course = cr.create(store, "p", [], 15, True)
    captured = []

    async def complete(self, **kw):
        captured.append(json.loads(kw["prompt"]))
        return json.dumps(
            {
                "slides": [dict(slide(), formula=r"\begin{cases}x+y=5\\2x-y=1\end{cases}")],
                "objective_complete": True,
            }
        )

    monkeypatch.setattr("deeptutor.services.llm.client.LLMClient.complete", complete)
    with cr.locked(store, "p", course["id"]) as (c, d):
        cr.generation_failure(d, visual.FormulaRenderError("bad tex"))
        cr.put(c, d)
    batch = asyncio.run(deck.generate(d))
    assert captured[0]["retry_feedback"] and batch["objective_complete"]
