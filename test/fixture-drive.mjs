import { chromium } from '@playwright/test';
import http from 'node:http';
import { resolveChrome } from '../scripts/chrome-path.mjs';
import { RECORDER } from '../scripts/recorder-inject.mjs';

const HTML = `<!doctype html><meta charset="utf-8"><body>
  <button data-testid="save-btn">保存</button>
  <button id="submit_9" onclick="fetch('/api/ok',{method:'POST',body: JSON.stringify({a:1})})">提交订单</button>
  <button onclick="fetch('/api/bad',{method:'POST',body:'{}'})">触发失败</button>
  <input placeholder="请输入用户名">
  <input type="password" placeholder="密码">
  <label><input type="checkbox" id="agree"> 同意条款</label>
  <table>
    <tr><td>张三</td><td><span class="op">删除</span></td></tr>
    <tr><td>李四</td><td><span class="op">删除</span></td></tr>
    <tr><td>王五</td><td><span class="op">删除</span></td></tr>
  </table>
  <div id="tip_box_10059"><span class="close_x">×</span></div>
  <a id="go" href="/next">立刻跳转</a>
  <iframe src="/login_frame.html" width="300" height="120"></iframe>
</body>`;

const FRAME = `<!doctype html><meta charset="utf-8"><body>
  <input placeholder="iframe内用户名">
  <button>iframe内登录</button></body>`;

const NEXT = `<!doctype html><meta charset="utf-8"><body><h1>第二页</h1>
  <button data-testid="after-nav">跳转后的按钮</button></body>`;

const srv = http.createServer((req, res) => {
  if (req.url === '/') { res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); return res.end(HTML); }
  if (req.url === '/login_frame.html') { res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); return res.end(FRAME); }
  if (req.url === '/next') { res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); return res.end(NEXT); }
  if (req.url === '/api/ok')  { res.writeHead(200, {'Content-Type':'application/json'}); return res.end('{"code":"200"}'); }
  if (req.url === '/api/bad') { res.writeHead(400, {'Content-Type':'application/json'}); return res.end('{"error":"subnetIdList 不能为空"}'); }
  res.writeHead(404); res.end();
});
await new Promise(r => srv.listen(0, r));
const base = `http://127.0.0.1:${srv.address().port}`;

const browser = await chromium.launch({ headless: true, executablePath: resolveChrome() });
const ctx = await browser.newContext();
const steps = [];
const seen = new Set();
const accept = (st) => { if (st?.id && !seen.has(st.id)) { seen.add(st.id); steps.push(st); } };
await ctx.exposeBinding('__recPush', (_s, st) => accept(st));
await ctx.addInitScript(RECORDER);
const page = await ctx.newPage();

const net = [];
page.on('request', r => { if (['xhr','fetch'].includes(r.resourceType())) net.push({t:Date.now(),phase:'req',method:r.method(),url:r.url(),body:r.postData()}); });
page.on('response', async r => {
  if (!['xhr','fetch'].includes(r.request().resourceType())) return;
  const e = {t:Date.now(),phase:'res',method:r.request().method(),url:r.url(),status:r.status()};
  if (r.status()>=400 || r.request().method()!=='GET') e.body = await r.text().catch(()=>null);
  net.push(e);
});

await page.goto(base);
await page.click('[data-testid=save-btn]');
await page.fill('input[placeholder="请输入用户名"]', 'alice');
await page.fill('input[type=password]', 'sup3rs3cret');
await page.check('#agree');
await page.click('#submit_9');
await page.waitForTimeout(300);
await page.click('text=触发失败');
await page.waitForTimeout(400);
await page.locator('tr', {hasText:'李四'}).locator('.op').click();
await page.click('#tip_box_10059 .close_x');
await page.waitForTimeout(300);

// iframe 内的操作：回放时必须 frameLocator 进去，直接 page.getByX() 找不到
await page.frameLocator('iframe').getByPlaceholder('iframe内用户名').fill('frame-user');
await page.waitForTimeout(200);

// 关键场景：点完立刻跳转。步骤若只存在页面内数组里，会随页面卸载一起消失。
await Promise.all([page.waitForURL('**/next'), page.click('#go')]);
await page.click('[data-testid=after-nav]');
await page.waitForTimeout(300);

// ── 断言菜单：右键 → 改 expected → 提交 ──
await page.goto(base);                      // 回首页，元素齐全
await page.waitForTimeout(500);

const menu = () => page.locator('#__rec_assert_menu__');
const sh = (sel) => menu().locator(sel);

// 1) 文本断言，用户把默认值改掉
await page.locator('[data-testid=save-btn]').click({ button: 'right' });
await page.waitForTimeout(300);
await sh('#es').fill('用户确认过的值');
await sh('#ok').click();
await page.waitForTimeout(300);

// 2) 空 expected 必须被挡住，勾了「允许空值」才能提交
await page.locator('[data-testid=save-btn]').click({ button: 'right' });
await page.waitForTimeout(300);
await sh('#es').fill('');
await page.waitForTimeout(200);
const blocked = await sh('#ok').isDisabled();
await sh('#allowEmpty').check();
await page.waitForTimeout(200);
const unblocked = !(await sh('#ok').isDisabled());
await sh('#cancel').click();

// 3) 勾选状态断言：expected 用布尔
await page.locator('#agree').click({ button: 'right' });
await page.waitForTimeout(300);
await sh('#t').selectOption('checked');
await page.waitForTimeout(200);
await sh('#ok').click();
await page.waitForTimeout(300);

// 4) 可见性断言，显式选 false
await page.locator('[data-testid=save-btn]').click({ button: 'right' });
await page.waitForTimeout(300);
await sh('#t').selectOption('visible');
await page.waitForTimeout(200);
await sh('#eb').selectOption('false');
await sh('#ok').click();
await page.waitForTimeout(300);

for (const st of await page.evaluate(() => (window.__rec ? window.__rec.drain() : []))) accept(st);
steps.sort((a,b) => a.t - b.t);
globalThis.__emptyGuard = { blocked, unblocked };
await browser.close(); srv.close();
console.log(JSON.stringify({steps, net, emptyGuard: globalThis.__emptyGuard}, null, 1));
