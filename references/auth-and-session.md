# 登录态：为什么脚本第二次跑就跳登录页

自动化脚本最常见的失败不是选择器错，是登录态没带对。这份文档按「症状 → 原因 → 解法」
组织，遇到哪个看哪个。

## 症状一：录制时好好的，回放直接跳登录页

### 先判断登录态存在哪

在浏览器控制台跑：

```js
JSON.stringify({
  cookie: document.cookie.split(';').map(s => s.trim().split('=')[0]),
  local: Object.keys(localStorage),
  session: Object.keys(sessionStorage),
})
```

然后对照：

| 登录态位置 | Playwright `storageState` 是否覆盖 |
|---|---|
| Cookie | ✅ 覆盖 |
| localStorage | ✅ 覆盖 |
| **sessionStorage** | ❌ **不覆盖** |

`storageState` 只存 cookies + localStorage。**这是最容易踩的坑**：登录流程写得完全正确，
`state.json` 也生成了，但应用启动时读的是 `sessionStorage.token`，读不到就跳登录页。

### 解法：用 addInitScript 在页面脚本执行前注回去

时机是关键。`addInitScript` 在页面任何脚本运行**之前**执行，所以 SPA 启动时能读到。
换成 `page.evaluate()` 就晚了 —— 那时应用已经判定未登录并开始跳转。

导出（登录之后）：

```ts
const session = await page.evaluate(() => JSON.stringify(sessionStorage));
fs.writeFileSync('.auth/session-storage.json', session);
```

注入（每个测试之前）：

```ts
await context.addInitScript((data: string) => {
  for (const [k, v] of Object.entries(JSON.parse(data))) {
    try { sessionStorage.setItem(k, v); } catch { /* 只读键或超配额 */ }
  }
}, fs.readFileSync('.auth/session-storage.json', 'utf-8'));
```

`assets/auth.setup.ts` 和 `assets/fixtures.ts` 已经实现了这套，直接复制即可。

## 症状二：同一个浏览器，这个标签页是登录的，新标签页却要重新登录

说明登录态是**按标签页隔离**的 —— 几乎可以确定在 sessionStorage 里（cookie 和
localStorage 都是跨标签页共享的）。

这个特性有个隐蔽的后果：**任何「复用用户已打开的浏览器」的方案都会失效**，因为新开的
标签页拿不到旧标签页的 sessionStorage。只能在脚本控制的那个上下文里完成登录，或者按
上面的办法注入。

## 症状三：走 SSO，登录要跳好几次

典型流程是 `应用 → SSO 登录页 → 回调 → 应用`。写 setup 时注意两点：

**不要用固定 URL 判断登录成功。** 回调可能带各种参数，也可能多跳一次。用应用侧的
状态判断更可靠：

```ts
await expect.poll(
  () => page.evaluate(() => sessionStorage.getItem('userInfo') !== null),
  { timeout: 60_000, message: '登录后 userInfo 一直为空' },
).toBe(true);
```

**登录后常有一次性弹窗**（首次引导、公告、隐私协议、验证码提示）。它们会挡住后续操作，
在 setup 里就关掉，别留给每个用例各自处理：

```ts
const dlg = page.getByRole('dialog');
if (await dlg.isVisible().catch(() => false)) {
  await dlg.getByRole('button', { name: /关闭|我知道了|确定/ }).first().click().catch(() => {});
}
```

## 症状四：Python / curl 复用浏览器登录态失败

从 Playwright 导出 cookie 给别的语言用是可行的，但有两个常见障碍：

**CSRF token。** 很多框架要求把某个 cookie 的值回填到请求头（`X-CSRF-Token`、
`X-XSRF-TOKEN`、`roarand` 等）。浏览器里是前端代码自动做的，换成 requests 就得手动：

```python
for name in ("XSRF-TOKEN", "csrfToken", "_xsrf"):
    if name in session.cookies:
        session.headers[name] = session.cookies[name]
```

**登录态在 sessionStorage 而不是 cookie。** 这种情况下光有 cookie 不够，服务端认的是
请求头里的 token。先在浏览器 Network 面板看一个已认证的请求带了什么头，照着补。

**不要凭空实现登录接口。** SSO 的登录 POST 往往有加密参数、验证码、时间戳签名。
没抓过就写，写出来的是猜测。可靠做法是让 Playwright 完成登录并导出凭据，其他语言只负责复用。

## 别让失败现场泄露密码

Playwright 默认在失败时保留 trace、录像和截图。登录用例失败时，输入框里的**明文密码**
会原样进到这些文件里 —— 这是凭据泄露最容易被忽视的一条路径，因为它不经过代码、
不经过版本库，只是静静躺在 `test-results/` 里。

给 setup 项目单独关掉：

```ts
{
  name: 'setup',
  testMatch: /auth\.setup\.ts/,
  use: { trace: 'off', video: 'off', screenshot: 'off' },
}
```

其他用例照常保留现场，它们不接触凭据。

**关掉这三项还不够。** Playwright 另外会写一份 `error-context.md`，里面同样是失败时的
页面快照，包含输入框里的明文密码，而且不受 trace/video/screenshot 开关控制。
实测在关掉三项之后，密码依然进了这个文件。

靠人记得清理迟早会漏，用 globalTeardown 做成机制（模板见 `assets/scrub-auth-artifacts.ts`）：

```ts
globalTeardown: './tests/scrub-auth-artifacts.ts',
```

它按目录名匹配 setup 项目的产物并整个删除；其他用例的现场保留，它们不碰凭据。
验证方式是故意让登录失败一次，确认现场被清掉 —— 用不存在的用户名，
别用错密码去撞真账号的锁定策略。

如果已经发生了，`test-results/` 和 `playwright-report/` 整个删掉 —— trace.zip 里
是完整的 DOM 快照，光删截图不够。

## 登录后的状态轮询要容忍导航

SSO 登录后页面会连续跳转。轮询里的 `page.evaluate` 一旦撞上导航就抛
`Execution context was destroyed`，而 `expect.poll` **遇到异常是直接失败、不会重试** ——
于是这一步变成靠运气：躲开导航窗口就过，撞上就挂。

```ts
await expect.poll(
  () => page.evaluate(() => sessionStorage.getItem('userInfo') !== null).catch(() => false),
  { timeout: 90_000, intervals: [500, 1000, 2000] },
).toBe(true);
```

`.catch(() => false)` 把异常当作"还没就绪"，轮询才真正起作用。

## 登录框定位：不要用 .or() 组合

看起来稳妥的写法反而会出事：

```ts
// ❌ 密码框没有 placeholder 时，这会解析到用户名框
const passBox = frame.getByPlaceholder(/密码/).or(frame.locator('#password')).first();
```

结果是用户名和密码被填进**同一个输入框**，登录失败，而失败现场里带着明文密码。

用单一稳定的定位方式，填完再断言一次：

```ts
const userBox = frame.locator('#username');
const passBox = frame.locator('#password');
await userBox.fill(user);
await passBox.fill(pass);
await expect(userBox).toHaveValue(user);   // 填错框是静默失败，断言让它显式
```

## 凭据管理

- 用户名密码只从环境变量读，不写进代码，不落盘
- `.auth/` 加进 `.gitignore` —— 里面是有效会话，等同于密码
- 录制器对 `type=password` 的输入只记录「填了密码」这个动作，值替换成
  `process.env.REC_PASSWORD`。生成的 spec 因此可以安全提交
- 会话会过期。脚本要能识别「跳到登录页」并给出清晰报错，而不是在某个选择器上超时
