"""Workspace-owned immutable curricula and revisioned active unit selection."""
from sqlalchemy import select
from pydantic import Field
from app.infrastructure.db.models import CourseBinding, CurriculumVersion, AgentRun, AuditEvent
from app.modules.learning.content_contracts import CurriculumDraft
from app.modules.learning.contracts import Contract
from app.modules.learning.content_repository import ContentRepository, content_hash
from app.modules.learning.content_service import require_content
from app.modules.workspaces.service import Workspaces
from app.modules.errors import DomainError


class BindCourse(CurriculumDraft):
    expected_revision: int = Field(ge=0, strict=True)
    active_unit_id: str = Field(min_length=1, max_length=100)


class SelectUnit(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    active_unit_id: str = Field(min_length=1, max_length=100)


class CourseUnit(Contract):
    unit_id: str
    version: str
    title: str


class CourseContext(Contract):
    workspace_id: str
    revision: int
    curriculum_id: str | None = None
    curriculum_version: str | None = None
    topic: str | None = None
    title: str | None = None
    active_unit_id: str | None = None
    units: list[CourseUnit] = Field(default_factory=list)
    binding_required: bool = True


def context(session, actor, wid):
    Workspaces().owned(session, actor, wid)
    binding = session.get(CourseBinding, wid)
    if not binding:
        return {'workspace_id': wid, 'revision': 0, 'binding_required': True}
    course = session.get(CurriculumVersion, binding.curriculum_id)
    if course.workspace_id != wid:
        raise DomainError(409, 'course_scope_conflict', '课程不属于此空间')
    return {'workspace_id': wid, 'revision': binding.revision, 'curriculum_id': course.id,
        'curriculum_version': course.version, 'topic': course.snapshot['topic'], 'title': course.snapshot['title'],
        'active_unit_id': binding.active_unit_id, 'units': course.snapshot['units'], 'binding_required': False}


def _lock(session, actor, wid, expected):
    Workspaces().owned(session, actor, wid, active=True)  # serializes first bind and all starts
    binding = session.get(CourseBinding, wid, with_for_update=True, populate_existing=True)
    if (binding.revision if binding else 0) != expected:
        raise DomainError(409, 'revision_conflict', '课程或当前单元已变更，请刷新后重试')
    busy = session.scalar(select(AgentRun.id).where(AgentRun.workspace_id == wid,
        AgentRun.kind.in_(['tutor', 'roundtable', 'teach', 'strategy']), AgentRun.status.in_(['queued', 'awaiting_input'])).limit(1))
    if busy:
        raise DomainError(409, 'learning_busy', '请先完成或取消当前学习/策略任务，再调整课程单元')
    return binding


def bind(session, actor, wid, body: BindCourse):
    binding = _lock(session, actor, wid, body.expected_revision)
    if body.active_unit_id not in body.unit_ids:
        raise DomainError(422, 'unit_outside_curriculum', '当前单元必须属于课程')
    repo = ContentRepository(session, workspace_id=wid)
    units = []
    items = [repo.pack(unit_id) for unit_id in body.unit_ids]
    for item in sorted(items, key=lambda item: item.unit_id):
        require_content(session, actor.account.source_kind, item.unit_id, item.version, wid)
    for item in items:
        units.append({'unit_id': item.unit_id, 'version': item.version, 'title': item.title})
    snapshot = {'topic': body.topic.strip(), 'title': body.title.strip(), 'units': units}
    if not snapshot['topic'] or not snapshot['title']:
        raise DomainError(422, 'invalid_topic', '请填写主题和课程名称')
    course = CurriculumVersion(workspace_id=wid, version=content_hash(snapshot), snapshot=snapshot)
    session.add(course)
    session.flush()
    if binding:
        binding.curriculum_id, binding.active_unit_id = course.id, body.active_unit_id
        binding.revision += 1
    else:
        session.add(CourseBinding(workspace_id=wid, curriculum_id=course.id, active_unit_id=body.active_unit_id, revision=1))
    session.add(AuditEvent(actor_id=actor.account.id, action='course.bind', target_id=course.id))
    session.flush()
    return context(session, actor, wid)


def select_unit(session, actor, wid, body: SelectUnit):
    binding = _lock(session, actor, wid, body.expected_revision)
    if not binding:
        raise DomainError(409, 'course_required', '请先确认此空间的课程')
    require_bound_unit(session, wid, body.active_unit_id, allow_other_member=True)
    item = ContentRepository(session, workspace_id=wid).pack(body.active_unit_id)
    require_content(session, actor.account.source_kind, item.unit_id, item.version, wid)
    binding.active_unit_id, binding.revision = body.active_unit_id, binding.revision + 1
    session.add(AuditEvent(actor_id=actor.account.id, action='course.select_unit', target_id=wid))
    session.flush()
    return context(session, actor, wid)


def require_bound_unit(session, wid, unit_id, *, allow_other_member=False):
    binding = session.get(CourseBinding, wid)
    if not binding:
        # Compatibility for existing static API clients/runs. Dynamic content must
        # always have an explicitly confirmed curriculum; UI binds all new routes.
        if unit_id.startswith('dynamic.'):
            raise DomainError(409, 'course_required', '请先确认课程与当前单元')
        return
    course = session.get(CurriculumVersion, binding.curriculum_id)
    if course.workspace_id != wid or not any(u['unit_id'] == unit_id for u in course.snapshot['units']):
        raise DomainError(409, 'unit_outside_curriculum', '此单元不属于当前空间课程')
    if not allow_other_member and unit_id != binding.active_unit_id:
        raise DomainError(409, 'unit_context_conflict', '请使用空间当前单元；如需调整，请先切换课程单元')
