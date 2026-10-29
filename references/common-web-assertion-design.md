# 常用 Web 测试场景断言能力设计

- 状态：Proposed
- 日期：2026-10-29
- 适用仓库：`edr-cloud-recorder`
- 实施目标：录制时能自然表达常用业务断言，生成的 pytest 与 golden trace
  回放使用同一语义，并且不会因跳过、抢跑或定位错误产生假绿。

## 1. 背景与目标

当前 recorder 已支持元素文本、输入值、属性、可见性、勾选状态、数量和三类时间
断言，也会为写请求生成状态码和请求体断言。基础比较能力已经存在，下一阶段的重点
不是继续堆算子，而是让常见测试场景能够被完整、稳定地表达。

本设计解决四类问题：

1. **覆盖常用场景**：页面跳转、表单、提示/弹窗、列表表格、异步状态、分页、
   新标签页和接口结果都有直接的断言表达。
2. **期望值由用户确认**：允许读取当前值辅助填写，但任何需要 `expected` 的断言，
   用户未确认前都不能保存。系统不得把“当前看到什么”直接当成用户意图。
3. **保持因果顺序**：断言绑定到产生它的页面、前置动作和目标元素；输入、点击、
   跳转、新标签页与断言的先后不能被生成器重排。
4. **消灭假绿**：未知类型、无法转译、负断言抢跑、多元素歧义和读取失败必须明确
   失败，不得静默跳过或把错误当成实际值。

非目标：像素级整页快照审批、跨浏览器云矩阵、移动端原生控件、性能指标和无障碍
规范扫描。这些能力可以消费 recorder 产物，但不进入本轮断言菜单。

## 2. 当前基线与主要缺口

| 能力 | 当前状态 | 主要缺口 |
|---|---|---|
| 文本/value/attribute | 已有 | 缺少统一的等待稳定策略和清晰的目标预览 |
| visible/checked/count | 已有 | 缺 enabled/editable/selected；负断言可能在页面未加载时抢跑 |
| 时间断言 | 已有 | 语义完整，需纳入统一等待与错误报告 |
| URL/标题 | 回放内部会校验路由 | 用户不能在录制菜单中显式添加页面级断言 |
| 表格/列表 | 可用 scoped/nth 定位 | 缺少“稳定行 + 指定列”和集合断言的明确交互 |
| 新标签页 | 已有 pageId/openerPageId 因果信息 | 缺显式页面数量、目标页 URL/标题断言入口 |
| 网络 | 写请求自动生成断言 | 缺用户选择请求、状态、响应体字段和“不应发请求” |
| 生成与回放 | 共享比较核心 | 新类型必须继续保证双路径同判和硬拒未知类型 |

## 3. 场景能力矩阵

优先级含义：P0 是正确性地基；P1 覆盖最常用 UI 场景；P2 补页面与网络契约；
P3 是可延后的高级能力。

| 场景 | 用户要表达的命题 | 断言对象/示例 | 优先级 |
|---|---|---|---|
| 页面到达 | 点击后确实到了目标页 | URL path/query、标题、关键 heading | P1 |
| 表单回填 | 输入值被接受或被格式化 | value equals/contains/regex | 已有，P0 加固 |
| 表单可操作性 | 按钮是否可用、字段能否编辑 | enabled、editable、readonly | P1 |
| 单选/下拉 | 选择结果正确 | checked、selected value/text | P1 |
| 校验提示 | 错误或成功信息出现 | text + visible | 已有，P0 加固 |
| 提示消失 | toast/loading/modal 最终消失 | visible=false 或 count=0 + 稳定窗口 | P0 |
| 列表/表格 | 条数、目标行和单元格正确 | count、row scope、cell text | P1 |
| 排序/分页 | 首末项、当前页、下一页结果正确 | nth item、attribute、selected | P2 |
| 异步刷新 | 值最终变为期望并保持稳定 | eventual + stableMs | P0 |
| 新标签页 | 打开了正确的新页面 | pageId/opener + URL/title/page count | P2 |
| 写接口 | 请求发生且返回符合契约 | method/url/status/request/response subset | 已有，P2 开放交互 |
| 禁止副作用 | 某操作不应发请求 | network count=0，限定观察区间 | P2 |
| 动态时间 | 当前时间或固定时间窗 | localtime/timewindow/timerange | 已有 |

## 4. 录制交互设计

### 4.1 入口

- 主入口：右键网页元素。
- 备用入口：按住 `Alt`（macOS 为 `Option`）并左键点击元素。
- 两种入口必须只选择断言目标，不触发业务点击，也不生成普通 click 步骤。
- 页面级断言从菜单顶部的“断言范围”切换到“当前页面”；网络级断言切换到
  “最近请求”。不要再发明第三套快捷键。

### 4.2 断言编辑器

编辑器按以下顺序展示：

1. **范围**：元素 / 当前页面 / 最近请求。
2. **目标预览**：元素摘要、选择器、当前匹配数量、所属 pageId；页面范围显示 URL
   和标题；网络范围显示 method、URL、状态。
