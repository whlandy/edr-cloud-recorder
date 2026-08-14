import { chromium } from '@playwright/test';
import http from 'node:http';
import { resolveChrome } from '../scripts/chrome-path.mjs';
import { RECORDER } from '../scripts/recorder-inject.mjs';

const HTML = `<!doctype html><meta charset="utf-8"><body>
  <button data-testid="save-btn">保存</button>
  <button id="submit_9" onclick="sendOrder()">提交订单</button>
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

  <!-- 标准开关：有 role 和 aria-checked -->
  <div id="sw1" role="switch" aria-checked="false" tabindex="0">自保护</div>

  <!-- 自研开关：只有 class，没有 aria -->
  <div id="sw2" class="ui-switch off"><span class="knob"></span></div>

  <!-- 整行可点，开关是点击目标的「后代」而不是祖先；状态写在内层 class 上 -->
  <div id="row_sp" class="labelAndItem" style="padding:24px">
    <span>行内自保护</span>
    <div class="eui_toggle"><div class="eui_toggle_container"><i class="eui_toggle_thumb"></i></div></div>
  </div>

  <!-- 慢开关：拨动后 500ms 才更新 class。固定等 60ms 的话检测不到变化，
       会退回盲点击 —— 回放时方向取决于当时的初始状态，而且不报错 -->
  <div id="row_slow" class="labelAndItem" style="padding:24px">
    <span>延迟自保护</span>
    <div class="eui_toggle"><div class="eui_toggle_container"><i class="eui_toggle_thumb"></i></div></div>
  </div>

  <!-- 组件框架批量吐出的同名 testid：不唯一，用它回放必然 strict mode 失败 -->
  <div data-testid="text-comp-span">重复标记甲</div>
  <div data-testid="text-comp-span">重复标记乙</div>

  <!-- 只有 data-cy：Playwright 默认的 testIdAttribute 是 data-testid，认不到 -->
  <button data-cy="cy-only">仅有 data-cy</button>

  <!-- 两个 placeholder 完全相同的输入框：真的那个有 id，另一个是诱饵。
       录制时点哪个都能跑通，回放必然 strict mode 报错。 -->
  <div id="real_box"><input id="real_user" placeholder="账号/手机号"></div>
  <div id="decoy_box"><input placeholder="账号/手机号" autocomplete="on"></div>

  <!-- 触发器显示的值，和下面浮层里的选项文本一模一样 -->
  <div id="trigger">Windows系统</div>
  <div id="pop" role="listbox" style="position:absolute;z-index:999;display:none">
    <div class="opt">Windows系统</div><div class="opt">Linux系统</div>
  </div>

  <script>
    // 请求体里混着三类值：稳定的、字符串型雪花 ID、数字型毫秒时间戳。
    // 后两类每次运行都不同，钉进断言就等于让用例必挂。
    function sendOrder() {
      fetch('/api/ok', { method: 'POST', body: JSON.stringify({
        a: 1, pageSize: 100, id: '39049753287328', endTime: Date.now(),
      }) });
    }
    sw1.addEventListener('click', () => sw1.setAttribute('aria-checked',
      sw1.getAttribute('aria-checked') === 'true' ? 'false' : 'true'));
    sw2.addEventListener('click', () => sw2.classList.toggle('off') || sw2.classList.toggle('on'));
    trigger.addEventListener('click', () => { pop.style.display = 'block'; });
    row_sp.addEventListener('click', () =>
      row_sp.querySelector('.eui_toggle_container').classList.toggle('toggled'));
    row_slow.addEventListener('click', () => setTimeout(() =>
      row_slow.querySelector('.eui_toggle_container').classList.toggle('toggled'), 500));
  </script>
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

// ── 开关与浮层 ──
await page.goto(base);          // 前面测过导航，这里先回首页
await page.waitForTimeout(500);
await page.click('#sw1');                    // aria 开关：false → true
await page.waitForTimeout(300);
await page.click('#trigger');                // 打开浮层
await page.waitForTimeout(300);
await page.locator('#pop .opt', { hasText: 'Windows系统' }).click();   // 与触发器同名
await page.waitForTimeout(400);

// 整行可点的开关：点在行的 padding 上，开关是这一下的后代
await page.click('#row_sp', { position: { x: 5, y: 5 } });
await page.waitForTimeout(400);

// 打字 + 回车：值在 change（失焦/回车之后）才记，按键当场就记，
// 于是录出来会是「先回车后填值」—— 回放时等于在空框上回车
await page.click('input[placeholder="请输入用户名"]');
await page.fill('input[placeholder="请输入用户名"]', 'bob');
await page.press('input[placeholder="请输入用户名"]', 'Enter');
await page.waitForTimeout(300);

// 点在页面空白处：会一路上溯到 html/body，回放时点了等于没点
await page.mouse.click(2, 2);
await page.waitForTimeout(300);

// 同名 testid + 只有 data-cy 的元素
await page.locator('[data-testid=text-comp-span]').first().click();
await page.waitForTimeout(300);
await page.click('[data-cy=cy-only]');
await page.waitForTimeout(300);

// 慢开关：class 要 500ms 后才变
await page.click('#row_slow', { position: { x: 5, y: 5 } });
await page.waitForTimeout(1500);

// 同 placeholder 的两个输入框，填真的那个。
// 必须失焦：fill 的值是在 change 事件里记的，不失焦就不会产生步骤。
await page.fill('#real_user', 'zhangsan');
await page.locator('#real_user').blur();
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
