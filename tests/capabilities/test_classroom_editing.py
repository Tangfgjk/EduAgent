import copy
from pathlib import Path
import runpy

import pytest

from deeptutor.learning import classroom as cr
from deeptutor.learning import classroom_editing as edit
from deeptutor.learning import classroom_media as media
from deeptutor.learning.models import LearningProgress
from deeptutor.learning.storage import LearningStore

helpers = runpy.run_path(str(Path(__file__).with_name("test_classroom.py")))
room = helpers["room"]


def prepare(room, visual=False):
    store, course = room
    helpers["ready"](store, course["id"])
    with cr.locked(store, "p", course["id"]) as (c, d):
        quiz = copy.deepcopy(d["pages"][0])
        page = {
            **copy.deepcopy(quiz),
            "id": "lecture",
            "kind": "slide",
            "title": "两个条件",
            "content": "认识两个未知数",
            "question": "",
            "options": [],
            "answer_index": None,
            "explanation": "",
            "narration": "旧讲稿",
        }
        if visual:
            page["visual"] = dict(
                title=page["title"],
                content=page["content"],
                layout="concept",
                panels=[{"heading": "未知数", "text": "两个未知数"}],
                formula="",
                notes="补充",
                spoken_script="旧讲稿",
                lines=[],
                source_ids=[],
            )
        d.update(
            pages=[page, quiz],
            outline=[
                {k: p[k] for k in ("title", "objective_id", "kind", "goal")} for p in [page, quiz]
            ],
            viewed=["lecture"],
        )
        cr.put(c, d)
    return store, course["id"]


def test_draft_is_durable_idempotent_scoped_and_does_not_expose_quiz_key(room, tmp_path):
    store, cid = prepare(room)
    d = edit.start(store, "p", cid)
    assert edit.start(store, "p", cid)["id"] == d["id"]
    assert "answer_index" not in d["pages"][1] and "explanation" not in d["pages"][1]
    saved = edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "新的解释"})
    assert saved["revision"] == 2 and saved["changed_page_ids"] == ["lecture"]
    reopened = LearningStore(store.db_path.parent)
    assert edit.get(reopened, "p", cid, d["id"])["pages"][0]["content"] == "新的解释"
    assert cr.load(store, "p", cid)["pages"][0]["content"] == "认识两个未知数"
    other = LearningStore(tmp_path / "other")
    other.save(LearningProgress(book_id="p"))
    with pytest.raises(LookupError):
        edit.get(other, "p", cid, d["id"])


def test_stale_save_protected_fields_and_quiz_are_rejected(room):
    store, cid = prepare(room)
    d = edit.start(store, "p", cid)
    for changes in (
        {"answer_index": 0},
        {"source_ids": ["fake"]},
        {"content": None},
        {"content": ""},
    ):
        with pytest.raises(ValueError):
            edit.save_page(store, "p", cid, d["id"], 1, "lecture", changes)
    with pytest.raises(ValueError, match="测验"):
        edit.save_page(store, "p", cid, d["id"], 1, "scene-0", {"content": "改变考题"})
    edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "新正文"})
    with pytest.raises(RuntimeError):
        edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "旧请求覆盖"})
    with pytest.raises(RuntimeError):
        edit.publish(store, "p", cid, d["id"], 1, "过期发布")


def test_publishing_forks_preserves_old_evidence_and_is_idempotent(room, monkeypatch):
    store, cid = prepare(room)
    old = cr.answer(store, "p", cid, 1, 1)
    evidence = store.load("p").model_dump()
    monkeypatch.setattr(media, "connection", cr.connection)
    asset = media.request(store, "p", cid, "pdf")
    d = edit.start(store, "p", cid)
    d = edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "新版完整解释"})
    new = edit.publish(store, "p", cid, d["id"], d["revision"], "修正定义说明")
    assert new["id"] != cid and new["version"] == 2
    assert not new["answers"] and not new["viewed"] and not new["conversation"]
    assert new["pages"][0]["content_review"] == "unverified"
    assert cr.load(store, "p", cid)["answers"] == old["answers"]
    assert store.load("p").model_dump() == evidence
    assert edit.publish(store, "p", cid, d["id"], d["revision"], "修正定义说明")["id"] == new["id"]
    changed_asset = media.request(store, "p", new["id"], "pdf")
    assert changed_asset["key"] != asset["key"]
    with pytest.raises(LookupError):
        media.get(store, "p", new["id"], asset["key"])
    assert [v["version"] for v in edit.editions(store, "p", new["id"])] == [2, 1]


def test_branches_get_distinct_versions_and_new_drafts_resume_independently(room):
    store, cid = prepare(room)
    d = edit.start(store, "p", cid)
    with pytest.raises(ValueError, match="修改"):
        edit.publish(store, "p", cid, d["id"], 1, "无修改")
    d = edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "第二版"})
    second = edit.publish(store, "p", cid, d["id"], d["revision"], "更清楚")
    next_draft = edit.start(store, "p", cid)
    assert next_draft["id"] != d["id"]
    next_draft = edit.save_page(
        store, "p", cid, next_draft["id"], 1, "lecture", {"content": "第三版来自初版"}
    )
    third = edit.publish(store, "p", cid, next_draft["id"], next_draft["revision"], "另一种说明")
    assert third["version"] == 3
    assert len(edit.editions(store, "p", second["id"])) == 3


