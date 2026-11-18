import runpy
from pathlib import Path
from psycopg.types.json import Jsonb
import zipfile
import pytest
from deeptutor.learning import classroom_media as media
from deeptutor.learning import classroom as cr
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.models import LearningProgress

room = runpy.run_path(str(Path(__file__).with_name("test_classroom.py")))["room"]
ready = runpy.run_path(str(Path(__file__).with_name("test_classroom.py")))["ready"]


def setup_room(room, monkeypatch):
    store, course = room
    monkeypatch.setattr(media, "connection", cr.connection)
    ready(store, course["id"])
    return store, course["id"]


def test_asset_cache_hidden_references_and_owner_boundary(room, monkeypatch, tmp_path):
    store, cid = setup_room(room, monkeypatch)
    a = media.request(store, "p", cid, "pdf")
    b = media.request(store, "p", cid, "pdf")
    assert a["key"] == b["key"] and a["state"] == "queued"
    assert "owner" not in a and "pages" not in a
    other = LearningStore(tmp_path / "other")
    other.save(LearningProgress(book_id="p"))
    with pytest.raises(LookupError):
        media.get(other, "p", cid, a["key"])
    with pytest.raises(LookupError):
        media.resource(store, "p", cid, a["key"], "../secret")


def test_exports_are_real_files_without_quiz_answer(tmp_path):
    body = {
        "sources": [],
        "pages": [
            {
                "title": "定义",
                "content": "两个未知数",
                "steps": [],
                "kind": "quiz",
                "question": "哪个正确",
                "options": ["甲", "乙"],
                "answer_index": 1,
                "explanation": "不应泄露",
            }
        ],
    }
    for kind in ("pdf", "pptx"):
        files = media.exports(body, tmp_path, kind)
        assert (tmp_path / files[0]).stat().st_size > 1000
    assert (tmp_path / "lesson.pdf").read_bytes().startswith(b"%PDF")
    with zipfile.ZipFile(tmp_path / "lesson.pptx") as archive:
        slides = "".join(
            archive.read(n).decode("utf-8")
            for n in archive.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
        assert "两个未知数" in slides and "不应泄露" not in slides


def test_simulation_requires_prediction_and_operations_and_persists(room, monkeypatch):
    store, cid = setup_room(room, monkeypatch)
    a = media.request(store, "p", cid, "simulation")
    from psycopg.types.json import Jsonb

    with media.connection() as c:
        body = c.execute(
            "SELECT body FROM wenjin_classroom_assets WHERE key=%s", (a["key"],)
        ).fetchone()[0]
        body.update(
            state="ready",
            spec={
                "title": "分类",
                "instructions": "判断",
                "categories": ["符合", "不符合"],
                "items": [
                    {"label": "甲", "category": 0, "explanation": "符合条件"},
                    {"label": "乙", "category": 1, "explanation": "次数不同"},
                ],
            },
        )
        c.execute(
            "UPDATE wenjin_classroom_assets SET body=%s WHERE key=%s", (Jsonb(body), a["key"])
        )
    public = media.view(media.get(store, "p", cid, a["key"]))
    assert "category" not in public["spec"]["items"][0]
    with pytest.raises(ValueError):
        media.interact(store, "p", cid, a["key"], "operate", "", [0, 1])
    media.interact(store, "p", cid, a["key"], "predict", "先看次数", [])
    with pytest.raises(ValueError):
        media.interact(store, "p", cid, a["key"], "explain", "解释", [])
    media.interact(store, "p", cid, a["key"], "operate", "", [0, 1])
    media.interact(store, "p", cid, a["key"], "explain", "次数不同，符合预测", [])
    saved = media.get(store, "p", cid, a["key"])["interaction"]
    assert len(saved["result"]) == 2 and saved["explanation"] == "次数不同，符合预测"
    assert store.load("p").learning_evidence == []


def test_linear_simulator_extracts_real_equation_without_code(tmp_path):
    result = media.build(
        {"kind": "linear", "pages": [{"content": "例如 2x+3y=8", "steps": [], "kind": "slide"}]},
        tmp_path,
    )
    assert result["spec"]["a"] == 2 and result["spec"]["b"] == 3 and result["spec"]["c"] == 8
    assert "script" not in result["spec"]
    with pytest.raises(ValueError):
        media.build(
            {"kind": "linear", "pages": [{"content": "这是语文", "steps": [], "kind": "slide"}]},
            tmp_path,
        )


def test_partial_chapter_av_cache_options_and_task_controls(room, monkeypatch):
    store,cid=setup_room(room,monkeypatch)
    with cr.locked(store,"p",cid) as (c,d):
        page=d["pages"][0]
        d.update(state="queued_pages",pages=[dict(page,module_id="m",id="one"),dict(page,module_id="m",id="two"),dict(page,module_id="other",id="three")])
        cr.put(c,d)
    a=media.request(store,"p",cid,"voice",0,"chapter")
    assert a["indices"]==[0,1] and a["page_count"]==2 and a["partial"]
    assert media.request(store,"p",cid,"voice",1,"chapter")["key"]==a["key"]
    assert media.request(store,"p",cid,"voice",0,"chapter",rate=1.25)["key"]!=a["key"]
    with pytest.raises(ValueError): media.request(store,"p",cid,"video",0,"course")
    assert media.request(store,"p",cid,"video",0,"page")["state"]=="queued"
    with media.connection() as c:
        job=media.get(store,"p",cid,a["key"])["body"]
        job.update(state="running",token="old-worker")
        c.execute("UPDATE wenjin_classroom_assets SET body=%s WHERE key=%s",(Jsonb(job),a["key"]))
    cancelled=media.control(store,"p",cid,a["key"],"cancel")
    assert cancelled["state"]=="cancelled"
    assert media.get(store,"p",cid,a["key"])["body"]["token"] is None
    assert media.control(store,"p",cid,a["key"],"retry")["state"]=="queued"
    assert store.load("p").learning_evidence==[]


def test_playback_persists_with_owner_isolation(room,monkeypatch,tmp_path):
    store,cid=setup_room(room,monkeypatch)
    a=media.request(store,"p",cid,"voice")
    with media.connection() as c:
        job=media.get(store,"p",cid,a["key"])["body"];job.update(state="ready",duration=30)
        c.execute("UPDATE wenjin_classroom_assets SET body=%s WHERE key=%s",(Jsonb(job),a["key"]))
    media.control(store,"p",cid,a["key"],"playback",12.5,1.25)
    assert media.view(media.get(store,"p",cid,a["key"]))["playback"]["seconds"]==12.5
    with pytest.raises(ValueError):media.control(store,"p",cid,a["key"],"playback",50,1)
    other=LearningStore(tmp_path/"different-owner");other.save(LearningProgress(book_id="p"))
    with pytest.raises(LookupError):media.control(other,"p",cid,a["key"],"playback",3,1)
    assert not store.load("p").learning_evidence


def test_missing_cached_files_are_requeued(room,monkeypatch):
    store,cid=setup_room(room,monkeypatch)
    a=media.request(store,"p",cid,"pdf")
    with media.connection() as c:
        body=media.get(store,"p",cid,a["key"])["body"];body.update(state="ready",files=["not-on-disk.pdf"])
        c.execute("UPDATE wenjin_classroom_assets SET body=%s WHERE key=%s",(Jsonb(body),a["key"]))
    assert media.request(store,"p",cid,"pdf")["state"]=="queued"



def test_playback_does_not_promote_an_old_export_over_newest_generation(room,monkeypatch):
    store,cid=setup_room(room,monkeypatch)
    old=media.request(store,"p",cid,"voice",rate=1)
    latest=media.request(store,"p",cid,"voice",rate=1.25)
    with media.connection() as c:
        for a,created in [(old,1),(latest,2)]:
            body=media.get(store,"p",cid,a["key"])["body"]
            body.update(state="ready",duration=30,created=created)
            c.execute("UPDATE wenjin_classroom_assets SET body=%s WHERE key=%s",(Jsonb(body),a["key"]))
    media.control(store,"p",cid,old["key"],"playback",10,1)
    assert media.listing(store,"p",cid)[0]["key"]==latest["key"]
