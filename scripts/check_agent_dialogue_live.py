"""Explicit, bounded DeepSeek smoke test; isolated synthetic DB, three calls, no retry.

Run from the repository root with --run-paid. Only model ID is read from the
active configuration; no business account, learning text or database is mutated.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.config import Settings
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import ModelConfiguration, ModelPolicy
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.worker import Worker
from tests.test_s02 import Harness
from tests.test_agent_dialogue import activated
from tests.test_model_runtime import publish
from tests.test_s05 import send
from app.modules.learning.content_catalog import get_pack


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-paid', action='store_true')
    args=parser.parse_args()
    if not args.run_paid:parser.error('Explicit --run-paid required (at most three real calls, 512 output tokens each)')
    source=Database(Settings.load(Path('.local/database.env')).database_url)
    try:
        with source.sessions() as session:
            policy=session.get(ModelPolicy,1)
            config=session.get(ModelConfiguration,policy.active_id)
            if config.vendor!='deepseek':raise SystemExit('Active provider must be DeepSeek')
            model=config.model
    finally:source.close()
    target=Path('.local/agent-dialogue-live')/uuid4().hex
    target.mkdir(parents=True)
    db=Database('sqlite:///'+(target/'probe.sqlite3').resolve().as_posix());db.migrate()
    results=[]
    try:
        with ExitStack() as stack:
            env=Harness(db,stack)
            client,wid,unit=activated(env) # Fake course creation, self-authored text only.
            publish(env,vendor='deepseek',model=model,account_daily_calls=0,site_daily_calls=0)
            for kind in ('tutor','teach','roundtable'):
                root=f'/api/v1/student/workspaces/{wid}/'+('tutoring' if kind=='tutor' else kind)
                response=client.post(root,headers={'Idempotency-Key':'start-'+kind},json={'unit_id':unit if kind=='tutor' else unit+'.'+kind})
                assert response.status_code==202,response.text
                Worker(db,enable_models=False).step();path=root+'/'+response.json()['run_id']
                if kind=='tutor':
                    with db.sessions() as s:pack=get_pack(unit,s)
                    send(env,client,path,'answer',answer=pack.tasks[0]['answer']);send(env,client,path,'next')
                    fields={'action':'hint','coaching_text':'我知道先算除法，但把负数除以正数当成了正数，应该怎样自己检查？'}
                else:
                    fields={'action':'contribute','answer':'0','reasoning':'我先算除法再相加，但不清楚负号参与除法的依据，请根据这部分讲解提问。','request_model':True}
                run=client.get(path).json()
                response=client.post(path+'/inputs',headers={'Idempotency-Key':'model-'+kind},json={
                    'expected_revision':run['revision'],'gate_id':run['gate_id'],**fields})
                assert response.status_code==202,response.text
                Worker(db,enable_models=False).step()
                oid=client.get(path).json()['coaching']['operation_id']
                from app.llm.catalog import provider as configured_provider
                class RecordedProvider:
                    async def generate(self,request):
                        value=await configured_provider('deepseek',model,512).generate(request)
                        (target/(kind+'-response.txt')).write_text(value.text,encoding='utf-8')
                        return value
                ModelTaskRunner(db,lambda c,s:RecordedProvider()).step(operation_id=oid) # Exactly one, no retry.
                run=client.get(path).json();op=run['coaching']
                results.append({'kind':kind,'status':op['status'],'error_code':op.get('error_code'),
                                'usage':op.get('usage'),'result':op.get('result')})
                client.post(path+'/cancel',json={'expected_revision':run['revision']})
                print(kind,op['status'],op.get('error_code'))
        (target/'summary.json').write_text(json.dumps({'model':model,'max_real_calls':3,'automatic_retry':False,
            'data':'self_authored_synthetic_only','results':results},ensure_ascii=False,indent=2),encoding='utf-8')
        print('summary:',target.as_posix()+'/summary.json')
        if any(row['status']!='succeeded' for row in results):raise SystemExit(1)
    finally:db.close()


if __name__=='__main__':main()
