import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

const html = fs.readFileSync(path.resolve(import.meta.dirname, '..', 'web', 'index.html'), 'utf8');

test('dashboard inline JavaScript parses', () => {
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
  assert.ok(scripts.length > 0, 'dashboard script missing');
  for (const script of scripts) new vm.Script(script);
});


test('live trade confirmation modal is wired to the token-protected API', () => {
  assert.match(html, /id="live-confirm"/);
  assert.match(html, /Approve &amp; send LIVE order/);
  assert.match(html, /\/api\/live-order\?/);
  assert.match(html, /decision=approve|decision/);
});
