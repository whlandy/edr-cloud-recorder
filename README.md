# web-record

把网页上的手工操作录制成可重复执行的 Playwright 脚本，同时抓取每一步触发的 HTTP 接口
（含请求体、状态码、失败响应）。

这是一个 [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)，
但脚本本身**不依赖任何特定 agent 或浏览器扩展** —— 只要 Node + Playwright，
在终端里直接跑也可以。

## 和 `playwright codegen` 的区别

codegen 只回答「点了哪里」，不回答「点完之后发生了什么」。本工具把两件事绑在一起：

```ts
await page.getByRole('button', { name: '确认', exact: true }).click();
//   ↳ POST /api/v1/policy/apply -> 200
//   ↳ GET  /api/v1/policy/status -> 200
```

当你的目的是搞清楚接口契约、或者想让脚本断言「这一步应该触发某个 POST 并返回 200」时，
codegen 给不了答案。

## 用法

```bash
# 在目标项目里准备环境
cp <此仓库>/assets/{package.json,playwright.config.ts,tsconfig.json} .
npm install

# 录制
node <此仓库>/scripts/record.mjs --url https://app.example.com --name login-flow
```

浏览器窗口弹出 → 正常操作 → 关闭窗口 → 输出到 `recordings/`：

| 文件 | 内容 |
|---|---|
| `<name>.json` | 原始记录：步骤 + 网络事件，都带毫秒时间戳 |
| `<name>.spec.ts` | 可跑的脚本草稿，接口调用作为注释挂在对应步骤下 |

## 结构

```
SKILL.md              技能定义：触发条件、工作流、录制后必做的三件事
scripts/
  record.mjs          录制器
  chrome-path.mjs     跨平台探测本机 Chromium（避免为版本号重下 170MB）
assets/               可直接复制到目标项目的模板
  playwright.config.ts  忽略自签证书 / 失败重试 / 串行 / 失败留 trace
  auth.setup.ts         登录并导出登录态（含 sessionStorage）
  fixtures.ts           authedPage + clickIfPresent / confirmAndCapture / snapshot
orchestrate/
  scenario.py           云端+端侧交替编排：cloud / endpoint / until 三种步骤
  endpoint.py           edr-wd 薄封装（驱动终端 GUI）
  example_scenario.py   可照抄的模板
references/
  endpoint-orchestration.md  edr-wd 是什么、怎么装、怎么和本 skill 配合
  auth-and-session.md   脚本第二次跑就跳登录页怎么办
  selectors.md          怎么让选择器活过第二次运行
  safe-writes.md        安全地验证会改数据的操作
  troubleshooting.md    浏览器起不来、证书、5xx、请求没被记录
```

## 设计上的几个取舍

**密码不进产物。** `type=password` 的输入只记录「填了密码」这个动作，值在生成的脚本里
替换成 `process.env.REC_PASSWORD`。所以 spec 可以安全提交。

**运行时生成的 id 会被跳过。** 像 `tip_box_10059` 这种自增 id 每次加载都变，用它定位
必然在第二次运行时失败。含 3 位以上数字的 id 和 class 一律不用。

**文本撞车会被标出来。** 录制器统计页面上有多少元素文本完全相同。撞车的选择器录制时
能跑通、回放时可能点错 —— 这类失败最难查，所以宁可在生成时就警告。

**只能用 CSS 兜底的点击会被包成「存在则点」。** 这类元素绝大多数是关闭弹窗，
弹窗不出现时脚本不该失败。

**失败响应体会被记录，成功的 GET 不会。** 排查 4xx/5xx 时响应体是唯一有用的信息；
成功的 GET 响应动辄几十 KB，全存没有价值。

## 依赖

**录制器**：Node 18+、`@playwright/test`、一个 Chromium 构建（系统装的或 Playwright 缓存里的都行）。

**编排（可选）**：Python 3.10+，以及 [edr-wd](references/endpoint-orchestration.md)
——只有需要到终端上验证效果时才用得上，录制器本身不依赖它。

## 许可

[MIT](LICENSE)
