"""Deploy the already built pinned text-only resource image; never arm a budget."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
LOCAL=ROOT/'.local'
SERVICE=LOCAL/'services/OpenMAIC-v1.2.0'
REF='a9da60b3b4adb429369c0da13d5637a904ead2b0'
IMAGE='wenjin-openmaic:1.2.0-text'


def run(args):
    result=subprocess.run(args,capture_output=True)
    if result.returncode:raise SystemExit('Deployment command failed; inspect local Docker state without exposing environment values')
    return result.stdout


def main():
    if run(['git','-C',str(SERVICE),'rev-parse','HEAD']).decode().strip()!=REF:raise SystemExit('Ref mismatch')
    run(['docker','image','inspect',IMAGE,'--format','{{.Id}}'])
    # Reuse an existing deployment, never delete its data or recreate a remote job.
    existing=subprocess.run(['docker','container','inspect','wenjin-openmaic-service','--format','{{.State.Running}}'],capture_output=True)
    if existing.returncode==0:
        run(['docker','start','wenjin-openmaic-service']);print('Existing OpenMAIC Docker service started; budget unchanged.');return
    if subprocess.run(['docker','network','inspect','wenjin-resources'],capture_output=True).returncode:
        run(['docker','network','create','wenjin-resources'])
    networks=json.loads(run(['docker','inspect','wenjin-openmaic-db','--format','{{json .NetworkSettings.Networks}}']))
    if 'wenjin-resources' not in networks:run(['docker','network','connect','wenjin-resources','wenjin-openmaic-db'])
    env=(SERVICE/'.env.local').read_text(encoding='utf-8').replace('@127.0.0.1:55433/','@wenjin-openmaic-db:5432/')
    target=LOCAL/'openmaic-docker.env';target.write_text(env,encoding='utf-8');target.chmod(0o600)
    profile=LOCAL/'openmaic-docker.yml';profile.write_text((SERVICE/'openmaic.yml').read_text(encoding='utf-8').replace('http://127.0.0.1:4003/','http://host.docker.internal:4003/'),encoding='utf-8');profile.chmod(0o600)
    record=LOCAL/'openmaic-process.json'
    if record.is_file():
        previous=json.loads(record.read_text(encoding='utf-8-sig'))
        if previous.get('pid'):
            pid=int(previous['pid'])
            result=run(['powershell','-NoProfile','-Command',f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"]).decode(errors='replace')
            if 'next' not in result or '4004' not in result:raise SystemExit('Native process identity mismatch; do not stop another process')
            run(['powershell','-NoProfile','-Command',f'Stop-Process -Id {pid} -Force'])
    run(['docker','run','-d','--name','wenjin-openmaic-service','--network','wenjin-resources','--restart','unless-stopped',
        '--env-file',str(target),'-p','127.0.0.1:4004:3000','--mount',f'type=bind,source={profile},target=/app/openmaic.yml,readonly',
        '-v','wenjin-openmaic-resources:/app/data',IMAGE])
    record.write_text(json.dumps({'runtime':'docker','container':'wenjin-openmaic-service','port':4004,'ref':REF,'budget_changed':False}),encoding='utf-8')
    print('OpenMAIC Docker service created on loopback 4004; budget unchanged.')


if __name__=='__main__':main()
