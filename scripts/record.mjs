#!/usr/bin/env node
/**
 * web-record —— 把网页操作录成 Playwright 脚本，并关联触发的接口
 *
 * 启动一个 Playwright 控制的浏览器，你在里面正常操作，脚本负责：
 *   - 记录每一次点击 / 输入 / 勾选 / 回车，为元素算出最稳的选择器
 *   - 从 Node 侧抓所有 XHR/fetch（含请求体、状态码、失败响应体）
 *   - 按时间把接口调用挂到触发它的那一步下面
 *   - 结束时输出原始 JSON + 一份可直接跑的 .spec.ts 草稿
 *
 * 与 `npx playwright codegen` 的区别：codegen 只产选择器，不记录接口。
 * 当你的目标是「搞清楚这个操作到底打了哪些接口、请求体长什么样」时，
 * codegen 给不了答案。
 *
 * 用法：
 *   node record.mjs --url https://app.example.com
 *   node record.mjs --url ... --name login-flow
 *   node record.mjs --url ... --api '/api/'        # 只记录路径含 /api/ 的请求
 *   node record.mjs --url ... --out ./recordings
 *
 * 环境变量：
 *   REC_CHROME_BIN   指定浏览器可执行文件（默认自动探测）
 *   REC_STATE_DIR    登录态目录（默认 ./.auth）
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveChrome } from './chrome-path.mjs';
import { RECORDER } from './recorder-inject.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const args = process.argv.slice(2);
const argOf = (k, d) => {
  const i = args.indexOf(k);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};
const hasFlag = (k) => args.includes(k);

const START_URL = argOf('--url', process.env.REC_URL);
if (!START_URL || hasFlag('--help') || hasFlag('-h')) {
  console.error(`用法: node record.mjs --url <起始页> [选项]

选项:
  --url <URL>        起始页地址（必填，也可用 REC_URL 环境变量）
  --name <名字>       输出文件名，默认按时间戳生成
  --api <路径片段>     只记录 URL 含该片段的请求，默认记录全部 XHR/fetch
  --out <目录>        输出目录，默认 ./recordings

环境变量:
  REC_CHROME_BIN     指定浏览器可执行文件（默认自动探测）
  REC_STATE_DIR      登录态目录，默认 ./.auth`);
  process.exit(1);
}

/**
 * 加载 Playwright
 *
 * 注意解析位置：录制器通常放在 skill 目录里，而依赖装在**用户的项目**里。
 * 直接 `import('@playwright/test')` 是相对本文件解析的，会在 skill 目录下找
 * node_modules —— 那里没有，于是明明用户项目装好了也报找不到。
 *
 * 所以先按 cwd 解析（覆盖绝大多数情况），失败再退回相对本文件解析
 * （覆盖 skill 就装在项目里的情况）。
 */
async function loadPlaywright() {
  const { createRequire } = await import('node:module');
  const { pathToFileURL } = await import('node:url');

  for (const base of [path.join(process.cwd(), 'noop.js'), fileURLToPath(import.meta.url)]) {
    try {
      const req = createRequire(base);
      const mod = await import(pathToFileURL(req.resolve('@playwright/test')).href);
      // @playwright/test 是 CJS：按文件路径 import 时导出会挂在 .default 上，
      // 而按包名 import 时能拿到具名导出。两种形态都兜住。
      const resolved = mod?.chromium ? mod : (mod?.default ?? mod);
      if (resolved?.chromium) return resolved;
    } catch { /* 换下一个基准位置 */ }
  }
  return null;
}

// 依赖检查放在参数校验之后：先让用户看到用法，再谈缺什么。
const pw = await loadPlaywright();
if (!pw) {
  console.error(`在 ${process.cwd()} 找不到 @playwright/test。

录制器需要在装了 Playwright 的项目里运行。在目标项目下执行：
  npm i -D @playwright/test

或者从 skill 的 assets/ 复制一份现成的工程配置：
  cp <skill>/assets/{package.json,playwright.config.ts,tsconfig.json} .
  npm install`);
  process.exit(1);
}
const { chromium } = pw;

const OUT_DIR = path.resolve(argOf('--out', 'recordings'));
const STATE_DIR = path.resolve(process.env.REC_STATE_DIR ?? '.auth');
const NAME = argOf('--name', `session-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`);
const API_FILTER = argOf('--api', null);
const ORIGIN = new URL(START_URL).origin;

