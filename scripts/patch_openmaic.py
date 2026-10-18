"""Apply the small, fail-closed service boundary to the pinned upstream source.

The upstream MIT license remains in the checkout. This integration is a service
adapter patch, not a claim that the unmodified upstream enforces our budget.
"""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent.parent
SERVICE=ROOT/'.local/services/OpenMAIC-v1.2.0'
REF='a9da60b3b4adb429369c0da13d5637a904ead2b0'


def main():
    actual=subprocess.check_output(['git','-C',str(SERVICE),'rev-parse','HEAD'],text=True).strip()
    if actual!=REF: raise SystemExit('Ref mismatch; do not patch a different upstream version')
    path=SERVICE/'middleware.ts';source=path.read_text(encoding='utf-8')
    marker='// EduAgent resource service gate v1'
    if marker not in source:
        anchor='  const { pathname } = request.nextUrl;'
        if source.count(anchor)!=1: raise SystemExit('Expected middleware seam not found')
        gate='''
  // EduAgent resource service gate v1: browser clients never receive this key.
  // Only health and capability probes are public; all owner/data/model routes
  // require the private backend credential. Budgets still live in EduAgent.
  if ((pathname.startsWith('/api/') && !['/api/health', '/api/generate-classroom/capabilities'].includes(pathname)) || !['GET', 'HEAD'].includes(request.method)) {
    const expected = process.env.WENJIN_RESOURCE_GATE_KEY;
    if (!expected || request.headers.get('x-eduagent-service-key') !== expected) {
      return new NextResponse('Forbidden', { status: 403 });
    }
  }
'''
        path.write_text(source.replace(anchor,anchor+gate),encoding='utf-8')
    patched=path.read_text(encoding='utf-8')
    patched=patched.replace("if (pathname.startsWith('/api/') && !['/api/health', '/api/generate-classroom/capabilities'].includes(pathname)) {","if ((pathname.startsWith('/api/') && !['/api/health', '/api/generate-classroom/capabilities'].includes(pathname)) || !['GET', 'HEAD'].includes(request.method)) {")
    path.write_text(patched,encoding='utf-8')
    ignore=SERVICE/'.dockerignore'
    patterns=ignore.read_text(encoding='utf-8')
    if '**/node_modules' not in patterns:
        ignore.write_text(patterns+'\n**/node_modules\n**/.next\n',encoding='utf-8')
    env=SERVICE/'.env.local'
    value=env.read_text(encoding='utf-8')
    if 'WENJIN_RESOURCE_GATE_KEY=' not in value:
        value+='WENJIN_RESOURCE_GATE_KEY='+(ROOT/'.local/openmaic-proxy.key').read_text(encoding='utf-8').strip()+'\n'
        env.write_text(value,encoding='utf-8');env.chmod(0o600)
    print('Fixed-ref resource service boundary prepared; rebuild and restart required.')


if __name__=='__main__': main()
