import { mkdirSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { runInNewContext } from 'node:vm';
import { syntheticDemo } from './demo.mjs';
import { validateInput } from './input.mjs';

const source = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const scriptJson = value => JSON.stringify(value).replaceAll('<', '\\u003c');

function extract(text, pattern, label) {
  const match = text.match(pattern);
  if (!match) throw new Error(`Missing ${label} in the CRT source`);
  return match[0];
}

function crtPresets(shell) {
  const constant = (name, next) => {
    const start = shell.indexOf(`    const ${name} = `);
    const end = shell.indexOf(next, start + 1);
    if (start < 0 || end < 0) throw new Error(`Missing CRT declaration boundary for ${name}`);
    return runInNewContext(`${shell.slice(start, end)}\n${name}`, {}, { timeout: 100 });
  };
  return {
    profiles: constant('displayProfiles', '    const rasterModes'),
    rasters: constant('rasterModes', '    let activeDisplayProfile'),
    levels: constant('characterLevels', '    const adjustmentControls'),
    caps: constant('characterCaps', '    const commonSyntaxRules'),
    artifactKeys: [...constant('characterArtifactKeys', '    const characterBloomKeys')],
    bloomKeys: [...constant('characterBloomKeys', '    const characterJitterKeys')],
    jitterKeys: [...constant('characterJitterKeys', '    const characterCaps')],
  };
}

export function buildViewer(data, timing = null) {
  validateInput(data, timing);
  const shell = source('../docs/index.html');
  const parts = {
    STYLES: extract(shell, /<style>[\s\S]*?<\/style>/, 'optics CSS') + `<style>\n${source('screen.css')}</style>`,
    FILTERS: extract(shell, /<svg class="crt-filter-defs"[\s\S]*?<\/svg>/, 'frame filters'),
    DATA: scriptJson(data),
    TIMING: scriptJson(timing),
    VIEWER: source('viewer.js').replace('__CRT_PRESETS__', () => scriptJson(crtPresets(shell))),
  };
  return source('screen.html').replace(/\{\{([A-Z]+)\}\}/g, (_, key) => {
    if (!Object.hasOwn(parts, key)) throw new Error(`Unknown viewer template slot: ${key}`);
    return parts[key];
  });
}

function buildFromCommandLine() {
  const { values } = parseArgs({ options: { demo: { type: 'boolean' }, input: { type: 'string' }, timing: { type: 'string' } } });
  if (Boolean(values.demo) === Boolean(values.input) || (values.demo && values.timing)) {
    throw new Error('Use --demo, or --input <reviewed-export.json> [--timing <elapsed-timing.json>]');
  }
  const readJson = file => JSON.parse(readFileSync(file, 'utf8'));
  const { data, timing } = values.demo ? syntheticDemo() : { data: readJson(values.input), timing: values.timing ? readJson(values.timing) : null };
  const html = buildViewer(data, timing);
  const repo = realpathSync(new URL('../', import.meta.url));
  const scratch = join(repo, '.scratch'), dir = join(scratch, 'telemetry-viewer');
  for (const folder of [scratch, dir]) {
    mkdirSync(folder, { recursive: true, mode: 0o700 });
    if (realpathSync(folder) !== folder) throw new Error('Refusing to write through a linked output directory');
  }
  const output = join(dir, values.demo ? 'demo.html' : 'index.html');
  const temporary = join(dir, `${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, html, { flag: 'wx', mode: 0o600 });
    renameSync(temporary, output);
  } finally {
    rmSync(temporary, { force: true });
  }
  console.log(output);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  buildFromCommandLine();
}