/* ---------- 启动 ---------- */
const net = [];
const allSteps = [];

const chromeBin = resolveChrome();
if (chromeBin) console.log(`浏览器: ${chromeBin.replace(process.env.HOME ?? '', '~')}`);

const browser = await chromium.launch({
  headless: false,
  executablePath: chromeBin,
  args: ['--ignore-certificate-errors'],
});

const ctxOpts = { ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } };
const stateFile = path.join(STATE_DIR, 'state.json');
if (fs.existsSync(stateFile)) {
  ctxOpts.storageState = stateFile;
  console.log('已载入 cookies / localStorage');
}

const context = await browser.newContext(ctxOpts);
await context.addInitScript(RECORDER);

// storageState 不含 sessionStorage。有些站点把登录态放在 sessionStorage 里，
// 那就必须在页面脚本执行**之前**注回去，否则 SPA 启动时读不到会立刻跳登录页。
const ssFile = path.join(STATE_DIR, 'session-storage.json');
if (fs.existsSync(ssFile)) {
  const raw = fs.readFileSync(ssFile, 'utf-8');
  await context.addInitScript((data) => {
    try { for (const [k, v] of Object.entries(JSON.parse(data))) sessionStorage.setItem(k, v); } catch { /* */ }
  }, raw);
  console.log('已载入 sessionStorage');
}

const page = await context.newPage();

const wanted = (req) => {
  const rt = req.resourceType();
  if (rt !== 'xhr' && rt !== 'fetch') return false;
  return API_FILTER ? req.url().includes(API_FILTER) : true;
};

page.on('request', (r) => {
  if (!wanted(r)) return;
  net.push({ t: Date.now(), phase: 'req', method: r.method(), url: r.url(), body: r.postData() ?? null });
});
page.on('response', async (r) => {
  if (!wanted(r.request())) return;
  const e = { t: Date.now(), phase: 'res', method: r.request().method(), url: r.url(), status: r.status() };
  // 失败响应和写操作的响应体一定要留 —— 排查 4xx/5xx 时这是唯一有用的信息。
  // 成功的 GET 响应体可能有几十上百 KB，全存没有价值。
  if (r.status() >= 400 || r.request().method() !== 'GET') {
    e.body = await r.text().then((t) => t.slice(0, 2000)).catch(() => null);
  }
  net.push(e);
});

// 页面跳转会清掉页面内的 steps，所以定时搬运到 Node 侧
const pump = setInterval(async () => {
  try {
    const s = await page.evaluate(() => (window.__rec ? window.__rec.drain() : []));
    if (s?.length) {
      allSteps.push(...s);
      for (const x of s) {
        const val = x.secret ? ' = <密码，未记录>' : x.value !== undefined ? ` = ${JSON.stringify(x.value)}` : '';
        console.log(`  [录制] ${x.type.padEnd(6)} ${x.sel}${val}`);
      }
    }
  } catch { /* 导航中，下次再取 */ }
}, 800);

console.log(`\n打开 ${START_URL}`);
console.log('需要登录就在这个窗口里登录（密码不会被记录）。');
console.log('操作完成后直接关闭浏览器窗口，脚本自动生成。\n');

await page.goto(START_URL, { waitUntil: 'domcontentloaded' }).catch((e) => {
  console.error(`打开失败: ${e.message.split('\n')[0]}`);
});
await page.waitForEvent('close', { timeout: 0 }).catch(() => {});
clearInterval(pump);

try {
  const s = await page.evaluate(() => (window.__rec ? window.__rec.drain() : []));
  if (s?.length) allSteps.push(...s);
} catch { /* 页面已关 */ }

// 顺手存下登录态，下次录制免登录
try {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  await context.storageState({ path: stateFile });
  const ss = await context.pages()[0]?.evaluate(() => JSON.stringify(sessionStorage)).catch(() => null);
  if (ss && ss !== '{}') fs.writeFileSync(ssFile, ss, 'utf-8');
} catch { /* 页面已关就取不到了，不影响主流程 */ }

await browser.close().catch(() => {});