3. **断言类型**：只展示该范围支持的类型。
4. **比较方式**：由类型约束可选算子。
5. **Expected**：从当前值预填时标记为“读取值”，用户必须修改或勾选
   “确认使用当前值”后才能添加。布尔断言同样需要显式确认 true/false。
6. **等待策略**：超时、轮询间隔、稳定时长。默认使用产品统一值，普通用户不必展开。
7. **结果预览**：用当前页面即时显示“现在会通过/失败”，但预览结果不代替确认。

保存按钮的硬门槛：目标可解释、类型合法、算子合法、expected 已确认、数值/正则/
时间配置可解析。任何一项不满足都禁用保存并显示具体原因。

### 4.3 多元素目标

当选择器匹配多个元素时，菜单不得默默使用 `.first()`：

- 用户选择“元素数量”时保留集合选择器；
- 用户选择单元素断言时，优先给出稳定作用域（例如表格行 + 列）；
- 仍不唯一时展示候选数量，让用户选择 nth 或补充稳定锚点；
- nth 必须记录录制位置和候选摘要，回放时匹配数量变化要给出诊断；
- 时间等易变文本不能作为自身定位锚点。

## 5. 统一数据模型

保持现有 flat step 向后兼容，只新增可选字段。旧录制没有新字段时继续按当前默认值
执行。

```json
{
  "id": "rec-12",
  "type": "assert",
  "scope": "element",
  "assertion": "text",
  "op": "contains",
  "expected": "保存成功",
  "expectedConfirmed": true,
  "sel": "getByRole(\"status\")",
  "kind": "role",
  "pageId": "page-2",
  "openerPageId": "page-1",
  "afterStepId": "rec-11",
  "wait": {
    "timeoutMs": 5000,
    "pollMs": 100,
    "stableMs": 300
  }
}
```

字段规则：

- `scope`：`element | page | network`，缺省为 `element`。
- `expectedConfirmed`：新录制必须为 true；老录制缺省视为已确认，保证兼容。
- `afterStepId`：断言的直接因果前置步骤。生成器可以插入等待，不得把断言移到它前面。
- `wait.timeoutMs`：总预算，不允许每层重试分别消耗一份完整预算。
- `wait.stableMs`：条件首次满足后需连续保持的时间；0 表示首次满足即通过。
- `pageId/openerPageId`：沿用页面因果模型；页面断言也必须绑定页面。
- `expectedFrom`：继续承载 localtime/timewindow/timerange，不与静态 expected 混义。

### 5.1 类型与算子

元素范围：

- 已有：`text`、`value`、`attribute`、`visible`、`checked`、`count`；
- 新增：`enabled`、`editable`、`selected`；
- 时间类型仍落为 text/value + `expectedFrom`，避免重复执行路径。

页面范围：

- `url`：equals / contains / starts_with / matches；允许选择是否比较 origin、path、query、hash；
- `title`：沿用字符串算子；
- `page_count`：equals / at_least / at_most，默认只统计当前 browser context。

网络范围：

- `request_count`、`status`、`request_body`、`response_body`；
- body 对象使用 subset 语义，敏感字段继续脱敏并转类型匹配；
- “不应发请求”必须同时记录观察起止边界，不能在动作刚发生时立即判 0。

## 6. 等待、稳定与负断言语义

所有断言都采用“在总预算内轮询，满足后按需稳定”的统一模型：

```text
deadline = start + timeoutMs
循环读取 actual
  读取失败：记录最后异常，继续到 deadline
  条件不满足：清空 stableSince
  条件满足：
    stableMs == 0 -> 通过
    首次满足 -> stableSince = now
    now - stableSince >= stableMs -> 通过
超时 -> 失败，报告 expected、lastActual、lastError、selector、pageId
```

负断言（不可见、不包含、count=0、request_count=0）必须遵守额外规则：

1. 默认 `stableMs=300`，避免动画中间态或瞬时缺席假绿；
2. 若前一步产生可观测网络/导航，观察窗从该动作开始，到预算结束或稳定通过；
3. 列表 `count=0` 必须有加载完成证据（ready anchor、相关响应完成或显式等待），
   否则编辑器给出警告，生成检查可配置为硬拒；
4. 读取异常永远不是 actual，`not_equals`/`not_contains` 不能拿异常文本通过。

## 7. 定位与页面因果

- 断言保留 `pageId`、`openerPageId`、完整 origin/path/query/hash 和 frameChain。
- 新标签页动作必须先建立 popup waiter，再执行点击，然后将后续断言绑定新 pageId。
- 输入事件必须先提交 fill/type 步骤，再允许 Enter、点击或断言；定位元素的 name 不得
  使用用户刚输入的 value。
- 元素断言优先使用 role/test-id/label/稳定作用域；视觉模板只用于动作定位，不能作为
  业务 expected 的来源。
- 选择器在录制时匹配 0 个或单元素断言匹配多个且未消歧，保存即失败。

