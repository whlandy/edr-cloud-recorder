import { test as setup, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// package.json 里 type=module，Playwright 按 ESM 处理 TS，__dirname 不存在
const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * 登录一次，把登录态导出给后续所有用例复用。
 *
 * 关键点：Playwright 的 storageState **只保存 cookies + localStorage**，
 * 不保存 sessionStorage。如果目标站点把 token 放在 sessionStorage 里
 * （表现为：新标签页打开需要重新登录），标准的「登录一次复用 storageState」
 * 方案会失败 —— 应用启动时读不到 token，立刻跳登录页。
 *
 * 所以这里额外导出 sessionStorage，由 fixtures.ts 用 addInitScript 注回去。
 *
 * ── 使用前需要改的地方 ──────────────────────────────
 *   1. LOGIN_URL / 登录成功的判据
 *   2. 用户名、密码输入框的定位方式
 *   3. 一次性弹窗的关闭逻辑（如果有）
 */

const AUTH_DIR = path.join(__dirname, '..', '.auth');
const STORAGE_STATE = path.join(AUTH_DIR, 'state.json');
const SESSION_STORAGE = path.join(AUTH_DIR, 'session-storage.json');
const COOKIES_EXPORT = path.join(AUTH_DIR, 'cookies.json');

// 改成应用的入口地址
const ENTRY_URL = process.env.REC_ENTRY_PATH ?? '/';

setup('登录并导出登录态', async ({ page, context }) => {
  const user = process.env.REC_USER;
  const pass = process.env.REC_PASSWORD;

  if (!user || !pass) {
    throw new Error(
      '缺少凭据。请先设置环境变量：\n' +
        '  export REC_USER=...\n' +
        '  export REC_PASSWORD=...\n' +
        '凭据只从环境变量读取，不写进代码库。',
    );
  }

  await page.goto(ENTRY_URL);

  // 未登录时通常会被重定向到登录页。用 URL 特征判断比固定等待可靠。
  await page.waitForURL(/login/i, { timeout: 60_000 }).catch(() => {
    // 已经是登录态就不会跳转，继续往下走
  });

  // Cookie 同意横幅之类的东西会挡住输入框
  const consent = page.getByRole('button', { name: /接受|同意|关闭|Accept/i }).first();
  if (await consent.isVisible().catch(() => false)) await consent.click().catch(() => {});

  // ── 改这里：换成实际的输入框定位 ──
  //
  // 用稳定的 id 或 label，**不要**用 .or() 组合多种定位方式。
  // 踩过的坑：某些登录页的密码框没有 placeholder（可访问名来自旁边的图标或标签），
  // getByPlaceholder(/密码/).or('#password') 会解析到用户名框，
  // 结果用户名和密码被填进同一个输入框 —— 而失败现场里那串明文就是泄露源。
  const userBox = page.locator('#username');
  const passBox = page.locator('#password');

  await userBox.waitFor({ state: 'visible', timeout: 30_000 });
  await passBox.waitFor({ state: 'visible', timeout: 30_000 });

  await userBox.fill(user);
  await passBox.fill(pass);
  // 填完校验一次：填错框是静默失败，只有断言能把它变成显式失败
  await expect(userBox, '用户名框内容异常，疑似把密码也填了进去').toHaveValue(user);

  await page.getByRole('button', { name: /登录|登入|Sign in/i }).click();

  // ── 改这里：换成可靠的登录成功判据 ──
  // 不要只判断 URL —— SSO 回调可能多跳几次，参数也不固定。
  // 判断应用侧的状态更准确。
  await expect
    .poll(
      () => page.evaluate(() => sessionStorage.length > 0 || localStorage.getItem('token') !== null),
      { timeout: 60_000, message: '登录后仍未检测到登录态，登录可能失败' },
    )
    .toBe(true);

  // 登录后的一次性弹窗（首次引导、公告、协议）会挡住后续操作，在这里统一关掉，
  // 别留给每个用例各自处理。
  const dlg = page.getByRole('dialog');
  if (await dlg.isVisible().catch(() => false)) {
    await dlg.getByRole('button', { name: /关闭|我知道了|确定|×/ }).first().click().catch(() => {});
  }

  fs.mkdirSync(AUTH_DIR, { recursive: true });

  // 1) cookies + localStorage
  await context.storageState({ path: STORAGE_STATE });

  // 2) sessionStorage —— Playwright 不管这块，必须自己存
  fs.writeFileSync(SESSION_STORAGE, await page.evaluate(() => JSON.stringify(sessionStorage)), 'utf-8');

  // 3) 纯 cookie 导出，方便 curl / Python 等复用
  fs.writeFileSync(COOKIES_EXPORT, JSON.stringify(await context.cookies(), null, 2), 'utf-8');

  console.log(`登录态已导出到 ${AUTH_DIR}/（记得加进 .gitignore）`);
});