/* ---------- 输出 ---------- */
fs.mkdirSync(OUT_DIR, { recursive: true });
const rawFile = path.join(OUT_DIR, `${NAME}.json`);
fs.writeFileSync(rawFile, JSON.stringify({ startUrl: START_URL, recordedAt: new Date().toISOString(), steps: allSteps, net }, null, 1), 'utf-8');

const strip = (u) => u.replace(ORIGIN, '');
const between = (a, b) => net.filter((n) => n.t >= a && n.t < b && n.phase === 'res');

const lines = [
  `import { test, expect } from '@playwright/test';`,
  ``,
  `// 由 web-record 生成：${NAME}`,
  `// ⚠ 这是草稿，不是成品。至少要做三件事：`,
  `//   1. 收紧标了 AMBIGUOUS 的选择器`,
  `//   2. 把接口注释换成真正的断言（expect(resp.status()).toBe(200) 之类）`,
  `//   3. 删掉与意图无关的误操作步骤`,
  ``,
  `test(${JSON.stringify(NAME)}, async ({ page }) => {`,
  `  await page.goto(${JSON.stringify(strip(START_URL) || '/')});`,
];

allSteps.forEach((s, i) => {
  const hi = allSteps[i + 1]?.t ?? Infinity;
  const calls = between(s.t, hi);
  const warn = s.ambiguous ? `   // ⚠ AMBIGUOUS: ${s.matches} 个元素匹配，回放时可能点错` : '';

  // CSS 兜底选择器基本都是关闭弹窗/提示这类「有就点、没有就跳过」的动作。
  // 生成成必经步骤会让脚本在弹窗不出现时直接失败。
  if (s.kind === 'css' && s.type === 'click') {
    lines.push(`  // ⚠ CSS 兜底（元素没有 role/label/稳定文本），建议改用语义定位`);
    lines.push(`  {`);
    lines.push(`    const el = page.${s.sel};`);
    lines.push(`    if (await el.isVisible().catch(() => false)) await el.click();`);
    lines.push(`  }`);
  } else if (s.type === 'click') {
    lines.push(`  await page.${s.sel}.click();${warn}`);
  } else if (s.type === 'fill' && s.secret) {
    lines.push(`  await page.${s.sel}.fill(process.env.REC_PASSWORD ?? '');${warn}`);
  } else if (s.type === 'fill') {
    lines.push(`  await page.${s.sel}.fill(${JSON.stringify(s.value ?? '')});${warn}`);
  } else if (s.type === 'check') {
    lines.push(`  await page.${s.sel}.check();${warn}`);
  } else if (s.type === 'uncheck') {
    lines.push(`  await page.${s.sel}.uncheck();${warn}`);
  } else if (s.type === 'press') {
    lines.push(`  await page.${s.sel}.press('Enter');${warn}`);
  }

  for (const c of calls) {
    lines.push(`  //   ↳ ${c.method} ${strip(c.url)} -> ${c.status}`);
    if (c.status >= 400 && c.body) {
      lines.push(`  //     ⚠ 失败响应: ${c.body.slice(0, 200).replace(/\s+/g, ' ')}`);
    }
  }
});

lines.push(`});`, ``);
const specFile = path.join(OUT_DIR, `${NAME}.spec.ts`);
fs.writeFileSync(specFile, lines.join('\n'), 'utf-8');

/* ---------- 小结 ---------- */
const responses = net.filter((n) => n.phase === 'res');
const writes = net.filter((n) => n.phase === 'req' && n.method !== 'GET');
const failed = responses.filter((n) => n.status >= 400);
const amb = allSteps.filter((s) => s.ambiguous);
const css = allSteps.filter((s) => s.kind === 'css');

console.log(`\n录制完成`);
console.log(`  操作 ${allSteps.length} 步 · 接口 ${responses.length} 次 · 写请求 ${writes.length} 次`);
console.log(`  原始记录  ${rawFile}`);
console.log(`  脚本草稿  ${specFile}`);
if (failed.length) {
  console.log(`\n  ⚠ ${failed.length} 个请求失败：`);
  for (const f of failed.slice(0, 5)) console.log(`      ${f.method} ${strip(f.url).split('?')[0]} -> ${f.status}`);
}
if (amb.length) console.log(`  ⚠ ${amb.length} 个选择器有歧义，草稿里已标出`);
if (css.length) console.log(`  ⚠ ${css.length} 个只能用 CSS 兜底，已包成「存在则点」`);
