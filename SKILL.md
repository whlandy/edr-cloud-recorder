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

常用参数：`--api /api/`（只记录含该片段的请求）、`--out <目录>`、`--config <文件>`。

### 配置文件

重复输同样的参数很烦，可以放一个 `config.json`（照抄 `config.example.json`）。查找顺序：

```
--config 指定的路径
./config.json                                  项目内覆盖
~/.config/edr-cloud-recorder/config.json       用户级默认
```


```json
{
  "baseUrl": "https://app.example.com",
  "entryPath": "/index.html#/home",
  "auth": { "user": "alice", "password": "" },
  "record": { "apiFilter": "/api/", "outDir": "recordings" }
}
```

命令行参数优先级高于配置文件。

**存凭据请放用户级目录** `~/.config/edr-cloud-recorder/config.json`（`chmod 600`）。
项目目录常常就是仓库目录，`.gitignore` 挡不住 `git add -A`；而 `~/.config` 根本不会被
任何仓库看见。加载器在文件存了密码却权限过宽时会提醒。不想落盘就留空，走环境变量
`REC_USER` / `REC_PASSWORD`。

## 录制时加断言：右键元素

录制器只会记录你**做了什么**，不会自己判断**应该是什么**。断言得由人给出 ——
右键任意元素，弹出：

```
断言类型：[文本等于 ▼]
Expected：[已完成        ]
          ☐ 允许空值   取消   添加断言
```

- `Expected` 默认填入元素当前值，**可以改**。保存的是你确认过的那个值，不是运行时读到的值 ——
  这是断言有意义的前提，否则就成了同义反复。
- 留空时「添加断言」是禁用的。空字符串是合法断言（比如断言输入框被清空），
  但必须勾「允许空值」明确表态，不能默默通过。
- `可见性` / `勾选状态` 的 expected 是布尔，用下拉选 true/false。

支持五种：

| 类型 | expected | 生成 |
|---|---|---|
| 文本等于 | 字符串 | `toHaveText(expected)` |
| 输入值等于 | 字符串 | `toHaveValue(expected)` |
| 可见性 | 布尔 | `toBeVisible()` / `toBeHidden()` |
| 勾选状态 | 布尔 | `toBeChecked()` / `not.toBeChecked()` |
| 属性等于 | 字符串 + 属性名 | `toHaveAttribute(attr, expected)` |

**所有断言都带 `expected`**，`visible: false` 和 `checked: false` 用值表达，
不靠断言名隐含预期。这样"不可见""未选中"也能准确表达：

```json
{ "type": "assert", "assertion": "text",
  "sel": "getByTestId(\"order-status\")", "expected": "已完成" }
```

菜单渲染在 Shadow DOM 里（页面 CSS 五花八门，不隔离会被改得没法用），
它自身的点击也不会被当成被录的操作。按 Esc 或点别处关闭。

## 生成的脚本已经做了什么

先说三件**直接决定回放能不能跑通**的，它们都由生成器自动完成 —— 这三件事以前
每录一次就要手工补一次，而且漏了任何一件，失败都不会指向真正的原因。

### 登录段直接不生成

录制几乎总是从登录页开始，于是登录必然被录进来；而它几乎总是回放不了：表单常在
iframe 里、常有同 placeholder 的诱饵输入框、密码又不该写进脚本。更重要的是，
它对用例的意图毫无贡献 —— 每条用例重登一次，只是让每条用例都多一个失败点。

所以生成器**砍掉开头连续的那一段登录**（iframe 路径含 login/sso，或密码框），
改用 `authedPage`：登录态由 `tests/auth.setup.ts` 存一次、所有用例复用。
砍掉的步骤原样留在文件头注释里，需要时能捡回来。只砍开头 —— 登录之后再出现的
iframe 操作是正经业务。

### 自带关弹窗前奏

草稿开头会有一句 `await dismissOverlays(page);`。别小看它，今天这一条就卡了三轮：

- 首启弹窗**常常不止一个**（实测叠了两个），只关一个，剩下那层照样吞掉所有点击；
- 遮罩关掉后**还会残留一会儿**，而它拦截点击时 Playwright 认为 click «成功了» ——
  失败报在后面某个 `waitForResponse` 上，看着像「接口没发」；
- 遮罩**常驻 DOM 只靠 CSS 隐藏**，所以判「可见数为 0」，判 `toHaveCount(0)` 永远等不到。

