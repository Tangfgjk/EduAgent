import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve, join, relative, isAbsolute } from 'node:path';
import openapiTS, { astToString } from 'openapi-typescript';

const root = fileURLToPath(new URL('../../', import.meta.url));
const scratch = resolve(root, '.local');
mkdirSync(scratch, { recursive: true });
const temp = mkdtempSync(join(scratch, 'openapi-'));
try {
  const python = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const jsonPath = resolve(temp, 'schema.json');
  execFileSync(python, [resolve(root, 'scripts/export_openapi.py'), jsonPath], { cwd: root, stdio: 'inherit' });
  const result = '// Generated from FastAPI. Run npm run generate:api; do not edit.\n' + astToString(await openapiTS(pathToFileURL(jsonPath)));
  const output = resolve(root, 'frontend/packages/api-client/src/schema.d.ts');
  if (process.argv.includes('--check')) {
    if (readFileSync(output, 'utf8') !== result) throw new Error('API types are stale; run npm run generate:api');
    process.stdout.write('API types match FastAPI.\n');
  } else {
    mkdirSync(resolve(output, '..'), { recursive: true });
    writeFileSync(output, result);
    process.stdout.write('API types generated.\n');
  }
} finally {
  const within = relative(scratch, resolve(temp));
  if (!within || within.startsWith('..') || isAbsolute(within)) throw new Error('Unsafe temporary directory');
  rmSync(temp, { recursive: true, force: true });
}
