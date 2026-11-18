"""Transactional curriculum history; rollback never restores old learning evidence."""

from deeptutor.learning.models import LearningModule


def history(store, path_id):
    if store.load(path_id) is None:
        raise LookupError("Learning project not found")
    return [
        dict(id=e.id, revision=e.revision, created_at=e.created_at, **e.payload)
        for e in store.list_events(path_id)
        if e.event_type == "plan.versioned"
    ][-30:][::-1]


def rollback(store, path_id, event_id, revision):
    selected = next((e for e in history(store, path_id) if e["id"] == event_id), None)
    if selected is None:
        raise LookupError("Plan version not found")

    def change(tx):
        if tx.base_revision != revision:
            raise RuntimeError("学习状态已变化，请刷新后重新撤销")
        current = [m.model_dump(mode="json") for m in tx.progress.modules]
        if current != selected["after"]:
            raise RuntimeError("当前计划已变更，不能直接撤销这一历史版本")
        before = selected["before"]
        old = {k["id"] for m in before for k in m["knowledge_points"]}
        now = {k["id"] for m in current for k in m["knowledge_points"]}
        if old != now:
            raise ValueError("此版本涉及知识点增删，需导师协调后恢复，避免丢失当前任务关联")
        focus = (
            tx.progress.pending_question.knowledge_point_id
            if tx.progress.pending_question
            else None
        )
        if focus is None:
            current_module = next(
                (m for m in tx.progress.modules if m.id == tx.progress.current_module_id), None
            )
            if current_module and 0 <= tx.progress.current_kp_index < len(
                current_module.knowledge_points
            ):
                focus = current_module.knowledge_points[tx.progress.current_kp_index].id
        tx.progress.modules = [LearningModule.model_validate(m) for m in before]
        if focus:
            for module in tx.progress.modules:
                for index, point in enumerate(module.knowledge_points):
                    if point.id == focus:
                        tx.progress.current_module_id = module.id
                        tx.progress.current_kp_index = index
        tx.emit(
            "plan.rollback",
            {"version_id": event_id, "reason": "学生撤销计划调整；保留全部学习证据"},
        )

    updated, _ = store.mutate(path_id, change)
    return {"revision": updated.version, "history": history(store, path_id)}