def test_source_change_and_running_generation_block_publishing(room):
    store, cid = room[0], room[1]["id"]
    with pytest.raises(RuntimeError):
        edit.start(store, "p", cid)
    store, cid = prepare(room)
    d = edit.start(store, "p", cid)
    d = edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"title": "已编辑"})
    with cr.locked(store, "p", cid) as (c, source):
        source["pages"][0]["title"] = "源已变化"
        cr.put(c, source)
    with pytest.raises(RuntimeError, match="原课堂"):
        edit.publish(store, "p", cid, d["id"], d["revision"], "发布")
    edit.discard(store, "p", cid, d["id"], d["revision"])
    assert edit.start(store, "p", cid)["id"] != d["id"]


def test_visual_schema_formula_and_coordinates_are_checked_before_save(room, monkeypatch):
    store, cid = prepare(room, visual=True)
    d = edit.start(store, "p", cid)
    with pytest.raises(ValueError):
        edit.save_page(
            store, "p", cid, d["id"], 1, "lecture", {"layout": "coordinate", "lines": [[0, 0, 1]]}
        )
    with pytest.raises(ValueError):
        edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"lines": [[1, float("nan"), 1]]})
    from deeptutor.learning import slide_visuals

    def bad(_):
        raise ValueError("公式语法错误")

    monkeypatch.setattr(slide_visuals, "elements", bad)
    with pytest.raises(ValueError, match="公式"):
        edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"formula": "bad"})


@pytest.mark.asyncio
async def test_http_revision_and_owner_boundary(room, monkeypatch):
    from fastapi import FastAPI
    import httpx

    from deeptutor.api.routers import mastery_path

    store, cid = prepare(room)
    monkeypatch.setattr(mastery_path, "LearningStore", lambda: store)
    app = FastAPI()
    app.include_router(mastery_path.router, prefix="/api/mastery-paths")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        root = f"/api/mastery-paths/topics/p/classrooms/{cid}/editor"
        d = (await client.post(root)).json()
        saved = await client.put(
            root + "/" + d["id"],
            json={"revision": 1, "page_id": "lecture", "changes": {"content": "HTTP修改"}},
        )
        assert saved.status_code == 200
        assert (
            await client.put(
                root + "/" + d["id"],
                json={"revision": 1, "page_id": "lecture", "changes": {"title": "冲突"}},
            )
        ).status_code == 409
        assert (await client.post(root.replace("/topics/p/", "/topics/other/"))).status_code == 404
        assert (
            await client.put(
                root + "/" + d["id"],
                json={"revision": 2, "page_id": "lecture", "changes": {"answer_index": 0}},
            )
        ).status_code == 422


def test_content_edits_invalidate_stale_scripts_but_accept_new_narration(room):
    from deeptutor.learning import classroom_av as av

    store, cid = prepare(room, visual=True)
    d = edit.start(store, "p", cid)
    d = edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "新定义讲解"})
    assert d["pages"][0]["visual"]["spoken_script"] == ""
    assert "新定义讲解" in av.script(d["pages"][0]) and "旧讲稿" not in av.script(d["pages"][0])
    d = edit.save_page(
        store,
        "p",
        cid,
        d["id"],
        d["revision"],
        "lecture",
        {"spoken_script": "现在按新的两个条件进行讲解。"},
    )
    assert "新的两个条件" in av.script(d["pages"][0])
    legacy = {
        "kind": "slide",
        "title": "定义",
        "content": "简短摘要",
        "narration": "这是用户修改的详细讲稿。",
        "steps": [],
    }
    assert "详细讲稿" in av.script(legacy)


def test_arranging_draft_preserves_objective_and_quiz_contracts(room):
    store, cid = prepare(room)
    d = edit.start(store, "p", cid)
    with pytest.raises(ValueError, match="至少"):
        edit.arrange(store, "p", cid, d["id"], 1, "delete", "lecture")
    with pytest.raises(ValueError, match="测验"):
        edit.arrange(store, "p", cid, d["id"], 1, "duplicate", "scene-0")
    d = edit.arrange(store, "p", cid, d["id"], 1, "duplicate", "lecture")
    added = d["selected_page_id"]
    d = edit.save_page(
        store, "p", cid, d["id"], d["revision"], added, {"content": "拆出来的完整内容"}
    )
    d = edit.arrange(store, "p", cid, d["id"], d["revision"], "move", added, 0)
    d = edit.arrange(store, "p", cid, d["id"], d["revision"], "delete", "lecture")
    published = edit.publish(store, "p", cid, d["id"], d["revision"], "拆分长内容")
    assert published["pages"][0]["id"] == added
    assert published["pages"][0]["objective_id"] == "k"
    assert len(published["pages"]) == len(published["outline"]) == 2
    assert cr.answer(store, "p", published["id"], 1, 1)["answers"]["scene-0"]["correct"]
    assert cr.load(store, "p", cid)["pages"][0]["id"] == "lecture"


def test_edited_pptx_uses_new_content_and_review_marker(room, tmp_path):
    import zipfile

    store, cid = prepare(room, visual=True)
    d = edit.start(store, "p", cid)
    assert "source_hash" not in d
    d = edit.save_page(store, "p", cid, d["id"], 1, "lecture", {"content": "修改后的解释"})
    new = edit.publish(store, "p", cid, d["id"], d["revision"], "校正讲解")
    media.exports({"sources": [], "pages": [new["pages"][0]]}, tmp_path, "pptx")
    with zipfile.ZipFile(tmp_path / "lesson.pptx") as z:
        text = "".join(
            z.read(n).decode()
            for n in z.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
    assert "修改后的解释" in text and "用户编辑内容" in text and "尚未核查" in text
    assert "认识两个未知数" not in text
