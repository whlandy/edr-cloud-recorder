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

## 生成的脚本已经做了什么

两件最费手工的事是自动完成的。

### 撞车的文本会自动加作用域

页面上有三个「删除」时，`getByText('删除')` 录制时能跑通（点的就是当前那个），
**回放时却可能点到另一行** —— 这类失败最难查，因为脚本不报错，只是做错了事。

录制器不会甩个 `.first()` 了事，而是向上找到「该文本在其内部唯一」的最近祖先，
为它算一个定位方式，生成的就是人工会写的那种：

```ts
await page.locator('tr', { hasText: '李四' }).getByText('删除', { exact: true }).click();
await page.getByRole('dialog').getByText('确认', { exact: true }).click();
```

作用域按可靠性挑：role + 无障碍名 > role 单独（弹窗最常见）> 标签 + 容器内的唯一文本。
实在找不到才退回 `.first()` 并标 `⚠ AMBIGUOUS`，那时候需要人工处理。

### 写请求会自动变成断言

注释不会失败，断言才会。所以非 GET 的调用会被包成「等待响应 + 断言」：

```ts
const [resp1] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/api/v1/policy') && r.request().method() === 'POST'),
  page.getByRole('dialog').getByText('确认', { exact: true }).click(),
]);
expect(resp1.status()).toBe(200);
expect(resp1.request().postDataJSON()).toMatchObject({
  "name": "123",
  "ruleList": [{ "category": 3, "baselineId": expect.any(String) }],
});
```

请求体里的 UUID 和长数字 ID 会换成 `expect.any(String)` —— 直接把整个 body 塞进
`toMatchObject` 会因为这些值每次都变而立刻失效，但整条删掉又丢了「这个字段必须存在」
的信息。保留结构、放宽易变值，字段在不在、类型对不对仍然被守住。

**GET 保持注释。** 一次点击可能连带十几个读请求，全断言只会让用例难读又易碎。

### 剩下的人工部分

- 删掉与意图无关的误操作 —— 录制会忠实记录一切，包括点错了又点回来
- 会产生数据的用例补上清理逻辑
- 仍标着 `⚠ AMBIGUOUS` 的选择器（少数）需要人工限定

## 选择器优先级

录制器按这个顺序取，遇到就停：

| 顺序 | 方式 | 稳定性 |
|---|---|---|
| 1 | `getByTestId`（`data-testid` / `data-test` / `data-cy` / `data-qa`） | 最稳，专为测试而设 |
| 2 | 输入框专用：`getByLabel` → `getByPlaceholder` | 稳，直接绑定表单语义 |
| 3 | `getByRole(role, { name })` | 很稳，跟着无障碍语义走 |
| 4 | `getByText` | 一般，需检查是否撞车 |
| 5 | `locator(cssPath)` | 兜底，随时会失效 |

输入框之所以插在 role 前面：只有 placeholder 的输入框，其无障碍名恰好**就是** placeholder，
于是 role 分支会产出 `getByRole('textbox', { name: '请输入用户名' })`。那样能用，但一旦后来
给它补了 `<label>`，无障碍名就变了，选择器随之失效。`getByPlaceholder` 只依赖 placeholder 本身。

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

## 对旧录制重新生成

生成器是独立模块（`scripts/generate-spec.mjs`），可以拿一份旧的 `<name>.json` 重新产出脚本：

```js
import { generateSpec } from '<skill>/scripts/generate-spec.mjs';
const d = JSON.parse(fs.readFileSync('recordings/old.json', 'utf-8'));
fs.writeFileSync('recordings/old.spec.ts',
  generateSpec({ steps: d.steps, net: d.net, startUrl: d.startUrl, name: 'old' }));
```

**但要清楚它能修什么、不能修什么**：

| 能 | 不能 |
|---|---|
| 断言生成、易变值放宽、变量命名、代码结构 | 选择器 |

选择器（包括撞车文本的作用域）是在**录制当时**依据活的 DOM 算出来的，结果已经固化在
JSON 里。页面早就不在了，重新生成时无从判断「这个文本在哪个容器内唯一」。
所以旧录制里的 `.first()` 不会因为重新生成而消失 —— 那需要重录。

## 自检

改动录制器后跑一遍，验证它仍然符合上面这些承诺：

```bash
node test/verify.mjs
```

它会造一个包含全部边界情况的页面（同名元素、自增 id、密码框、会发请求的按钮），
用真实浏览器录一遍，逐条断言。13 项全过才退出 0。

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
