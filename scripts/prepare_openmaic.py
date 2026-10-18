"""Prepare an isolated local OpenMAIC profile. Never print credentials.

This does not reset an existing budget. --activate-budget is only for a scope
explicitly approved by the product owner, after the EduAgent migration.
"""
import argparse
import hashlib
import json
import secrets
from pathlib import Path
from sqlalchemy import select
from app.config import Settings
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import ResourceBudget


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-budget',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    local=root/'.local';service=local/'services/OpenMAIC-v1.2.0'
    if not service.is_dir(): raise SystemExit('先下载并核对固定 OpenMAIC v1.2.0 源码')
    keyfile=local/'openmaic-proxy.key'
    if not keyfile.exists(): keyfile.write_text(secrets.token_urlsafe(48),encoding='utf-8');keyfile.chmod(0o600)
    proxy_key=keyfile.read_text(encoding='utf-8').strip()
    postgres=local/'openmaic-postgres.env'
    if not postgres.exists():
        postgres.write_text('POSTGRES_USER=openmaic\nPOSTGRES_DB=openmaic\nPOSTGRES_PASSWORD='+secrets.token_hex(24)+'\n',encoding='utf-8');postgres.chmod(0o600)
    password=next(line.split('=',1)[1] for line in postgres.read_text(encoding='utf-8').splitlines() if line.startswith('POSTGRES_PASSWORD='))
    config=service/'openmaic.yml'
    config.write_text('''providers:
  eduagent:
    preset: openai-compatible
    baseUrl: http://127.0.0.1:4003/internal/openmaic/v1
    apiKey: ${OPENMAIC_PROXY_KEY}
    models: [deepseek-chat, deepseek-flash]
slots:
  llm: eduagent:deepseek-chat
  webSearch: null
  image: null
  video: null
  tts: null
  asr: null
  document: null
lock: all
allowUserKeys: false
''',encoding='utf-8')
    env=service/'.env.local'
    if not env.exists():
        env.write_text('DATABASE_URL=postgresql://openmaic:'+password+'@127.0.0.1:55433/openmaic\n'+
            'OPENMAIC_PROXY_KEY='+proxy_key+'\nOPENMAIC_SECRET_KEY='+secrets.token_hex(32)+'\nOWNER_SINGLE_USER=false\nCOOKIE_SECURE=0\n',encoding='utf-8');env.chmod(0o600)
    if args.activate_budget:
        db=Database(Settings.load(local/'database.env').database_url);db.assert_ready()
        with db.transaction() as session:
            digest=hashlib.sha256(proxy_key.encode()).hexdigest()
            prior=session.scalar(select(ResourceBudget).where(ResourceBudget.secret_hash==digest).with_for_update())
            if prior is None: session.add(ResourceBudget(secret_hash=digest,enabled=True))
            # Existing counters and stop states are deliberately never reset.
        db.close()
    print(json.dumps({'profile_prepared':True,'budget_activation_requested':args.activate_budget,'credential_values_printed':False}))


if __name__=='__main__': main()