## 8. 生成、回放与校验

两条执行路径必须同判：

| 范围 | pytest 生成 | trace 回放 |
|---|---|---|
| element | Playwright expect 或 `rec_assert` 统一轮询 | `_assert_locator` 调同一比较核心 |
| page | `expect(page).to_have_url/title` 或统一轮询 | pageId 路由后调用同一 page reader |
| network | expect_response + `assert_subset` | response waiter + `_validate_response` |

发布前静态校验：

- scope/type/op 组合必须在白名单中；
- 需要 expected 的断言必须存在 expected 且已确认；
- page scope 不得带 DOM selector，element scope 必须带可转译 selector；
- 时间、正则、数量、timeout/stable 配置必须可解析；
- 无法生成 pytest 或 trace 的任何一步都让整份产物生成失败，不允许注释后跳过；
- 生成后统计 recording、pytest、trace 三处断言数量和 id，必须一一对应。

失败报告至少包含：步骤 id、范围、断言类型、算子、expected、last actual、最后读取异常、
页面 URL/pageId、selector 或请求摘要、耗时和稳定时长。断言失败归类为 `assertion`，
定位失败为 `automation`，配置/产物错误为 `infrastructure`。

## 9. 实施阶段

每阶段单独提交，可独立回滚；每项新增能力必须有一个“缺少该能力时会失败”的测试。

### P0：正确性地基

1. 增加 `expectedConfirmed`、`afterStepId`、`wait` 的录制与 schema 校验。
2. 抽取统一 deadline + stable 轮询，生成与回放共用。
3. 负断言默认稳定窗口；读取异常不得参与比较。
4. 生成后做三产物断言 id/数量一致性检查。
5. 菜单显示选择器、匹配数量、当前值确认状态。

验收：老录制兼容；所有已有断言同判；故意删除/改写任一产物断言时校验失败；
异步先缺席后出现的元素不能让 visible=false 抢跑通过。

### P1：常用 UI 场景

1. 新增 enabled、editable、selected。
2. 新增 page URL/title 断言菜单与执行链。
3. 表格行 + 列作用域交互，多元素不得静默 `.first()`。
4. 为 toast、modal、loading、表单错误提供预设，但预设只填配置，不改变底层语义。

验收：页面跳转、表单提交、下拉选择、提示出现/消失、表格目标行五条真实 Chromium
fixture 在 pytest 与 trace 回放两条路径都通过；逐项注入反例时都能失败。

### P2：页面与网络契约

1. page_count 与 popup 页面级断言。
2. 最近请求选择器；开放 status/request/response subset 断言。
3. request_count=0 的有界观察窗。
4. 网络与触发动作按 request id/时间窗关联，不能靠“最后一个同 URL 请求”猜。

验收：同页跳转、popup、并发同 URL 请求、应发/不应发请求均有正反例。

### P3：高级能力（本轮可不实现）

- 集合 every/some/顺序断言；
- 组合断言组（同一行同时满足多个条件）；
- 经人工审批的视觉快照断言；
- 断言模板收藏与参数化。

## 10. 预计改动位置

| 文件 | 责任 |
|---|---|
| `scripts/recorder-inject.mjs` | 菜单、expected 确认、范围、匹配预览、wait 配置 |
| `scripts/record.py` | page/request 因果信息、落盘校验、模板采集 |
| `assets/rec_assert.py` | 统一 deadline/stable 轮询与新 UI 状态 reader |
| `scripts/generate_spec.py` | 新类型生成、三产物一致性、硬拒不可转译 |
| `scripts/generate_trace.py` | 新 assertion spec 和 page/network scope |
| `scripts/replay_trace.py` | page/network 路由、新类型回放、结构化失败 |
| `scripts/trace_schema.py` | 可选字段访问器与兼容校验 |
| `test/test_assert_menu.py` | 菜单与 expected 明确确认 |
| `test/test_generated_execution.py` | pytest/trace 双路径同判 |
| `test/test_trace_replay.py` | 因果、等待、失败分类、网络场景 |

## 11. 完成定义

P0 + P1 全部完成才算“常用 UI 断言能力完成”；P2 可作为下一里程碑，但页面 URL/title
属于 P1，不得延后。交付必须同时满足：

- 新旧全部自动化测试通过，`scripts/check_visual.py` 与 skill validator 通过；
- 至少一条 headed 录制覆盖输入 → 点击 → 跳转/提示 → 断言 → 回放；
- 至少一条 popup 录制在新页面执行 URL/title/元素断言；
- 每类新增断言都有正例、反例、异步例和读取失败例；
- recording.json、生成 pytest、trace.json 的断言 id 和数量一致；
- 不弱化已有断言、不通过增加 sleep 掩盖竞态、不静默跳过失败步骤；
- 每个阶段一个清晰 commit；仅在全量验证后推送。

若实现过程中发现当前数据模型无法保持上述因果或双路径同判，应先更新本设计并记录
反例，再继续编码；不得边实现边用兼容降级绕过设计约束。
