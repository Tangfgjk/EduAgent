"""Read immutable static and DB content through one explicit session boundary.

Registered records are a shared development library, not student-generated private
artifacts. G03 will add owner-scoped generation. IDs identify immutable versions;
updates must register a new ID. Registration never enables or approves content.
"""
import hashlib
import json
from sqlalchemy import select
from app.infrastructure.db.models import GeneratedContentVersion, TaskVersion, ActivityVersion, AuditEvent, SystemState, ContentApproval
from app.modules.errors import DomainError
from app.modules.learning.content_contracts import GeneratedContentSpec, ValidationReport


def content_hash(body):
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ContentRepository:
    def __init__(self, session=None, workspace_id=None, include_private=False):
        self.session, self.workspace_id, self.include_private = session, workspace_id, include_private

    def visible(self, row):
        return self.include_private or row.workspace_id is None or row.workspace_id == self.workspace_id

    def packs(self):
        from app.modules.learning.content_catalog import PACKS
        dynamic = [] if self.session is None else [self._pack(row) for row in self.session.scalars(select(GeneratedContentVersion).order_by(GeneratedContentVersion.unit_id)) if self.visible(row)]
        return [*PACKS, *dynamic]

    def activities(self):
        from app.modules.learning.activity_catalog import ACTIVITY_PACKS
        dynamic = [] if self.session is None else [self._activity(row) for row in self.session.scalars(select(ActivityVersion).order_by(ActivityVersion.unit_id)) if self.visible(self.session.get(GeneratedContentVersion, row.parent_unit_id))]
        return [*ACTIVITY_PACKS, *dynamic]

    def pack(self, unit_id):
        from app.modules.learning.content_catalog import PACKS
        item = next((p for p in PACKS if p.unit_id == unit_id), None)
        if item:
            return item
        row = self.session.get(GeneratedContentVersion, unit_id) if self.session else None
        if row and self.visible(row):
            return self._pack(row)
        raise DomainError(404, 'content_not_found', '未找到此开发内容版本')

    def activity(self, unit_id=None, *, version=None):
        from app.modules.learning.activity_catalog import ACTIVITY_PACKS
        item = next((p for p in ACTIVITY_PACKS if (p.version == version if version else p.unit_id == unit_id)), None)
        if item:
            return item
        row = None
        if self.session is not None:
            row = self.session.scalar(select(ActivityVersion).where(ActivityVersion.version == version)) if version else self.session.get(ActivityVersion, unit_id)
        if row and self.visible(self.session.get(GeneratedContentVersion,row.parent_unit_id)):
            return self._activity(row)
        if version:
            raise DomainError(409, 'version_conflict', '互动版本已变化，请重新开始；历史记录仍保留')
        raise DomainError(404, 'activity_not_found', '未找到此开发互动版本')

    def _pack(self, row):
        from app.modules.learning.content_catalog import ContentPack
        body = row.spec
        tasks = {task.task_id: task.spec for task in self.session.scalars(select(TaskVersion).where(TaskVersion.unit_id == row.unit_id))}
        return ContentPack(row.unit_id, body['title'], body['grade'], body['domain'], tuple(body['knowledge_points']),
            tuple(body['prerequisites']), [tasks[t['id']] for t in body['tasks']], row.version,
            '登记内容仅检查结构和参考数值可解析性；题意、答案正确性与教学质量须审核，不面向真实学生。')

    def _activity(self, row):
        from app.modules.learning.activity_catalog import ActivityPack
        from app.modules.learning.output_gate import ROLE_QUESTIONS
        parent = self.pack(row.parent_unit_id)
        tasks = {t['id']: t for t in parent.tasks}
        spec = row.spec
        templates = {
            'teach': [{'speaker': '虚拟学伴', 'text': '你能说明这个方法的依据，以及如何检查它吗？', 'category': 'role_opinion'}],
            'roundtable': [{'speaker': name, 'text': text, 'category': 'role_opinion'} for name, text in zip(('思路提出者', '质疑者', '整理者'), ROLE_QUESTIONS.values())],
        }
        return ActivityPack(row.unit_id, spec['title'], parent.unit_id, tasks[spec['task_id']], templates, row.version,
            spec['kind'] + '_cycle_v1', spec.get('verification_task') or tasks[spec['verification_task_id']], parent)

    def register(self, actor, spec: GeneratedContentSpec, workspace_id=None, validation_report=None):
        if actor.account.role != 'admin':
            raise DomainError(403, 'role_forbidden', '仅管理员可登记开发内容')
        # A single registry namespace spans two tables. Serialize registrations
        # to prevent a pack ID racing an activity ID in a second transaction.
        self.session.get(SystemState, 1, with_for_update=True)
        ids = [spec.unit_id, *(a.unit_id for a in spec.activities)]
        all_content = ContentRepository(self.session, include_private=True)
        known = {p.unit_id for p in all_content.packs()} | {p.unit_id for p in all_content.activities()}
        if known.intersection(ids):
            raise DomainError(409, 'content_id_conflict', '内容 ID 已存在；修改内容须登记新 ID')
        for prerequisite in spec.prerequisites:
            self.pack(prerequisite)
        body = spec.model_dump()
        version = content_hash(body)
        self.session.add(GeneratedContentVersion(unit_id=spec.unit_id, version=version, spec=body, creator_id=actor.account.id, workspace_id=workspace_id,
            validation=validation_report or ValidationReport(status='unverifiable', verifier='bounded-rational.v1', scope='structure_only').model_dump()))
        self.session.flush()
        for task in spec.tasks:
            self.session.add(TaskVersion(unit_id=spec.unit_id, task_id=task.id, spec=task.model_dump()))
        for activity in spec.activities:
            self.session.add(ActivityVersion(unit_id=activity.unit_id, parent_unit_id=spec.unit_id,
                version=content_hash({'parent_version': version, 'activity': activity.model_dump()}), spec=activity.model_dump()))
        self.session.add(AuditEvent(actor_id=actor.account.id, action='content.register', target_id=spec.unit_id))
        self.session.flush()
        return self.inspect(spec.unit_id)

    def inspect(self, unit_id):
        row = self.session.get(GeneratedContentVersion, unit_id)
        if not row:
            raise DomainError(404, 'content_not_found', '未找到登记内容')
        approval = self.session.get(ContentApproval, unit_id)
        from app.modules.governance.reviews import records
        reviews = records(self.session, 'content', unit_id, row.version)
        ready = bool(reviews and reviews[0]['conclusion'] == 'development_ready')
        enabled = bool(ready and approval and approval.enabled and approval.version == row.version)
        return {'unit_id': row.unit_id, 'version': row.version, 'spec': row.spec,
            'validation': row.validation, 'status': 'enabled_development' if enabled else 'reviewed_not_enabled' if ready else 'registered_requires_review', 'created_at': row.created_at}
