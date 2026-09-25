'use strict';

const { app, BrowserWindow, Menu, dialog, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');

let windowRef = null;
let engine = null;
let engineLog = null;
let quitting = false;

const PORT = 8484;

function repoRoot() {
  return path.resolve(__dirname, '..');
}

function dataRoot() {
  return app.getPath('userData');
}

function templateRoot() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'templates')
    : repoRoot();
}

function iconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'assets', 'app-icon.ico')
    : path.join(repoRoot(), 'assets', 'app-icon.ico');
}

function ensureUserFiles() {
  const root = dataRoot();
  fs.mkdirSync(root, { recursive: true });

  const copies = [
    ['config.yaml', 'config.yaml'],
    ['.env.example', '.env.example'],
  ];
  for (const pair of copies) {
    const sourceName = pair[0];
    const destName = pair[1];
    const src = path.join(templateRoot(), sourceName);
    const dst = path.join(root, destName);
    if (!fs.existsSync(dst) && fs.existsSync(src)) {
      fs.copyFileSync(src, dst);
    }
  }

  const envPath = path.join(root, '.env');
  const examplePath = path.join(root, '.env.example');
  if (!fs.existsSync(envPath) && fs.existsSync(examplePath)) {
    fs.copyFileSync(examplePath, envPath);
  }

  return {
    root: root,
    envPath: envPath,
    configPath: path.join(root, 'config.yaml'),
  };
}

