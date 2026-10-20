"""Owned, resumable next action derived from real persistent state."""
from sqlalchemy import select
from app.infrastructure.db.models import AgentRun, CourseBinding, CourseBuild
from app.modules.learning.contracts import Contract
from app.modules.workspaces.service import Workspaces


class NextAction(Contract):
    workspace_id: str
    stage: str
    title: str
    explanation: str
    destination: str
    run_id: str | None = None
    read_only: bool = False


def next_action(session, actor, wid):
    space = Workspaces().owned(session, actor, wid)
    def result(stage, title, reason, path, run_id=None):
        return NextAction(workspace_id=wid, stage=stage, title=title, explanation=reason,
            destination=path, run_id=run_id, read_only=space.status != 'active')
    if space.status != 'active':
        return result('archived', '回顾学习记录', '此空间已归档，恢复后可继续学习。', 'evidence')
    pending = session.scalar(select(AgentRun).where(AgentRun.workspace_id == wid,
        AgentRun.status.in_(['queued', 'awaiting_input']), AgentRun.kind.in_(['tutor', 'teach', 'roundtable', 'plan']))
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1))
    if pending:
        path = {'tutor': 'study', 'teach': 'teach', 'roundtable': 'roundtable', 'plan': 'plan'}[pending.kind]
        return result('resume', '继续尚未完成的学习', '已有表达和进度已保存，请接着当前任务继续。', path, pending.id)
    if not session.get(CourseBinding, wid):
        build = session.scalar(select(CourseBuild).where(CourseBuild.workspace_id == wid)
            .order_by(CourseBuild.created_at.desc(), CourseBuild.id.desc()).limit(1))
        return result('course', '继续准备课程' if build else '告诉我你想学什么',
            '课程目标需要明确确认；空间名称不会自动绑定单元。', 'courses')
    if space.kind == 'project' and not session.scalar(select(AgentRun.id).where(AgentRun.workspace_id == wid,
            AgentRun.kind == 'plan', AgentRun.status == 'completed').limit(1)):
        return result('plan', '确认目标与学习节奏', '已有课程，现在由你确认本次目标和时间安排。', 'plan')
    completed = session.scalar(select(AgentRun).where(AgentRun.workspace_id == wid, AgentRun.kind == 'tutor',
        AgentRun.status == 'completed').order_by(AgentRun.created_at.desc()).limit(1))
    if completed:
        return result('reflect', '看看证据，再选择复习', '本轮已经结束；先回顾独立检测与提示记录，再决定下一步。', 'evidence', completed.id)
    return result('learn', '开始学习与诊断', '课程与节奏已准备好，从自己的尝试开始。', 'study')
