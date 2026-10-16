"""只读、来源分组的过程读模型；不从交互次数推断能力。"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.infrastructure.db.models import FactJudgment, LearningFact, LearningInput, MemoryNote, Workspace, now
from app.modules.evidence.contracts import EvidenceCounts
from app.modules.workspaces.service import Workspaces

LOCAL_ZONE = timezone(timedelta(hours=8))
DIMENSIONS = ["数学抽象", "逻辑推理", "数学建模", "直观想象", "数学运算", "数据分析"]


def counts():
    return EvidenceCounts().model_dump()


def day(timestamp):
    return datetime.fromtimestamp(timestamp, LOCAL_ZONE).date().isoformat()


def category(fact):
    if fact.kind == "activity_contribution": return "activity"
    if fact.kind == "reflection": return "reflection"
    if fact.kind == "hint": return "hint"
    if fact.kind == "answer" and fact.payload["stage"] == "checkpoint": return "checkpoint"
    if fact.kind in {"answer", "review_answer"}: return "answer"
    return "process"


class Evidence:
    spaces = Workspaces()

    def report(self, session, actor, wid=None, days=30, source="all", record_kind="all", offset=0):
        self.spaces._student(actor)
        if wid:
            workspaces = [self.spaces.owned(session, actor, wid)]
        else:
            # 全局固定为本人正式项目；按固定顺序锁，与写入/归档互斥，多个读取顺序一致。
            workspaces = list(session.scalars(select(Workspace).where(Workspace.owner_id == actor.account.id,
                Workspace.kind == "project").order_by(Workspace.id).with_for_update()))
        by_id = {workspace.id: workspace for workspace in workspaces}
        timestamp = now()
        midnight = datetime.fromtimestamp(timestamp, LOCAL_ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
        start = int((midnight - timedelta(days=days - 1)).timestamp())
        groups, daily, stages = defaultdict(counts), defaultdict(counts), defaultdict(lambda: {"passed": 0, "failed": 0, "unverifiable": 0})
        records = []
        condition = [LearningFact.workspace_id.in_(by_id), LearningFact.created_at <= timestamp]
        if source != "all": condition.append(LearningFact.source_kind == source)
        # 同时取不可变原文，来源页仍通过所有权检查。旧事实也供窗口内的后续自述/复核引用。
        rows = session.execute(select(LearningFact, LearningInput).join(LearningInput, LearningFact.input_id == LearningInput.id).where(*condition)).all() if by_id else []
        facts = {fact.id: fact for fact, _ in rows}

        def bump(fact, at, key):
            groups[fact.source_kind][key] += 1
            daily[(day(at), fact.source_kind)][key] += 1

        def add_record(fact, identity, at, kind, status, text):
            workspace = by_id[fact.workspace_id]
            records.append({"record_id": identity, "fact_id": fact.id, "workspace_id": workspace.id,
                "workspace_title": workspace.title, "archived": workspace.status == "archived", "record_kind": kind,
                "source_kind": fact.source_kind, "stage": fact.payload["stage"], "status": status,
                "created_at": at, "task_id": fact.task_id, "excerpt": text[:160]})

        for fact, entry in rows:
            if fact.created_at < start: continue
            bump(fact, fact.created_at, "facts")
            kind = category(fact)
            if fact.kind in {"answer", "review_answer"}:
                bump(fact, fact.created_at, "answers")
                status = fact.payload["status"]
                stages[(fact.source_kind, fact.payload["stage"])][status] += 1
                if kind == "checkpoint":
                    bump(fact, fact.created_at, "checkpoint_answers")
                    if status == "passed": bump(fact, fact.created_at, "checkpoint_passed")
            elif kind == "hint": bump(fact, fact.created_at, "hints")
            elif kind == "reflection": bump(fact, fact.created_at, "reflections")
            elif kind == "activity": bump(fact, fact.created_at, "activity_contributions")
            text = entry.payload.get("reflection") or entry.payload.get("reasoning") or entry.payload.get("answer") or fact.payload.get("revealed_hint") or "学习过程记录"
            add_record(fact, fact.id, fact.created_at, kind, fact.payload["status"], text)

        # 使用 JOIN 避免将大批事实 ID 作为 IN 参数；来源与时间按原事实范围再次过滤。
        if by_id:
            for model, kind, key in [(MemoryNote, "self_report", "self_reports"), (FactJudgment, "human_judgment", "human_judgments")]:
                items = session.scalars(select(model).join(LearningFact, model.fact_id == LearningFact.id)
                    .where(*condition, model.created_at >= start, model.created_at <= timestamp))
                for item in items:
                    fact = facts[item.fact_id]
                    bump(fact, item.created_at, key)
                    add_record(fact, item.id, item.created_at, kind, item.conclusion if kind == "human_judgment" else None,
                               item.note if kind == "human_judgment" else item.text)
        # 来源记录筛选/分页只影响下方列表，不改变上方整个范围的统计。
        filtered = [record for record in records if record_kind == "all" or record["record_kind"] == record_kind]
        filtered.sort(key=lambda record: (record["created_at"], record["record_id"]), reverse=True)
        return {"scope": "workspace" if wid else "global", "workspace_count": len(workspaces), "days": days,
            "starts_at": start, "as_of": timestamp,
            "groups": [{"source_kind": source, **value} for source, value in sorted(groups.items())],
            "daily": [{"day": date, "source_kind": source, **value} for (date, source), value in sorted(daily.items())],
            "stages": [{"source_kind": source, "stage": stage, **value} for (source, stage), value in sorted(stages.items())],
            "competencies": [{"name": name} for name in DIMENSIONS],
            "records": filtered[offset:offset + 20], "record_count": len(filtered), "offset": offset}