### 作用域不会锚在时间上

给撞车文本找作用域时，跳过日期、时刻、长数字、UUID 这类值。实测栽过：一行结果被
锚在 `hasText: "2026-08-14 10:22:29"` 上，**当场连跑三遍全绿**，几小时后数据重跑就
再也找不到那一行。这类失败延迟发作，录完当场验证不出来 —— 所以只能在生成时避开。

---

另外两件最费手工的事也是自动完成的。

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

**数字型的时间戳同样要放宽**，换成 `expect.any(Number)`。「最近 30 天」这类默认
查询条件会把录制那一刻的毫秒时间戳带进请求体（实测 `endTime` 就是录制时的"现在"），
只放宽字符串的话，这种断言下一次运行必然对不上。门槛取 1e9：10 位是秒级时间戳，
13 位是毫秒级，业务上的页码、数量都远小于它。

**GET 保持注释。** 一次点击可能连带十几个读请求，全断言只会让用例难读又易碎。

### 剩下的人工部分

- 删掉与意图无关的误操作 —— 录制会忠实记录一切，包括点错了又点回来
- 会产生数据的用例补上清理逻辑
- 仍标着 `⚠ AMBIGUOUS` 的选择器（少数）需要人工限定

点在页面空白处的那一下不会进草稿：它会一路上溯到 `html`，生成 `locator("html").click()`
这种回放时点了等于没点的步骤，留着只会让读的人反复判断它有没有意义。

## 回放前过一遍这张清单

草稿不是拿来直接跑的。下面几类问题录制器**原理上**看不见，只能人工过一遍 ——
它们的共同点是：录制时那个"活人"顺手处理掉了，脚本却无从知道要处理。

**① 前置遮罩：首次引导、公告、分辨率提示**

这些东西出现与否取决于账号状态和历史操作。录制时人手一关就过去了，脚本会被
`<div class="mask">` 拦住点击直到超时 —— 报错信息还只说"元素不可点击"。

**草稿开头的 `dismissOverlays(page)` 已经处理了常见情形**（关到没有可见遮罩为止）。
但它认的是几个常见 class；组件库不同就得给它传选择器：

```ts
await dismissOverlays(page, ['.my-dialog-close', '.guide-skip'], '.my-mask');
```

关不掉的弹窗仍然要人看一眼 —— 有些引导必须走完流程，点关闭没用。

> **不要用 `el.remove()` 把遮罩从 DOM 里删掉。** 这招看着很爽，代价是：遮罩背后的
> 应用状态没变（引导流程仍在进行、body 仍是 `overflow:hidden`），后面的操作会以更
> 难查的方式失败；更糟的是，万一某天这个遮罩是**真 bug**（该关的没关），删 DOM 会
> 让用例继续绿灯。用例的价值就在于该红的时候红。

**② 登录段的步骤顺序**

录制器按事件发生的时间顺序忠实记录，包括人类的自我修正：点错了又点回来、
填了一半改主意。人做这些毫无障碍，脚本照着跑就未必说得通。

有一种顺序错误是**录制器自己造成的**，已经自动修掉了：值是在 `change`（失焦或回车
之后）才记的，按键则是按下就记，于是「打字 + 回车」录出来必然是 `press` 在 `fill`
前面 —— 照原样回放就是在空输入框上回车。生成时会把同一字段上紧挨着的这一对交换过来
（时间戳跟着换，否则回车触发的请求会挂到填值那一步下面）。不设时间阈值：实测同一次
录制里，密码那对间隔 18ms，用户名那对 1025ms，卡阈值只会漏修打字慢的人。

**剩下的顺序问题仍然要人看** —— 那些是真的人类改主意，录制器无从分辨。

登录本身已经不进草稿了（见上文「登录段直接不生成」），所以这一条现在只影响
业务步骤。登录态的存取见 [references/auth-and-session.md](references/auth-and-session.md)。

**③ 深层导航改直达**

侧边栏菜单在小 viewport 下要滚动才可见，录制时窗口大、回放时窗口小就点不到。
一串菜单点击如果净效果只是"到某个页面"，换成 `page.goto()` 更稳也更快。
生成的草稿里，地址发生变化的地方会带一条 `⇢ 地址已变为 ...` 的提示。

