"""Start the prepared, pinned local resource service without opening a budget.

Requires built OpenMAIC and its Docker database. Credentials stay in .local.
Use --node for an installed Node >=24 executable when the default is older.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import httpx

REF='a9da60b3b4adb429369c0da13d5637a904ead2b0'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',default=os.getenv('WENJIN_OPENMAIC_NODE') or shutil.which('node'))
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    local=root/'.local';service=local/'services/OpenMAIC-v1.2.0'
    if not service.is_dir(): raise SystemExit('先按 92 号文档下载、配置、构建固定版本')
    actual=subprocess.check_output(['git','-C',str(service),'rev-parse','HEAD'],text=True).strip()
    if actual!=REF:raise SystemExit('OpenMAIC ref mismatch')
    middleware=(service/'middleware.ts').read_text(encoding='utf-8')
    if 'EduAgent resource service gate v1' not in middleware or "!['GET', 'HEAD'].includes(request.method)" not in middleware:
        raise SystemExit('先应用服务门禁补丁并重新构建')
    if not (service/'.next/BUILD_ID').is_file():raise SystemExit('先构建 OpenMAIC')
    node=args.node
    if not node: raise SystemExit('需要安装 Node24，或通过 --node 指定')
    version=subprocess.check_output([node,'--version'],text=True).strip()
    if int(version.lstrip('v').split('.')[0])<24:raise SystemExit('请使用 Node24 或更高版本')
    with httpx.Client(timeout=2,trust_env=False) as client:
        try:
            health=client.get('http://127.0.0.1:4004/api/health')
            if health.status_code==200:
                if client.get('http://127.0.0.1:4004/api/stages').status_code!=403:raise SystemExit('端口已有服务但访问门禁不符合要求')
                print('OpenMAIC already healthy on loopback 4004; budget unchanged.');return
        except httpx.HTTPError:pass
    result=subprocess.run(['docker','start','wenjin-openmaic-db'],capture_output=True)
    if result.returncode:raise SystemExit('OpenMAIC 独立数据库未准备；按 92 号文档建立，不输出敏感错误')
    stdout=(local/'openmaic.out.log').open('ab');stderr=(local/'openmaic.err.log').open('ab')
    process=subprocess.Popen([node,'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','4004'],cwd=service,
        stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    (local/'openmaic-process.json').write_text(json.dumps({'pid':process.pid,'port':4004,'ref':REF,'budget_changed':False}),encoding='utf-8')
    with httpx.Client(timeout=2,trust_env=False) as client:
        for _ in range(15):
            if process.poll() is not None:raise SystemExit('OpenMAIC exited; inspect local redacted logs')
            try:
                if client.get('http://127.0.0.1:4004/api/health').status_code==200:
                    print('OpenMAIC ready on loopback 4004; budget unchanged.');return
            except httpx.HTTPError:pass
            time.sleep(1)
    raise SystemExit('Service started but readiness was not confirmed; do not recreate a generation job')


if __name__=='__main__':main()
