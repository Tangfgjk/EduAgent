"""Track goals outside the admitted catalog without presenting a fabricated plan.

The local source scan produces research candidates only. It does not create or
approve course assets; that remains the course-governance workflow's job.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from app.core.schema import utcnow
from app.learning.assets import AssetCatalog
from app.learning.goal_scope import goal_target_kcs


def _topic(goal_text: str) -> str:
    text = unicodedata.normalize("NFKC", goal_text).strip()
    text = re.sub(r"^(我想|我要|希望|计划|准备)?(学习|掌握|了解|复习|研究)", "", text)
    return text.strip(" ：:，。 ") or goal_text.strip()


def local_source_candidates(goal_text: str, root: Path) -> list[dict]:
    """Exact topic mentions in the trusted local source root, never web claims."""
    topic = _topic(goal_text)
    if len(topic) < 2 or not root.is_dir():
        return []
    candidates = []
    for path in sorted(root.glob("*.md")):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        content = path.read_text(encoding="utf-8-sig")
        if topic not in content and topic not in path.name:
            continue
        candidates.append({"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "match": "exact_topic_mention", "verified_for_teaching": False})
        if len(candidates) == 5:
            break
    return candidates


class CourseRequestService:
    def __init__(self, store, catalog: AssetCatalog, source_root: Path):
        self.store = store
        self.catalog = catalog
        self.source_root = source_root
        with store.lock:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS course_requests (
                request_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL,
                normalized_goal TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL)""")
            store.conn.execute("CREATE INDEX IF NOT EXISTS course_requests_by_learner ON course_requests(learner_id,created_at)")
            store.conn.commit()

    def submit(self, learner_id: str, goal_text: str) -> dict:
        goal_text = goal_text.strip()
        if not 2 <= len(goal_text) <= 120:
            raise ValueError("学习目标需为 2 至 120 个字符")
        if goal_target_kcs(goal_text, self.catalog):
            raise ValueError("该目标已有课程，请直接建立学习目标")
        normalized = unicodedata.normalize("NFKC", goal_text).casefold().replace(" ", "")
        with self.store.lock:
            existing = self.store.conn.execute(
                "SELECT payload FROM course_requests WHERE learner_id=? AND normalized_goal=? AND status IN ('awaiting_sources','needs_review') ORDER BY created_at DESC LIMIT 1",
                (learner_id, normalized)).fetchone()
            if existing:
                return json.loads(existing[0])
            sources = local_source_candidates(goal_text, self.source_root)
            record = {
                "request_id": "course-" + uuid4().hex,
                "learner_id": learner_id,
                "goal_text": goal_text,
                "topic": _topic(goal_text),
                "status": "needs_review" if sources else "awaiting_sources",
                "created_at": utcnow().isoformat(),
                "analysis": {
                    "catalog_match": False,
                    "local_source_candidates": sources,
                    "search_scope": "trusted_local_markdown_only",
                    "candidate_only": True,
                    "next_steps": ["补充可信课程资料", "建立知识点和先修关系", "制作可验证题目与提示", "课程审核后发布"],
                },
            }
            self.store.conn.execute("INSERT INTO course_requests VALUES (?,?,?,?,?,?)",
                                    (record["request_id"], learner_id, normalized, record["status"],
                                     record["created_at"], json.dumps(record, ensure_ascii=False)))
            self.store.conn.commit()
            return record

    def list_for(self, learner_id: str) -> list[dict]:
        with self.store.lock:
            rows = self.store.conn.execute("SELECT payload FROM course_requests WHERE learner_id=? ORDER BY created_at DESC",
                                           (learner_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]
