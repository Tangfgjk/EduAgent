"""One explicitly authorized DeepSeek development probe in a new isolated database.

Never run as a production worker. Uses self-authored fixture learning, one model
request, no retry, and writes only a safe summary into the ignored local folder.
"""
import argparse
from datetime import datetime,timezone,timedelta
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.infrastructure.db.database import Database
from app.infrastructure.runtime.model_tasks import ModelTaskRunner,model_key
from app.worker import Worker
from tests.test_s02 import Harness
from tests.test_g08 import ready
from tests.test_model_runtime import publish


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-one-authorized-call',action='store_true')
    args=parser.parse_args()
    if not args.execute_one_authorized_call:parser.error('需要明确选择单次授权调用')
    if not model_key():raise SystemExit('本机DeepSeek密钥未配置；没有发送请求')
    folder=Path('.local/semantic-rsi-live')/uuid4().hex;folder.mkdir(parents=True)
    db=Database('sqlite:///'+(folder/'probe.sqlite3').resolve().as_posix());db.migrate()
    try:
        with ExitStack() as stack:
            env=Harness(db,stack);client,wid,_=ready(env)
            publish(env,vendor='deepseek',account_daily_calls=30,site_daily_calls=100)
            root=f'/api/v1/student/workspaces/{wid}/skill-evolutions'
            response=client.post(root,headers={'Idempotency-Key':'authorized-one-call'},json={'mode':'semantic','allow_personal_trial':True})
            if response.status_code!=202:raise SystemExit('单次探针未入队，HTTP '+str(response.status_code))
            rid=response.json()['run_id'];path=root+'/'+rid
            if not Worker(db,enable_models=False).step():raise SystemExit('教学准备未完成，未调用外部模型')
            waiting=client.get(path).json();operation=waiting.get('generation')
            if not operation:raise SystemExit('未预约模型任务；没有发送请求')
            ModelTaskRunner(db).step(operation_id=operation['operation_id'])
            run=client.get(path).json()
            summary={'schema_version':'semantic-rsi-live-probe.v1','date':datetime.now(timezone(timedelta(hours=8))).date().isoformat(),'provider':'deepseek',
                'self_authored_development':True,'external_call_attempts':1,'automatic_retries':0,'max_output_tokens':2048,
                'evidence_records':len((run.get('dataset') or {}).get('semantic_evidence',[])),
                'phase':run['phase'],'generation':run.get('generation'),'trial_status':(run.get('trial') or {}).get('status'),
                'mechanism_eligible':(run.get('evaluation') or {}).get('eligible_for_personal_trial'),
                'effect_status':'not_validated','error_code':run.get('error_code')}
            (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(summary,ensure_ascii=False))
            print('Local evidence: '+(folder/'summary.json').as_posix())
    finally:db.close()


if __name__=='__main__':main()
