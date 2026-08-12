/**
 * 解析本机可用的 Chromium 可执行文件（跨平台）
 *
 * 为什么需要这个：Playwright 每个版本只认自己那一版的 browser build。
 * 本机缓存里往往已有别的版本的完整构建，直接 executablePath 指过去即可，
 * 不必为了版本号再下 170MB —— 内网/弱网环境尤其有用。
 *
 * 查找顺序：
 *   1. REC_CHROME_BIN 环境变量（显式指定，最高优先）
 *   2. PLAYWRIGHT_BROWSERS_PATH 或各平台默认的 ms-playwright 缓存，取版本号最高的构建
 *   3. 系统安装的 Chrome / Chromium
 *   4. 都没有则返回 undefined，交给 Playwright 用它自己那一版
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function cacheDirs() {
  if (process.env.PLAYWRIGHT_BROWSERS_PATH) return [process.env.PLAYWRIGHT_BROWSERS_PATH];
  const home = os.homedir();
  switch (process.platform) {
    case 'darwin':
      return [path.join(home, 'Library/Caches/ms-playwright')];
    case 'win32':
      return [path.join(process.env.LOCALAPPDATA ?? path.join(home, 'AppData/Local'), 'ms-playwright')];
    default: // linux
      return [path.join(home, '.cache/ms-playwright')];
  }
}

// 各平台在 chromium-<rev>/ 下的相对路径（Chrome for Testing 与旧 Chromium 命名都覆盖）
const REL_PATHS = {
  darwin: [
    'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
    'chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
    'chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium',
    'chrome-mac/Chromium.app/Contents/MacOS/Chromium',
  ],
  linux: [
    'chrome-linux/chrome',
    'chrome-linux64/chrome',
    'chrome-linux/headless_shell',
  ],
  win32: [
    'chrome-win/chrome.exe',
    'chrome-win64/chrome.exe',
  ],
};

const SYSTEM_CHROME = {
  darwin: [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ],
  linux: [
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
  ],
  win32: [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  ],
};

export function resolveChrome({ quiet = true } = {}) {
  const override = process.env.REC_CHROME_BIN;
  if (override) {
    if (!fs.existsSync(override)) throw new Error(`REC_CHROME_BIN 指向的文件不存在：${override}`);
    return override;
  }

  const plat = process.platform === 'darwin' || process.platform === 'win32' ? process.platform : 'linux';
  const rels = REL_PATHS[plat];

  for (const cache of cacheDirs()) {
    if (!fs.existsSync(cache)) continue;
    const builds = fs
      .readdirSync(cache)
      .filter((d) => /^chromium(_headless_shell)?-\d+$/.test(d))
      .sort((a, b) => Number(b.split('-').pop()) - Number(a.split('-').pop()));
    for (const b of builds) {
      for (const rel of rels) {
        const p = path.join(cache, b, rel);
        if (fs.existsSync(p)) return p;
      }
    }
  }

  for (const p of SYSTEM_CHROME[plat]) if (fs.existsSync(p)) return p;

  if (!quiet) console.warn('未找到可用的 Chromium，将交给 Playwright 自行处理（可能提示需要 install）');
  return undefined;
}

// 直接执行时打印结果，方便别的 agent 排查环境
if (import.meta.url === `file://${process.argv[1]}`) {
  const p = resolveChrome({ quiet: false });
  console.log(p ?? '(未找到)');
}
