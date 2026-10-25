"""One explicitly approved development TTS probe, idempotent ledger, no retry.

Does not modify the exhausted six-submit media batch. Run only with an explicit
new human authorization. Secrets are read locally and never printed.
"""
import json
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select
from app.config import Settings
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import MediaBudget, MediaOperation, ResourceArtifact, AuditEvent, now
from app.modules.resources.media import VERSION, MODELS, PRICE, GROUP_CAP, sources, MediaRunner
from app.modules.learning.content_repository import content_hash
from app.modules.errors import DomainError


def main():
    ledger=Path('.local/tts-once-20261026.json')
    if ledger.exists():
        raise SystemExit('This one-submit authorization already has a ledger; no retry allowed.')
    settings=Settings.load(Path('.local/database.env'))
    if not settings.database_url:raise SystemExit('Explicit development database required.')
    db=Database(settings.database_url)
    text='计算有理数混合运算时，先看括号，再看运算顺序。请暂停，自己说明这一步用了什么规则，最后用另一种方法检查结果。'
    assert len(text)<=150 and len(text)*PRICE['audio']<=20_000
    authorization='human_single_tts_150_chars_002_yuan_no_retry'
    with db.transaction() as session:
        if session.scalar(select(MediaBudget.id).where(MediaBudget.body['authorization'].as_string()==authorization)):
            raise SystemExit('Database already records this one-submit authorization; no retry allowed.')
        target=None
        for resource in session.scalars(select(ResourceArtifact).where(ResourceArtifact.status=='approved').order_by(ResourceArtifact.created_at.desc())):
            if not resource.body.get('classroom') or len(resource.body['classroom']['scenes'])!=3:continue
            budget=MediaBudget(id=str(uuid5(NAMESPACE_URL,'EduAgent:'+authorization)),workspace_id=resource.workspace_id,resource_id=resource.id,enabled=True,revision=1,
                body={'version':VERSION,'resource_hash':resource.body.get('media_base_hash',resource.sha256),
                    'total_cap_microyuan':20_000,'reserved_microyuan':len(text)*PRICE['audio'],
                    'caps':{'image':0,'audio':1,'video':0},'prices_microyuan':PRICE,'group_caps_microyuan':GROUP_CAP,
                    'approved_at':now(),'expires_at':now()+300,'authorization':authorization,
                    'notice':'一次限定配音复测；无学生数据，无重试，不续期旧预算。'})
            try:sources(session,budget)
            except DomainError:continue
            target=budget;break
        if not target:raise SystemExit('No active, approved, adult/synthetic three-scene source outside independent assessment. Nothing submitted.')
        session.add(target);session.flush()
        request={'kind':'audio','model':MODELS['audio'],'prompt':text,'scene':0,'region':'cn-beijing',
                 'resource_hash':target.body['resource_hash'],'size':None,'duration':None,'voice':'Cherry'}
        operation=MediaOperation(budget_id=target.id,slot='audio:0',request=request,request_hash=content_hash(request),reserved_microyuan=len(text)*PRICE['audio'])
        session.add(operation);session.flush()
        session.add(AuditEvent(action='media.single_tts_authorized',target_id=target.id))
        report={'budget_id':target.id,'operation_id':operation.id,'characters':len(text),'submit_cap':1,
                'cap_microyuan':20_000,'reserved_microyuan':operation.reserved_microyuan,'status':'reserved_no_retry'}
        # Exclusive file creation precedes external I/O; a crash never leads to a
        # second submit. Database and file also preserve uncertain reservations.
        with ledger.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    MediaRunner(db).step(operation_id=report['operation_id'])
    with db.transaction() as session:
        operation=session.get(MediaOperation,report['operation_id'])
        budget=session.get(MediaBudget,report['budget_id']);budget.enabled=False;budget.revision+=1
        report.update(status=operation.status,receipt=operation.receipt)
    ledger.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    db.close()


if __name__=='__main__':main()