> **不要靠加 `waitForTimeout` 来"修"失败。** Playwright 的定位器本身会等元素
> 可见、可点、稳定，加 sleep 能"修好"的场景，绝大多数真实病因是别的：选择器撞车
> （strict mode）、被遮罩挡住、或者点到了长得一样的另一个元素。sleep 只是把失败
> 概率压低，换来的是每次跑都白等，以及在 CI 上偶发失败。
>
> 真需要等"某件事发生"时，等那件事本身：`expect.poll`、`waitForResponse`、
> 对目标状态的 `expect`。`networkidle` 同样不推荐 —— 长轮询或心跳接口会让它永远等不到。

## 选择器优先级

录制器按这个顺序取，遇到就停：

| 顺序 | 方式 | 稳定性 |
|---|---|---|
| 1 | `getByTestId`（`data-testid` / `data-test` / `data-cy` / `data-qa`）**且全页唯一** | 最稳，专为测试而设 |
| 2 | 输入框专用：`getByLabel` → `getByPlaceholder`，**都要求全页唯一** | 稳，直接绑定表单语义 |
| 2.5 | 输入框退路：`locator('#id')`（id 不含 3 位以上数字） | 一般，取决于 id 是否手写 |
| 3 | `getByRole(role, { name })`，**要求同 role+名的元素唯一** | 很稳，跟着无障碍语义走 |
| 4 | `getByText` | 一般，需检查是否撞车 |
| 5 | `locator(cssPath)` | 兜底，随时会失效 |

**testid 排第一，但先验唯一性。** 它「本该」是为测试而设的唯一标识，可组件框架常常
批量吐出同一个值 —— 实测遇到过 245 个元素共用 `data-testid="text-comp-span"`。那时它
不但不是最稳的，反而是最坏的：回放必然 strict mode 报错，而且看代码完全看不出问题。
所以录制器会数一遍，不唯一就当它不存在，往下走 role / text 分支。

顺带一提，`getByTestId` 只认 Playwright 配置里的 `testIdAttribute`（默认 `data-testid`）。
元素只有 `data-cy` / `data-qa` 时，录制器生成属性选择器 `locator('[data-cy="..."]')`，
而不是 `getByTestId` —— 后者语法上没错，回放时却根本找不到元素。

**唯一性检查对 label / placeholder / role+名同样生效。** 登录页尤其容易踩：实测一个
表单里有两个 placeholder 完全相同的输入框，真的那个带 `id="username"`，另一个是诱饵。
录制时点哪个都能跑通，回放**第一步**就 strict mode 报错 —— 而报错长得像"页面变了"。
这时录制器退到 `locator('#username')`，而不是 `getByText(无障碍名)`：后者语法正确，
但 `input` 没有文本内容，回放永远找不到元素 —— 这种"看着对的死选择器"比报错更难查。

输入框之所以插在 role 前面：只有 placeholder 的输入框，其无障碍名恰好**就是** placeholder，
于是 role 分支会产出 `getByRole('textbox', { name: '请输入用户名' })`。那样能用，但一旦后来
给它补了 `<label>`，无障碍名就变了，选择器随之失效。`getByPlaceholder` 只依赖 placeholder 本身。

**开关会录成"拨到指定状态"，而不是盲点一下。** 自研开关多半是 `div` 加 class，
不是 `input[type=checkbox]`。只录一次 `click` 的话，回放时初始状态一旦与录制时不同，
就会**朝反方向拨** —— 脚本不报错，只是把开关设错了，这类失败极难查。

录制器认 `role="switch"`、`aria-checked`，以及 class 里带 switch/toggle 的元素，
记下"这一下要拨到什么状态"，生成幂等的代码：

```ts
{
  const sw = page.getByRole('switch', { name: '自保护', exact: true });
  if ((await sw.getAttribute('aria-checked')) !== "true") await sw.click();
  await expect(sw).toHaveAttribute('aria-checked', "true");
}
```

**状态怎么表达的，也一并记下来。** 没有 aria 的自研开关多半把状态写在 class 上
（`<div class="eui_toggle_container toggled">`）。只按 aria 那套生成的话，
`getAttribute('aria-checked')` 恒为 `null`，于是每次都点、断言每次都挂。
class 型的生成这样：

