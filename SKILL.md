---
name: web-record
description: >
  把网页上的手工操作录制成可重复执行的 Playwright 脚本，同时抓取每一步触发的
  HTTP 接口（含请求体、状态码、失败响应）。

  在以下情况下使用这个 skill —— 即使用户没有说出「录制」或「Playwright」这些词：
  • 用户说「我在网页上做了一系列操作，能不能变成脚本」「帮我把这个流程自动化」
    「录一下我的操作」「生成回归测试」「把这个点击流程固化下来」
  • 用户想搞清楚某个界面操作背后调了哪些接口、请求体长什么样 —— 抓包 + 接口勘察
  • 用户在做 Web 端 E2E 测试、冒烟测试，需要从真实操作出发写用例
  • 用户抱怨「点了按钮不知道发生了什么」「接口文档和实际对不上」
  • 用户提到 codegen 但真正需要的是接口关联（codegen 只产选择器，不记录接口）

  也在需要安全地验证「写操作」时使用：本 skill 提供基线快照 → 执行 → 还原 →
  逐字节比对的模式，以及用路由拦截抓取请求体但不真正发送的做法。

  不适用于：纯 API 测试（没有界面参与）、移动端 App、桌面客户端。
---

# web-record

把手工的网页操作变成两样东西：一份**可回放的 Playwright 脚本**，和一份**接口调用记录**。

## 为什么不用 `npx playwright codegen`

codegen 只回答「点了哪里」，不回答「点完之后发生了什么」。当用户的真正目的是搞清楚
接口契约、或者想让脚本断言「这一步应该触发某个 POST 并返回 200」时，codegen 给不了。

本 skill 的录制器把两件事绑在一起：每个操作步骤下面挂着它触发的接口调用，包括请求体和
失败响应体。生成的草稿长这样：

```ts
await page.getByRole('button', { name: '确认', exact: true }).click();
//   ↳ POST /api/v1/policy/apply -> 200
//   ↳ GET  /api/v1/policy/status -> 200
```

## 快速开始

```bash
# 1. 准备环境（在目标项目里，或任意空目录）
cp -r <skill>/assets/* .          # playwright.config.ts / tsconfig.json / package.json
npm install

# 2. 录制
node <skill>/scripts/record.mjs --url https://app.example.com --name login-flow

# 浏览器窗口弹出 → 用户操作 → 关闭窗口 → 自动生成
```

输出到 `recordings/`：

| 文件 | 内容 |
|---|---|
| `<name>.json` | 原始记录：步骤 + 网络事件，都带毫秒时间戳 |
| `<name>.spec.ts` | 可跑的脚本草稿，接口调用作为注释挂在对应步骤下 |

常用参数：`--api /api/`（只记录含该片段的请求）、`--out <目录>`、`REC_CHROME_BIN`（指定浏览器）。

## 录制之后必须做的三件事

**草稿不是成品。** 直接提交 codegen 或本工具的原始输出是自动化脚本腐烂最快的方式。
录完请依次处理：

### 1. 收紧标了 `⚠ AMBIGUOUS` 的选择器

录制器会统计页面上有多少个元素的文本与目标完全相同。撞车的选择器在录制时能跑通
（点的就是当前那个），**回放时却可能点到另一个** —— 这类失败最难查，因为脚本不报错，
只是做错了事。

处理办法是加限定容器，而不是加 `.first()`：

```ts
// ❌ 草稿给的（能跑，但语义错了）
await page.getByText('删除', { exact: true }).first().click();

// ✅ 限定到具体行
await page.getByRole('row', { name: /张三/ }).getByText('删除').click();
```

### 2. 把接口注释换成断言

注释只是记录，不会失败。真正有价值的是让脚本验证接口行为：

```ts
const resp = page.waitForResponse((r) =>
  r.url().includes('/api/v1/policy/apply') && r.request().method() === 'POST');
await page.getByRole('button', { name: '确认' }).click();
expect((await resp).status()).toBe(200);
```

想断言请求体，用 `waitForRequest` 拿 `postData()`。这比断言界面文字稳得多，
因为界面文案会改，接口契约不会轻易改。

### 3. 删掉误操作

录制会忠实记录一切，包括点错了又点回来。这些步骤留在脚本里只会拖慢速度、制造脆弱点。

## 选择器优先级

录制器按这个顺序取，遇到就停：

| 顺序 | 方式 | 稳定性 |
|---|---|---|
| 1 | `getByTestId`（`data-testid` / `data-test` / `data-cy` / `data-qa`） | 最稳，专为测试而设 |
| 2 | `getByRole(role, { name })` | 很稳，跟着无障碍语义走 |
| 3 | `getByPlaceholder` | 稳，但占位符可能随文案改动 |
| 4 | `getByText` | 一般，需检查是否撞车 |
| 5 | `locator(cssPath)` | 兜底，随时会失效 |

**运行时自增 id 会被自动跳过**（如 `tip_box_10059`），因为它们每次加载都变，
用来定位必然在第二次运行时失败。同理，含 3 位以上数字的 class 也会被过滤。

落到第 5 档的元素，录制器会把它包成「存在则点」而不是必经步骤：

```ts
{
  const el = page.locator("div.dialog > span.close");
  if (await el.isVisible().catch(() => false)) await el.click();
}
```

这类元素绝大多数是关闭弹窗/提示条 —— 弹窗不出现时脚本不该失败。但这只是止血，
真正的修法是给这些元素加 `data-testid`，或改用语义定位。

## 深入

遇到具体问题时再读，不必预先加载：

| 文件 | 什么时候读 |
|---|---|
| [references/auth-and-session.md](references/auth-and-session.md) | 脚本第二次跑就跳登录页；SSO；token 在 sessionStorage；多标签页隔离 |
| [references/selectors.md](references/selectors.md) | 选择器反复失效；动态表格；页签位置漂移；自定义组件没有 role |
| [references/safe-writes.md](references/safe-writes.md) | 要验证会**改数据**的操作；需要抓请求体但不能真发出去 |
| [references/troubleshooting.md](references/troubleshooting.md) | 浏览器起不来；证书报错；偶发 5xx；请求没被记录 |

## 素材

`assets/` 里是可直接复制到目标项目的模板：

- `playwright.config.ts` —— 忽略自签证书、失败重试、串行执行、失败时留 trace/截图/录像
- `auth.setup.ts` —— 登录一次并导出登录态（**含 sessionStorage**，Playwright 原生不管这块）
- `fixtures.ts` —— `authedPage` fixture，把登录态在页面脚本执行前注回去
- `tsconfig.json` / `package.json`

## 判断这个 skill 是否用对了

录制器解决的是「**从真实操作出发**」的问题。如果用户已经清楚知道要调哪些接口、
界面只是个壳，那直接写 API 脚本更快 —— 别为了用录制器而用录制器。

反过来，如果用户的疑问是「这个按钮到底干了什么」「为什么文档写的和实际不一样」，
那录制是最短路径：一次操作同时拿到操作序列和接口真相。