function envValues(envPath) {
  if (!fs.existsSync(envPath)) return {};
  const out = {};
  const text = fs.readFileSync(envPath, 'utf8');
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx < 1) continue;
    const key = line.slice(0, idx).trim();
    let value = line.slice(idx + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

function credentialsReady(envPath) {
  const env = envValues(envPath);
  return Boolean(
    (env.CAPITAL_EMAIL || env.CAPITAL_IDENTIFIER) &&
    env.CAPITAL_API_KEY &&
    env.CAPITAL_API_PASSWORD
  );
}

function configuredMode(configPath) {
  try {
    const text = fs.readFileSync(configPath, 'utf8');
    const match = text.match(/^mode:\s*(demo|live)\s*(?:#.*)?$/im);
    return match ? match[1].toLowerCase() : 'demo';
  } catch (_) {
    return 'demo';
  }
}

async function requireCredentials(envPath) {
  while (!credentialsReady(envPath)) {
    const result = await dialog.showMessageBox({
      type: 'warning',
      title: 'CFD Desk setup',
      message: 'Capital.com credentials are required before CFD Desk can start.',
      detail:
        'Add CAPITAL_EMAIL, CAPITAL_API_KEY and CAPITAL_API_PASSWORD to the app .env file. ' +
        'Twitch and Kick credentials remain optional.',
      buttons: ['Open .env', 'Retry', 'Quit'],
      defaultId: 0,
      cancelId: 2,
      noLink: true,
    });
    if (result.response === 2) return false;
    if (result.response === 0) {
      await shell.openPath(envPath);
    }
  }
  return true;
}

async function confirmLive(mode) {
  if (mode !== 'live') return true;
  const result = await dialog.showMessageBox({
    type: 'warning',
    title: 'CFD Desk · LIVE',
    message: 'Start CFD Desk with the Capital.com LIVE environment?',
    detail:
      'LIVE mode can submit real-money CFD orders after the existing CFD Desk confirmations and risk gates.',
    buttons: ['Start LIVE', 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
  });
  return result.response === 0;
}

function backendCommand(mode, token) {
  const args = ['--mode', mode, '--skip-tune', '--desktop'];
  const env = Object.assign({}, process.env, {
    CFD_DESKTOP: '1',
    CFD_DATA_DIR: dataRoot(),
    CFD_WEB_HOST: '127.0.0.1',
    CFD_WEB_TOKEN: token,
    PYTHONUNBUFFERED: '1',
  });

  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, 'engine', 'cfd-engine.exe'),
      args: args,
      cwd: dataRoot(),
      env: env,
    };
  }

  const python = process.env.CFD_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  return {
    command: python,
    args: ['-u', '-m', 'app.auto'].concat(args),
    cwd: repoRoot(),
    env: env,
  };
}

function startEngine(mode, token) {
  const spec = backendCommand(mode, token);
  const logPath = path.join(dataRoot(), 'backend.log');
  engineLog = fs.createWriteStream(logPath, { flags: 'a' });
  engineLog.write('\n--- CFD Desk engine start ' + new Date().toISOString() + ' mode=' + mode + ' ---\n');

  engine = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: spec.env,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  engine.stdout.pipe(engineLog, { end: false });
  engine.stderr.pipe(engineLog, { end: false });

  engine.on('error', function (error) {
    if (engineLog) engineLog.write('engine spawn error: ' + (error.stack || error) + '\n');
  });

  engine.on('exit', function (code, signal) {
    if (engineLog) {
      engineLog.write('engine exit code=' + code + ' signal=' + (signal || '') + '\n');
    }
    engine = null;
    if (!quitting && windowRef) {
      dialog.showMessageBox(windowRef, {
        type: 'error',
        title: 'CFD engine stopped',
        message: 'The background CFD engine stopped.',
        detail: 'Exit code: ' + (code == null ? 'unknown' : code) + '\nLog: ' + logPath,
        buttons: ['OK'],
      });
    }
  });
}

function stopEngine() {
  if (!engine || engine.killed) return;
  const pid = engine.pid;
  try {
    if (process.platform === 'win32' && pid) {
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
    } else {
      engine.kill('SIGTERM');
    }
  } catch (_) {}
  engine = null;
  if (engineLog) {
    try { engineLog.end(); } catch (_) {}
    engineLog = null;
  }
}

function waitForDesk(engineProcess, timeoutMs) {
  const timeout = timeoutMs || 60000;
  const started = Date.now();
  return new Promise(function (resolve, reject) {
    let done = false;

    function finish(error) {
      if (done) return;
      done = true;
      if (error) reject(error);
      else resolve();
    }

    function probe() {
      if (done) return;
      if (!engineProcess || engineProcess.exitCode !== null) {
        finish(new Error('CFD engine exited before the desk became ready.'));
        return;
      }

      const req = http.get(
        {
          host: '127.0.0.1',
          port: PORT,
          path: '/',
          timeout: 700,
        },
        function (res) {
          res.resume();
          if (res.statusCode === 200) {
            finish();
          } else if (Date.now() - started >= timeout) {
            finish(new Error('CFD Desk startup timed out with HTTP ' + res.statusCode + '.'));
          } else {
            setTimeout(probe, 250);
          }
        }
      );

      req.on('timeout', function () { req.destroy(); });
      req.on('error', function () {
        if (Date.now() - started >= timeout) {
          finish(new Error('CFD Desk startup timed out.'));
        } else {
          setTimeout(probe, 250);
        }
      });
    }

    probe();
  });
}

function createWindow(token, mode) {
  Menu.setApplicationMenu(null);
  windowRef = new BrowserWindow({
    title: mode === 'live' ? 'CFD Desk · LIVE' : 'CFD Desk · DEMO',
    width: 1500,
    height: 960,
    minWidth: 1050,
    minHeight: 700,
    backgroundColor: '#070a0f',
    show: false,
    autoHideMenuBar: true,
    icon: iconPath(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  windowRef.webContents.setWindowOpenHandler(function (details) {
    if (/^https?:\/\//i.test(details.url)) shell.openExternal(details.url);
    return { action: 'deny' };
  });

  windowRef.once('ready-to-show', function () { windowRef.show(); });
  windowRef.on('closed', function () {
    windowRef = null;
    if (!quitting) app.quit();
  });

  windowRef.loadURL(
    'http://127.0.0.1:' + PORT + '/?token=' + encodeURIComponent(token)
  );
}

async function boot() {
  const files = ensureUserFiles();
  if (!(await requireCredentials(files.envPath))) {
    app.quit();
    return;
  }

  const mode = configuredMode(files.configPath);
  if (!(await confirmLive(mode))) {
    app.quit();
    return;
  }

  const token = crypto.randomBytes(32).toString('base64url');
  startEngine(mode, token);

  try {
    await waitForDesk(engine, 60000);
  } catch (error) {
    const result = await dialog.showMessageBox({
      type: 'error',
      title: 'CFD Desk could not start',
      message: error.message,
      detail:
        'The engine runs hidden in the background. Check ' +
        path.join(files.root, 'backend.log') +
        ' for details.',
      buttons: ['Open .env', 'Open log folder', 'Quit'],
      defaultId: 2,
      cancelId: 2,
      noLink: true,
    });
    if (result.response === 0) await shell.openPath(files.envPath);
    if (result.response === 1) await shell.openPath(files.root);
    app.quit();
    return;
  }

  createWindow(token, mode);
}

app.whenReady().then(boot);

app.on('before-quit', function () {
  quitting = true;
  stopEngine();
});

app.on('window-all-closed', function () {
  app.quit();
});
