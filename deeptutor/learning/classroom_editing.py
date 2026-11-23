"""Durable, owner-scoped lesson drafts and immutable published editions."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import math
import time
import uuid

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from deeptutor.learning import classroom as cr
from deeptutor.learning.personal_training import scope


class PageChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=12000)
    steps: list[str] | None = Field(default=None, max_length=12)
    narration: str | None = Field(default=None, max_length=12000)
    formula: str | None = Field(default=None, max_length=250)
    panels: list[dict[str, str]] | None = Field(default=None, min_length=1, max_length=4)
    layout: str | None = None
    notes: str | None = Field(default=None, max_length=3000)
    spoken_script: str | None = Field(default=None, max_length=1800)
    lines: list[list[float]] | None = Field(default=None, max_length=3)


def initialize(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS wenjin_classroom_drafts(scope text NOT NULL,project text NOT NULL,parent_id text NOT NULL,id text PRIMARY KEY,body jsonb NOT NULL,updated double precision NOT NULL)"
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS wenjin_classroom_active_draft ON wenjin_classroom_drafts(scope,project,parent_id) WHERE body->>'state'='draft'"
    )


def fingerprint(page):
    return hashlib.sha256(
        json.dumps(
            {k: page.get(k) for k in ("title", "content", "steps", "narration", "visual")},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def source_hash(course):
    return hashlib.sha256(
        json.dumps(
            [course["outline"], course["pages"]], sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()


def public(d):
    course = cr.public(d["snapshot"])
    return {
        k: copy.deepcopy(d[k])
        for k in (
            "id",
            "parent_id",
            "revision",
            "state",
            "updated",
            "changed_page_ids",
        )
    } | {
        "parent_version": d["snapshot"]["version"],
        "pages": course["pages"],
        "page_order_changed": d.get("page_order_changed", False),
        "sources": course["sources"],
        "published_id": d.get("published_id"),
    }


def write(c, d):
    d["updated"] = time.time()
    c.execute(
        "UPDATE wenjin_classroom_drafts SET body=%s,updated=%s WHERE id=%s",
        (Jsonb(d), d["updated"], d["id"]),
    )


@contextmanager
def locked(store, project, cid, did):
    with cr.locked(store, project, cid) as (c, parent):
        initialize(c)
        row = c.execute(
            "SELECT body FROM wenjin_classroom_drafts WHERE scope=%s AND project=%s AND parent_id=%s AND id=%s FOR UPDATE",
            (scope(store), project, cid, did),
        ).fetchone()
        if not row:
            raise LookupError("课件草稿不存在")
        yield c, parent, row[0]


def start(store, project, cid):
    with cr.locked(store, project, cid) as (c, parent):
        if parent["state"] != "ready":
            raise RuntimeError("请先等待课堂全部生成完成，再编辑课件")
        if not any(p["kind"] != "quiz" for p in parent["pages"]):
            raise ValueError("本课堂只有测验页，暂无可编辑讲解内容")
        initialize(c)
        row = c.execute(
            "SELECT body FROM wenjin_classroom_drafts WHERE scope=%s AND project=%s AND parent_id=%s AND body->>'state'='draft' FOR UPDATE",
            (scope(store), project, cid),
        ).fetchone()
        if row:
            return public(row[0])
        d = {
            "id": uuid.uuid4().hex,
            "parent_id": cid,
            "revision": 1,
            "state": "draft",
            "snapshot": copy.deepcopy(parent),
            "source_hash": source_hash(parent),
            "changed_page_ids": [],
            "updated": time.time(),
        }
        c.execute(
            "INSERT INTO wenjin_classroom_drafts VALUES(%s,%s,%s,%s,%s,%s)",
            (scope(store), project, cid, d["id"], Jsonb(d), d["updated"]),
        )
        return public(d)


def get(store, project, cid, did):
    with locked(store, project, cid, did) as (_, _, d):
        return public(d)


def patch_page(page, changes):
    fields = PageChanges.model_validate(changes).model_dump(exclude_unset=True)
    if not fields or any(v is None for v in fields.values()):
        raise ValueError("请填写修改内容；清空讲稿或公式时使用空字符串")
    if any(k in fields and not fields[k].strip() for k in ("title", "content")):
        raise ValueError("标题和正文不能只包含空白")
    if page["kind"] == "quiz":
        raise ValueError("测验页及答案契约不在课件编辑范围内，请通过原任务核查或修复")
    result = copy.deepcopy(page)
    if page.get("visual"):
        from deeptutor.learning.teaching_deck import TeachingSlide

        visual = copy.deepcopy(page["visual"])
        if set(fields) - {
            "title",
            "content",
            "formula",
            "panels",
            "layout",
            "notes",
            "spoken_script",
            "lines",
        }:
            raise ValueError("视觉课件请编辑讲解区和配音讲稿")
        visible_changed = any(
            k in fields and fields[k] != visual.get(k) for k in ("content", "panels", "formula")
        )
        if visible_changed and fields.get(
            "spoken_script", visual.get("spoken_script", "")
        ) == visual.get("spoken_script", ""):
            fields["spoken_script"] = ""
        visual.update(fields)
        visual = TeachingSlide.model_validate(visual).model_dump()
        if any(not p["heading"].strip() or not p["text"].strip() for p in visual["panels"]):
            raise ValueError("讲解区标题和说明不能为空白")
        lines = visual["lines"]
        if any(
            len(line) != 3
            or not all(math.isfinite(n) and abs(n) <= 100000 for n in line)
            or line[0] == line[1] == 0
            for line in lines
        ):
            raise ValueError("每条坐标直线需有效的 a,b,c，且 a、b 不同时为零")
        if visual["layout"] == "coordinate" and (not lines or len(visual["panels"]) > 2):
            raise ValueError("坐标图需要直线参数，且最多两个讲解区")
        result.update(
            visual=visual,
            title=visual["title"],
            content=visual["content"],
            narration=visual["content"]
            + "\n"
            + "\n".join(p["heading"] + "：" + p["text"] for p in visual["panels"]),
        )
        from deeptutor.learning.slide_visuals import elements

        elements(result)  # Validate formula syntax and layout before saving.
    else:
        if set(fields) - {"title", "content", "steps", "narration"}:
            raise ValueError("本页使用图文版式，可修改正文、板书步骤和讲稿")
        if any(not step.strip() or len(step) > 2000 for step in fields.get("steps", [])):
            raise ValueError("板书每一步需为1至2000字")
        if "steps" in fields and page["kind"] != "whiteboard":
            raise ValueError("只有白板页可以修改步骤")
        if any(
            k in fields and fields[k] != page.get(k) for k in ("content", "steps")
        ) and fields.get("narration", page.get("narration", "")) == page.get("narration", ""):
            fields["narration"] = ""
        result.update(fields)
    return result


def save_page(store, project, cid, did, revision, page_id, changes):
    with locked(store, project, cid, did) as (c, parent, d):
        if d["state"] != "draft" or d["revision"] != revision:
            raise RuntimeError("草稿已变化或已发布，请重新加载后再编辑")
        page = next((p for p in d["snapshot"]["pages"] if p["id"] == page_id), None)
        if page is None:
            raise LookupError("课件页不存在")
        updated = patch_page(page, changes)
        if fingerprint(updated) == fingerprint(page):
            return public(d)
        updated.update(content_origin="learner_edit", content_review="unverified")
        page.update(updated)
        refresh_changes(d, parent)
        d["revision"] += 1
        write(c, d)
        return public(d)


def publish(store, project, cid, did, revision, reason):
    reason = reason.strip()
    if not 1 <= len(reason) <= 500:
        raise ValueError("请用1至500字说明这次修改")
    with locked(store, project, cid, did) as (c, parent, d):
        if d["state"] == "published" and d["revision"] == revision:
            return cr.load(store, project, d["published_id"])
        if d["state"] != "draft" or d["revision"] != revision:
            raise RuntimeError("草稿已变化，请重新加载后再发布")
        if parent["state"] != "ready" or source_hash(parent) != d["source_hash"]:
            raise RuntimeError("原课堂内容已变化，请从最新内容重新开始编辑")
        if not d["changed_page_ids"] and not d.get("page_order_changed"):
            raise ValueError("尚无内容修改，保存草稿后再发布")
        root = parent.get("edition", {}).get("root_id", cid)
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (scope(store) + project + root + ":editions",),
        )
        version = c.execute(
            "SELECT COALESCE(MAX((body->>'version')::integer),1)+1 FROM wenjin_classrooms WHERE scope=%s AND project=%s AND (id=%s OR body#>>'{edition,root_id}'=%s)",
            (scope(store), project, root, root),
        ).fetchone()[0]
        new = copy.deepcopy(d["snapshot"])
        new.update(
            id=uuid.uuid4().hex,
            version=version,
            created=time.time(),
            position=0,
            answers={},
            answer_outbox={},
            help={},
            conversation=[],
            request_ids=[],
            viewed=[],
            whiteboard_steps={},
            state="ready",
            attempts=0,
            lease=0,
            token=None,
        )
        for key in (
            "signature",
            "answer_store_root",
            "answer_retry_at",
            "completed_at",
            "ended_at",
            "resume_state",
            "error",
            "ask_requests",
            "generation_error",
            "retry_feedback",
        ):
            new.pop(key, None)
        new["outline"] = [
            {k: p[k] for k in ("title", "objective_id", "kind", "goal")} for p in new["pages"]
        ]
        new["edition"] = {
            "root_id": root,
            "parent_id": cid,
            "parent_version": parent["version"],
            "draft_id": did,
            "reason": reason,
            "changed_page_ids": d["changed_page_ids"],
            "published_at": time.time(),
            "review_state": "unverified",
        }
        c.execute(
            "INSERT INTO wenjin_classrooms VALUES(%s,%s,%s,%s,%s)",
            (scope(store), project, new["id"], Jsonb(new), time.time()),
        )
        d.update(state="published", published_id=new["id"])
        write(c, d)
        return cr.public(new)


def discard(store, project, cid, did, revision):
    with locked(store, project, cid, did) as (c, _, d):
        if d["state"] != "draft" or d["revision"] != revision:
            raise RuntimeError("草稿已变化或已经发布，请重新加载")
        d["state"] = "discarded"
        write(c, d)
        return {"discarded": True}


def editions(store, project, cid):
    with cr.locked(store, project, cid) as (c, d):
        root = d.get("edition", {}).get("root_id", cid)
        return [
            {
                "id": r[0]["id"],
                "version": r[0]["version"],
                "created": r[0]["created"],
                "parent_id": r[0].get("edition", {}).get("parent_id"),
                "reason": r[0].get("edition", {}).get("reason", "首次生成"),
            }
            for r in c.execute(
                "SELECT body FROM wenjin_classrooms WHERE scope=%s AND project=%s AND body->>'deleted_at' IS NULL AND (id=%s OR body#>>'{edition,root_id}'=%s) ORDER BY (body->>'version')::integer DESC LIMIT 100",
                (scope(store), project, root, root),
            )
        ]


def refresh_changes(d, parent):
    old = {p["id"]: p for p in parent["pages"]}
    new = {p["id"]: p for p in d["snapshot"]["pages"]}
    d["changed_page_ids"] = [
        key
        for key in dict.fromkeys([*old, *new])
        if key not in old or key not in new or fingerprint(old[key]) != fingerprint(new[key])
    ]
    d["page_order_changed"] = list(old) != list(new)


def arrange(store, project, cid, did, revision, action, page_id, position=None):
    with locked(store, project, cid, did) as (c, parent, d):
        if d["state"] != "draft" or d["revision"] != revision:
            raise RuntimeError("草稿已变化，请重新加载后操作")
        pages = d["snapshot"]["pages"]
        index = next((i for i, p in enumerate(pages) if p["id"] == page_id), None)
        if index is None:
            raise LookupError("课件页不存在")
        page = pages[index]
        if page["kind"] == "quiz":
            raise ValueError("测验页不能复制、删除或移动")
        selected = page_id
        if action == "duplicate":
            if len(pages) >= len(parent["pages"]) + 100:
                raise ValueError("本次草稿新增页面已达100页，请先发布再继续编辑")
            new = copy.deepcopy(page)
            selected = new["id"] = "edit-" + uuid.uuid4().hex
            new.update(content_origin="learner_edit", content_review="unverified")
            new["title"] = "新页：" + new["title"][:25]
            if new.get("visual"):
                new["visual"]["title"] = new["title"]
            pages.insert(index + 1, new)
        elif action == "delete":
            if not any(
                p["kind"] != "quiz"
                and p["objective_id"] == page["objective_id"]
                and p["id"] != page_id
                for p in pages
            ):
                raise ValueError("请保留该知识点至少一页讲解；可先复制并改写，再删除原页")
            pages.pop(index)
            selected = next(
                p["id"]
                for p in pages[max(0, index - 1) :] + pages[: max(0, index - 1)]
                if p["kind"] != "quiz"
            )
        elif action == "move":
            if position is None or not 0 <= position < len(pages):
                raise ValueError("目标页面位置无效")
            if position == index:
                return public(d) | {"selected_page_id": selected}
            pages.insert(position, pages.pop(index))
        else:
            raise ValueError("不支持的页面操作")
        d["snapshot"]["outline"] = [
            {k: p[k] for k in ("title", "objective_id", "kind", "goal")} for p in pages
        ]
        refresh_changes(d, parent)
        d["revision"] += 1
        write(c, d)
        return public(d) | {"selected_page_id": selected}
