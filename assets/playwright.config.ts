import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * 复用本机已有的 Chromium 构建
 *
 * Playwright 每个版本只认自己那一版 browser build，升级后会要求重下约 170MB。
 * 内网/弱网环境里这一步经常卡死。缓存里通常已有可用的构建，直接指过去即可。
 * 逻辑与 scripts/chrome-path.mjs 一致（配置是 TS、录制器是 ESM，各写一份更省事）。
 */
function resolveChrome(): string | undefined {
  if (process.env.REC_CHROME_BIN) return process.env.REC_CHROME_BIN;
  const cache =
    process.env.PLAYWRIGHT_BROWSERS_PATH ??
    (process.platform === 'darwin'
      ? path.join(os.homedir(), 'Library/Caches/ms-playwright')
      : process.platform === 'win32'
        ? path.join(process.env.LOCALAPPDATA ?? '', 'ms-playwright')
        : path.join(os.homedir(), '.cache/ms-playwright'));
  if (!fs.existsSync(cache)) return undefined;

  const rels = process.platform === 'darwin'
    ? ['chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
       'chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
       'chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium']
    : process.platform === 'win32'
      ? ['chrome-win/chrome.exe', 'chrome-win64/chrome.exe']
      : ['chrome-linux/chrome', 'chrome-linux64/chrome'];

  const builds = fs.readdirSync(cache)
    .filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1]));
  for (const b of builds) for (const r of rels) {
    const p = path.join(cache, b, r);
    if (fs.existsSync(p)) return p;
  }
  return undefined;
}

export default defineConfig({
  // 登录用例的失败现场含明文密码，跑完统一清除，见该文件注释
  globalTeardown: './tests/scrub-auth-artifacts.ts',

  // 默认只收 tests/ 里整理过的用例。录制草稿要跑得显式打开：
  //   REC_DRAFTS=1 npx playwright test recordings/xxx.spec.ts
  //
  // 草稿不进默认收集，是因为它按定义就是半成品：可能引用还没写的 helper
  // （一个文件解析失败会让整次收集归零），更要紧的是里面常有未经审阅的真实
  // 写操作和明文密码登录 —— 不该被一句 `npx playwright test` 顺带跑掉。
  // 但也不能让它压根跑不了，那样录完第一件想做的事就卡住了。
  testDir: '.',
  testMatch: process.env.REC_DRAFTS
    ? ['tests/**/*.spec.ts', 'recordings/**/*.spec.ts']
    : ['tests/**/*.spec.ts'],
  outputDir: './test-results',
  timeout: 120_000,
  expect: { timeout: 15_000 },

  // 服务端在短时间内收到大量请求时可能返回 5xx，重试即恢复。
  // 不重试会产生大量假失败，掩盖真问题。
  retries: process.env.CI ? 2 : 1,

  // 界面操作往往共享全局状态，并行会互相污染。
  // 确认用例之间互不影响后再调大。
  workers: 1,

  reporter: [['list'], ['html', { outputFolder: './playwright-report', open: 'never' }]],

  use: {
    baseURL: process.env.REC_BASE_URL,
    // 自签证书 / IP 直连 / 内网域名都需要这个
    ignoreHTTPSErrors: true,
    actionTimeout: 20_000,
    navigationTimeout: 60_000,
    // 失败时保留现场，比读日志快得多
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    launchOptions: {
      executablePath: resolveChrome(),
      args: ['--ignore-certificate-errors'],
    },
  },

  projects: [
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
      // 登录用例绝不留现场。trace / 录像 / 失败截图会把输入框里的明文密码
      // 原样存到磁盘上 —— 这是凭据泄露最容易被忽视的一条路径。
      // 其他用例照常保留现场，它们不接触凭据。
      use: { trace: 'off', video: 'off', screenshot: 'off' },
    },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
      dependencies: ['setup'],
    },
  ],
});