```ts
{
  const sw = page.getByText('自保护', { exact: true });
  const state = sw.locator('.eui_toggle_container').first();
  const isOn = () => state.evaluate((e) => e.classList.contains("toggled"));
  if ((await isOn()) !== true) await sw.click();
  await expect.poll(isOn).toBe(true);
}
```

注意**点的和读的不是同一个元素**：整行可点，但状态写在内层容器上。不记这个偏移，
读到的永远是行、永远读不出状态。

**开关是点击目标的后代时也能认出来。** 表单常把「标签 + 说明文字 + 开关」放一整行，
整行都可点，这时事件目标是行容器，开关在它**里面**。只往上找会漏掉。

还有一种静态看不出来的情形：class 型开关在「关」的时候往往什么标记都没有，只有开启
才多一个 `toggled`。标记缺席既可能是关，也可能是这一层根本不带状态。录制器的办法是
看拨完之后**哪一层的 class 变了** —— 变的那层就是状态层。恰好一层变化时才认，
多层一起变说明猜不准。

认不出状态的开关会退化成普通点击 —— 宁可少做，也不猜一个可能相反的状态。

**二次确认要自己补。** 有些开关拨动时会弹确认框，有些不弹，同一个页面里都可能不一致。
录制器把确认点击忠实记成了独立一步，但它是否出现取决于开关的方向和具体项，
所以回放时该写成「存在则点」而不是必经步骤：

```ts
const confirm = page.getByRole('button', { name: '确认', exact: true });
if (await confirm.isVisible().catch(() => false)) await confirm.click();
```

**浮层里的元素会被限定到浮层内。** 下拉选项常渲染在 portal 里（挂到 body 底下
而不是触发器旁边），而且**触发器显示的值和选项文本往往一模一样**：

```ts
// ❌ 撞车：触发器显示「Windows系统」，选项也叫「Windows系统」
await page.getByText('Windows系统', { exact: true }).first().click();

// ✅ 限定到浮层
await page.getByRole('listbox').getByText('Windows系统', { exact: true }).click();
```

识别方式：`role` 是 listbox/menu/dialog/tooltip 等，或者「绝对定位 + z-index ≥ 100」——
后者是浮层最通用的特征，能覆盖没写 ARIA 的自研组件。

**iframe 里的元素会自动加 frameLocator。** 登录表单放在 iframe 里是很常见的做法，
而 `page.getByX()` 只搜主文档 —— 不处理的话录制时能跑通、回放必然找不到元素。
录制器记录每一步的归属框架，生成时自动包一层：

```ts
await page.frameLocator('iframe[src*="custom_login.html"]').getByPlaceholder('用户名').fill('...');
```

用 `src` 片段而不是整条 src 或 nth：src 常带随机 query，nth 会随布局变。

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
| [references/selectors.md](references/selectors.md) | 选择器反复失效；动态表格；页签位置漂移；自定义组件没有 role；点了没反应／时灵时不灵 |
| [references/safe-writes.md](references/safe-writes.md) | 要验证会**改数据**的操作；需要抓请求体但不能真发出去 |
| [references/troubleshooting.md](references/troubleshooting.md) | 浏览器起不来；证书报错；偶发 5xx；请求没被记录 |
| [references/ui-assertions.md](references/ui-assertions.md) | 断言读到的和你以为的不一致；界面不刷新；连错窗口/连错机器；怎么选断言点 |
| [references/endpoint-orchestration.md](references/endpoint-orchestration.md) | 云端改了、要到**终端上**验证是否生效；edr-wd 怎么装、怎么配合 |

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
| 断言生成、易变值放宽、变量命名、代码结构、步骤顺序（先回车后填值那类） | 选择器、录制时才判断得了的过滤（如空白处点击） |

选择器（包括撞车文本的作用域）是在**录制当时**依据活的 DOM 算出来的，结果已经固化在
JSON 里。页面早就不在了，重新生成时无从判断「这个文本在哪个容器内唯一」。
所以旧录制里的 `.first()` 不会因为重新生成而消失 —— 那需要重录。

## 云端 + 端侧联动（orchestrate/）

有些效果**只在终端上才看得见**：云端下发一条策略，接口返回 200 只说明服务端记下了，
终端有没有真的生效是另一回事。`orchestrate/` 把两边串起来。

