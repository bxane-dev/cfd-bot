import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(import.meta.dirname, '..');
const main = fs.readFileSync(path.join(root, 'desktop', 'main.js'), 'utf8');
const backend = fs.readFileSync(path.join(root, 'desktop', 'backend_entry.py'), 'utf8');
const buildBat = fs.readFileSync(path.join(root, 'BUILD_APP.bat'), 'utf8');
const buildShortcut = fs.readFileSync(path.join(root, 'build.bat'), 'utf8');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));

test('desktop app runs the CFD engine hidden in background', () => {
  assert.match(main, /windowsHide:\s*true/);
  assert.match(main, /cfd-engine\.exe/);
  assert.match(main, /CFD_DATA_DIR/);
  assert.match(main, /--desktop/);
  assert.match(main, /nodeIntegration:\s*false/);
  assert.match(main, /contextIsolation:\s*true/);
});

test('desktop app uses one persistent user-data folder', () => {
  assert.match(main, /app\.getPath\('userData'\)/);
  assert.match(backend, /CFD_DATA_DIR/);
  assert.match(backend, /config\.yaml/);
  assert.match(backend, /\.env\.example/);
});

test('Windows build emits installer and portable app with custom icon', () => {
  const targets = pkg.build.win.target.map(x => x.target);
  assert.ok(targets.includes('nsis'));
  assert.ok(targets.includes('portable'));
  assert.equal(pkg.build.win.icon, 'assets/app-icon.ico');
  assert.match(buildBat, /app-icon\.b64/i);
  assert.match(buildBat, /app-icon-source\.jpg/i);
  assert.match(buildBat, /pillow/i);
  assert.match(buildBat, /app-icon\.ico/i);
  assert.match(buildBat, /PyInstaller/i);
  assert.match(buildBat, /--windowed/i);
  assert.match(buildBat, /run dist:win/i);
});


test('one-click build.bat delegates to the complete Windows build', () => {
  assert.match(buildShortcut, /call BUILD_APP\.bat/i);
  assert.match(buildBat, /--windowed/i);
  assert.match(buildBat, /Generating Windows icon/i);
});


test('builder preserves diagnostics instead of disappearing', () => {
  assert.match(buildBat, /build\.log/i);
  assert.match(buildBat, /pause/i);
  assert.match(buildBat, /if defined CI/i);
  assert.match(buildShortcut, /build\.log/i);
  assert.match(buildShortcut, /pause/i);
});
