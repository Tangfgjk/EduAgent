import hashlib

from sqlalchemy import select, update

from app.infrastructure.db.models import AuditEvent, ContentApproval, DevelopmentReview, DevelopmentReviewScope
from app.modules.errors import DomainError

REQUIRED_CONTENT = {'math.g9.probability.counting.dev1', 'math.g7.statistics.frequency.dev1', 'math.g9.probability.teach.dev2', 'math.g9.probability.roundtable.dev2'}


def content_lock(session, actor, target, version):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if session.bind.dialect.name == 'postgresql' else sqlite_insert
    session.execute(insert(ContentApproval).values(unit_id=target, version=version, enabled=False, revision=0,
        reviewer_id=actor.account.id).on_conflict_do_nothing(index_elements=['unit_id']))
    return session.get(ContentApproval, target, with_for_update=True, populate_existing=True)


def scope_id(kind, target, version):
    return hashlib.sha256((kind + ':' + target + ':' + version).encode()).hexdigest()


def records(session, kind, target, version):
    rows = session.scalars(select(DevelopmentReview).where(DevelopmentReview.scope_id == scope_id(kind, target, version))
        .order_by(DevelopmentReview.revision.desc()).limit(20))
    return [{'revision': row.revision, 'conclusion': row.conclusion, 'note': row.note,
        'created_at': row.created_at, 'review_kind': 'development_only'} for row in rows]


def check_content(session, target, version):
    rows = records(session, 'content', target, version)
    if (rows and rows[0]['conclusion'] != 'development_ready') or ((target in REQUIRED_CONTENT or target.startswith('dynamic.')) and not rows):
        raise DomainError(409, 'development_review_required', '此版本尚未通过开发内容核对，请先填写审核依据')


def content_target(target, session=None):
    from app.modules.learning.content_catalog import PACKS
    from app.modules.learning.activity_catalog import ACTIVITY_PACKS
    from app.modules.learning.content_repository import ContentRepository
    repo = ContentRepository(session, include_private=True)
    item = next((item for item in (*repo.packs(), *repo.activities()) if item.unit_id == target), None)
    if item is None:
        raise DomainError(404, 'not_found', '未找到此内容版本')
    return item


def save(session, actor, body):
    kind, target, version = body['kind'], body['target_id'], body['version']
    if kind == 'content':
        item = content_target(target, session)
        actual = item.version
        allowed = {'development_ready', 'needs_revision'}
    else:
        from app.modules.governance.workbench import case_target
        actual = case_target(target)['report_sha256']
        allowed = {'acceptable', 'model_issue', 'reference_ambiguous', 'runtime_issue'}
    if actual != version:
        raise DomainError(409, 'version_conflict', '来源版本已变化，请重新查看后复核')
    if body['conclusion'] not in allowed:
        raise DomainError(422, 'invalid_review', '此类复核不支持该结论')
    approval = content_lock(session, actor, target, version) if kind == 'content' else None
    identifier = scope_id(kind, target, version)
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if session.bind.dialect.name == 'postgresql' else sqlite_insert
    session.execute(insert(DevelopmentReviewScope).values(id=identifier, revision=0).on_conflict_do_nothing(index_elements=['id']))
    updated = session.execute(update(DevelopmentReviewScope).where(DevelopmentReviewScope.id == identifier,
        DevelopmentReviewScope.revision == body['expected_revision']).values(revision=body['expected_revision'] + 1).returning(DevelopmentReviewScope.revision)).scalar()
    if updated is None:
        raise DomainError(409, 'revision_conflict', '复核意见已更新，请刷新后重试')
    session.add(DevelopmentReview(scope_id=identifier, revision=updated, kind=kind, target_id=target, version=version,
        conclusion=body['conclusion'], note=body['note'], reviewer_id=actor.account.id))
    session.add(AuditEvent(actor_id=actor.account.id, action='development.review', target_id=identifier))
    if kind == 'content' and body['conclusion'] == 'needs_revision':
        if approval and approval.version == version and approval.enabled:
            approval.enabled, approval.revision = False, approval.revision + 1
            session.add(AuditEvent(actor_id=actor.account.id, action='content.review_withdraw', target_id=target))
    session.flush()
    return {'revision': updated, 'reviews': records(session, kind, target, version)}
