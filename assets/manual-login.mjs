#!/usr/bin/env node
/**
 * 手动登录并导出登录态
 *
 * 站点在风控触发后会要求滑块验证码，自动登录就走不通了。验证码本来就是用来
 * 拦自动化的，不该去绕；正确做法是让人过一次，然后复用会话 —— 登录态能用一段时间，
 * 期间所有云端操作都不必再登录。
 *
 * 用法：node manual-login.mjs
 *   浏览器窗口打开 → 你手动登录（含验证码）→ 检测到登录成功后自动导出并关闭
 */

import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveChrome } from '../scripts/chrome-path.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.REC_BASE_URL;
if (!BASE) { console.error('请设置 REC_BASE_URL，或改用 config.json'); process.exit(1); }
const ENTRY = BASE + (process.env.REC_ENTRY_PATH ?? '/');
const AUTH = path.join(__dirname, '.auth');
const WAIT_MINUTES = 15;

const browser = await chromium.launch({
  headless: false,
  executablePath: resolveChrome(),
  args: ['--ignore-certificate-errors', '--start-maximized'],
});
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: null });
const page = await ctx.newPage();
page.setDefaultNavigationTimeout(90_000);

console.log(`\n浏览器已打开：${ENTRY}`);
console.log('请在窗口里登录（含滑块验证码）。检测到登录成功后会自动导出并关闭窗口。');
console.log(`最多等待 ${WAIT_MINUTES} 分钟。\n`);

await page.goto(ENTRY, { waitUntil: 'domcontentloaded' }).catch(() => {});

// 判据用应用侧状态而不是 URL —— SSO 回调会多跳几次，参数还不固定。
// .catch 是必须的：轮询期间撞上导航，evaluate 会抛 Execution context was destroyed。
const deadline = Date.now() + WAIT_MINUTES * 60_000;
let ok = false;
while (Date.now() < deadline) {
  ok = await page
    .evaluate(() => sessionStorage.getItem('userInfo') !== null)
    .catch(() => false);
  if (ok) break;
  await page.waitForTimeout(1500);
}

if (!ok) {
  console.error(`\n❌ ${WAIT_MINUTES} 分钟内未检测到登录成功，未导出任何内容。`);
  await browser.close().catch(() => {});
  process.exit(1);
}

// 关掉登录后的一次性弹窗，避免它出现在后续用例里
const dlg = page.getByRole('dialog');
if (await dlg.isVisible().catch(() => false)) {
  await dlg.getByRole('button', { name: /关闭|×|我知道了/ }).first().click().catch(() => {});
}
await page.waitForTimeout(1500);

fs.mkdirSync(AUTH, { recursive: true });
await ctx.storageState({ path: path.join(AUTH, 'state.json') });
fs.writeFileSync(path.join(AUTH, 'session-storage.json'),
  await page.evaluate(() => JSON.stringify(sessionStorage)), 'utf-8');
fs.writeFileSync(path.join(AUTH, 'cookies.json'),
  JSON.stringify(await ctx.cookies(), null, 2), 'utf-8');

// 立刻验证导出的凭据真能调通接口。只看到"登录成功"是不够的 ——
// 否则后面每个用例都会因为同一个原因失败，而失败点散落各处。
// 自检用的只读接口，按站点改；留空则只探首页
const PROBE = process.env.REC_PROBE_PATH ?? '/';
const probe = await page.evaluate(async (u) => {
  const r = await fetch(u);
  return { status: r.status, json: (r.headers.get('content-type') ?? '').includes('json') };
}, PROBE);

console.log(`\n✅ 登录态已导出到 ${AUTH}/`);
console.log(`   连通性自检：HTTP ${probe.status}，返回 JSON = ${probe.json}`);
if (!probe.json) console.log('   ⚠ 返回的不是 JSON，会话可能仍有问题');

await browser.close().catch(() => {});