```python
sc = Scenario("某策略的云→端联动")
sc.cloud("读取当前配置", read_cloud)
sc.endpoint("连接并校验身份", lambda: ep.connect().assert_matches(obj_name))
sc.endpoint("采集端侧基线",  lambda: snapshot(ep.table_rows(refresh_text="Refresh")))
sc.cloud("下发变更",        apply_change)
sc.until("等待端侧出现新记录", probe, timeout=90, interval=5)   # ← 时间差在这里
sc.endpoint("断言新记录符合预期", assert_new)
sc.cloud("还原",            restore)
sc.run()
```

端侧能力来自 **edr-wd**（独立项目，通过 MCP 驱动 Windows/macOS GUI），
这里只做一层薄封装。安装、配置、以及它和本 skill 的分工，见
[references/endpoint-orchestration.md](references/endpoint-orchestration.md)。

**关键约束：等生效只能朝端侧轮询。** 云端接口未必暴露"终端是否已应用"的进度 ——
实测过一个产品，真实下发前后云侧的状态字段纹丝不动。固定 `sleep` 是错的：
短了偶发失败，长了每个场景白等几分钟。`until` 就是为此存在的。

`orchestrate/example_scenario.py` 是可照抄的模板，云端部分留了接口给你实现。

## 自检

改动录制器后跑一遍，验证它仍然符合上面这些承诺：

```bash
node test/verify.mjs
```

它会造一个包含全部边界情况的页面（同名元素、自增 id、密码框、会发请求的按钮），
用真实浏览器录一遍，逐条断言。60 项全过才退出 0。

**加了新能力就往里加断言。** 这个文件是上面所有承诺的唯一执行者 —— 承诺写进
SKILL.md 却没有对应断言，下一次重构就会悄悄把它改没。

## 素材

`assets/` 里是可直接复制到目标项目的模板，**目录结构照搬**（`cp -r assets/* .`）：

```
playwright.config.ts     忽略自签证书、失败重试、串行执行、失败时留 trace/截图/录像
tsconfig.json
package.json
manual-login.mjs         站点要求验证码时的兜底：人工登录一次，导出登录态复用
tests/
  auth.setup.ts          登录一次并导出登录态（含 sessionStorage，Playwright 原生不管这块）
  fixtures.ts            authedPage fixture，把登录态在页面脚本执行前注回去
  scrub-auth-artifacts.ts 跑完清掉含明文密码的失败现场
```

`tests/` 下那三个文件的位置是**配置写死的**：`globalTeardown` 指向 `./tests/scrub-auth-artifacts.ts`，
`setup` 项目按 `auth.setup.ts` 匹配，`fixtures.ts` 按 `__dirname/../.auth` 找登录态。
平铺到根目录的话，`npx playwright test` 连配置都加载不了。

**录制草稿要跑，得显式打开：**

```bash
REC_DRAFTS=1 npx playwright test recordings/xxx.spec.ts
```

默认的 `npx playwright test` 只收 `tests/` 里整理过的用例。草稿不进默认收集有两个原因，
第二个更要紧：

- 草稿按定义是半成品，可能引用还没写的 helper。**一个文件解析失败会让整次收集归零** ——
  实测过一份引用了不存在的 `./fixtures` 的草稿，把整个工程的用例列表打成 0。
- 草稿里常有**未经审阅的真实写操作**和明文密码登录。它不该被一句 `npx playwright test`
  顺带跑掉 —— 尤其是在你以为自己只是在跑回归测试的时候。

但也不能让它压根跑不了：`testDir` 只写 `./tests` 的话，`recordings/` 里的草稿连显式指定
文件名都跑不了（Playwright 不收 testDir 之外的文件），而那正是录完之后第一件想做的事。

**验证码不要绕。** 它存在的目的就是拦自动化登录，破解既不该做、也不可靠。
正确做法是承认登录是低频操作：人过一次，之后复用会话，直到过期再来一次。
`manual-login.mjs` 就是干这个的 —— 开一个窗口等你登录，检测到成功后自动导出并自检连通性。

## 判断这个 skill 是否用对了

录制器解决的是「**从真实操作出发**」的问题。如果用户已经清楚知道要调哪些接口、
界面只是个壳，那直接写 API 脚本更快 —— 别为了用录制器而用录制器。

反过来，如果用户的疑问是「这个按钮到底干了什么」「为什么文档写的和实际不一样」，
那录制是最短路径：一次操作同时拿到操作序列和接口真相。
