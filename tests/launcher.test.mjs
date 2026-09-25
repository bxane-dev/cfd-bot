import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(import.meta.dirname, '..');
const start = fs.readFileSync(path.join(root, 'START.bat'), 'utf8');
const live = fs.readFileSync(path.join(root, 'START_LIVE.bat'), 'utf8');
const requirements = fs.readFileSync(path.join(root, 'requirements.txt'), 'utf8');
const updater = fs.readFileSync(path.join(root, 'UPDATE.bat'), 'utf8');
const gitignore = fs.readFileSync(path.join(root, '.gitignore'), 'utf8');

test('both launchers repair a virtual environment that is missing pip', () => {
  for (const launcher of [start, live]) {
    assert.match(launcher, /-m pip --version/);
    assert.match(launcher, /-m ensurepip --upgrade/);
    assert.match(launcher, /pip repair failed\. Rebuilding the virtual environment/i);
    assert.match(launcher, /-m venv \.venv/);
  }
});

test('demo launcher bootstraps Python and every declared dependency', () => {
  assert.match(start, /winget install -e --id Python\.Python\.3\.12/);
  assert.match(start, /https:\/\/www\.python\.org\/ftp\/python\/3\.12\.10\/python-3\.12\.10-amd64\.exe/);
  assert.match(start, /-m venv \.venv/);
  assert.match(start, /-m pip install -r requirements\.txt/);
  assert.match(start, /import pandas,yaml,dotenv,optuna/);
  assert.match(start, /scripts\\sticky_console\.py --mode demo/);
  for (const packageName of ['pandas', 'numpy', 'pyyaml', 'python-dotenv', 'optuna', 'websocket-client']) {
    assert.match(requirements.toLowerCase(), new RegExp(`^${packageName}`, 'm'));
  }
});

test('live launcher remembers one-time acknowledgement', () => {
  assert.match(live, /if not exist "\.live_acknowledged"/i);
  assert.match(live, /choice \/C YN/i);
  assert.match(live, />"\.live_acknowledged" echo acknowledged/i);
  assert.match(live, /starting without another prompt/i);
  assert.doesNotMatch(live, /Type LIVE and press Enter to continue/);
  assert.match(live, /scripts\\sticky_console\.py --mode live/);
  assert.doesNotMatch(live, /START\.bat/);
});

test('both launchers keep the bxane banner in a sticky terminal region when ANSI is available', () => {
  for (const launcher of [start, live]) {
    assert.match(launcher, /call :sticky_banner/i);
    assert.match(launcher, /\[12;r/);
    assert.match(launcher, /:reset_scroll_region/i);
    assert.match(launcher, /BXANE\.txt/i);
  }
});

test('both launchers show bxane as visible author', () => {
  for (const launcher of [start, live]) {
    assert.match(launcher, /echo\s+\^\|\s+BY bxane\s+\^\|/i);
    assert.match(launcher, /echo\s+============================================================/);
  }
});

test('ZIP downloads bootstrap only from bxane-dev/cfd-bot', () => {
  for (const launcher of [start, live]) {
    assert.match(launcher, /if not exist "\.git"/i);
    assert.match(launcher, /call UPDATE\.bat --bootstrap-only/i);
  }
  assert.match(updater, /git init/i);
  assert.match(updater, /git fetch origin main --depth 1/i);
  assert.match(updater, /bxane-dev\/cfd-bot\.git/i);
  assert.doesNotMatch(updater, /bonexd\/cfd_bot\.git/i);
  assert.doesNotMatch(updater, /bloodvitr\/cfd_bot/i);
  assert.match(updater, /:lock_origin/i);
  assert.match(updater, /if \/I not "!ORIGIN_URL!"=="%REPO_URL%"/i);
  assert.match(updater, /git branch --set-upstream-to=origin\/main main/i);
});

test('updater preserves local work before resetting to GitHub main', () => {
  assert.match(updater, /git stash push -u/i);
  assert.match(updater, /git branch "!BACKUP_BRANCH!" HEAD/i);
  assert.match(updater, /git reset --hard origin\/main/i);
  assert.match(gitignore, /^\.env$/m);
});


test('launchers use the persistent console renderer so BXANE cannot scroll away', () => {
  const sticky = fs.readFileSync(path.join(root, 'scripts', 'sticky_console.py'), 'utf8');
  for (const launcher of [start, live]) {
    assert.match(launcher, /sticky_console\.py/);
    assert.match(launcher, /call :reset_scroll_region/i);
  }
  assert.match(sticky, /stdout=subprocess\.PIPE/);
  assert.match(sticky, /\x1b\[H\x1b\[2J/);
  assert.match(sticky, /Child output is piped/);
  assert.match(sticky, /BANNER_PATH/);
});


test('launchers always restore the canonical BXANE ASCII banner and contain no BONE branding', () => {
  for (const launcher of [start, live]) {
    assert.match(launcher, /call :ensure_bxane_banner/i);
    assert.match(launcher, /BXANE_B64=/i);
    assert.doesNotMatch(launcher, /@?BONE/i);
  }
  const banner = fs.readFileSync(path.join(root, 'BXANE.txt'), 'utf8');
  assert.match(banner, /_______/);
  assert.match(banner, /bxane/i);
  assert.doesNotMatch(banner, /@?BONE/i);
});


test('launchers persist failure diagnostics with exact local path and commit context', () => {
  assert.match(start, /logs\\START_DEMO\.log/i);
  assert.match(live, /logs\\START_LIVE\.log/i);
  for (const launcher of [start, live]) {
    assert.match(launcher, /Launcher:\s*%~f0/i);
    assert.match(launcher, /Working directory:\s*%CD%/i);
    assert.match(launcher, /git rev-parse --short HEAD/i);
    assert.match(launcher, /FAILURE:\s*%FAIL_REASON%/i);
    assert.match(launcher, /Last 30 log lines/i);
    assert.match(launcher, /CFD_START_LOG/i);
  }
});

test('updater writes a persistent diagnostic log', () => {
  assert.match(updater, /logs\\UPDATE\.log/i);
  assert.match(updater, /git remote -v/i);
  assert.match(updater, /git status --short --branch/i);
  assert.match(updater, /git branch -vv/i);
  assert.match(updater, /Update log:/i);
});
