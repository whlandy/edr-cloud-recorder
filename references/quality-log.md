# 持续质量记录 — 测试场景矩阵与轮次记录

持续维护：每轮任务从矩阵选一个最高价值缺口推进，结束后更新本文件。
状态含义：✅ 已覆盖（真实录制证据 + 双回放 + 正反验证）；🔒 仅单元测试覆盖；
⚠️ 部分覆盖；❌ 尚未覆盖。Win-headed 列：在 Windows headed Chrome 上验证过才打 ✅。

## 测试场景矩阵

| 场景 | 覆盖 | 真实录制 | pytest 回放 | trace 回放 | 反向验证 | Win-headed | 证据 |
|---|---|---|---|---|---|---|---|
| 文本 equals/contains/not_* | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | recordings/tiangonglab-8000-recording, test_acceptance_p1_scenarios |
| 输入值 value + 正负算子 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | test_acceptance_p1_scenarios |
| visible/hidden（含负断言稳定窗） | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | test_review_p1_completion |
| checked/unchecked | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | test_review_p1_completion |
| enabled/editable/selected | ✅ | ⚠️ 单元+两链 | ✅ | ✅ | ✅ | ❌ | test_review_p1_completion, test_assert_menu |
| count equals/at_least/at_most | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | test_review_generation_consistency |
| 动态时间 localtime/timewindow/timerange | ✅ | ✅ | ✅ | ✅ | ⚠️ | ❌ | recordings/tiangonglab-8000-recording |
| expected 显式确认（拒自动猜测） | ✅ | n/a | ✅ | ✅ | ✅ | ❌ | test_review_p1_completion, test_assert_menu |
| 多元素歧义（禁静默 first） | ✅ | n/a | ✅ | ✅ | ✅ | ❌ | test_review_p1_completion, test_assert_menu |
| 页面 URL/title 断言 | ✅ | ⚠️ | ✅ | ✅ | ✅ | ❌ | test_p2_page_network |
| page_count | ✅ | ⚠️ | ✅ | ✅ | ✅ | ❌ | test_p2_page_network（两链同判，真实 Chromium） |
| **网络 status/request_body/response_body** | ✅（2026-10-30 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 201→500 双链失败） | ❌ | recordings/p2-network-scope-recording, test_p2_network_scope |
| **request_count=0 有界观察窗** | ✅（2026-10-30 本轮） | ✅ headed | ✅ | ✅ | ✅（真发请求双链失败 actual=1 + 污染 reqUrl 双链失败） | ❌ | recordings/p2-request-count-zero-recording, test_p2_request_count_zero |
| **网络断言 × popup（跨页请求归属）** | ✅（2026-10-30 P2.7/P2.8） | ✅ headed | ✅ | ✅ | ✅（负：opener 违规双链失败 actual=1；正向：期望篡改 500 双链失败 + 无请求不假通过 + 方法隔离） | ❌ | recordings/p2-popup-network-recording, test_p2_popup_network |
| **网络断言「最近请求」菜单交互** | ✅（P2.2 轮） | ✅ headed | n/a | n/a | ✅ | ❌ | test_assert_menu（真实菜单 iframe 交互；P2.6 轮补 count=0 保存/稳定窗 2 条回归） |
| iframe / frameChain | ✅ | ✅ | ✅ | ✅ | ⚠️ | ❌ | fixture_drive, test_record_replayability |
| popup / 新标签页 pageId 因果 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | test_acceptance_p1_scenarios, acd9b8c |
| afterStepId 因果顺序 | ✅ | ✅（本轮网络断言实测） | ✅ | ✅ | ✅ | ❌ | test_review_p1_completion + 本轮 |
| 同主机不同端口 / 跨 origin | ✅（2026-10-30 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 expected 双链失败 + 污染 pageOrigin 状态错位暴露） | ❌ | recordings/p2-cross-origin-recording, test_cross_origin |
| Cookie/localStorage/sessionStorage 恢复 | ⚠️ | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | rec_session 快照有测试（test_session_restore 属并行会话） |
| **页面 reload/前进后退导航事件** | ✅（2026-10-30 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 + 删导航步骤 + 路线断言突变） | ❌ | recordings/p2-nav-events-recording, test_nav_events |
| **页面关闭（tab/popup close + 关后归属 + page_count）** | ✅（2026-10-30 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 1→2 双链失败 + 删 page_close 步骤状态错位） | ❌ | recordings/p2-page-close-recording, test_page_close |
| 网络失败 / 读取异常非通过 | ✅ | n/a | ✅ | ✅ | ✅ | ❌ | test_review_trace_edges, test_failure_categories |
| Windows headed Chrome 启动/录制/回放 | ⚠️ | ✅（旧轮） | ✅ | ✅ | ⚠️ | ⚠️ | test_windows_compat, test_windows_smoke |
| 短命标签页闪开闪关 | ✅ | ✅（旧轮） | ✅ | ✅ | ⚠️ | ✅ | aa941af（Windows storage restore tab 修复） |
| **回放节奏 / 导航等待（慢导航 >5s 双链同判）** | ✅（2026-10-31 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 expected 双链失败 + 禁用修复重放真实录制「路径不符」复现） | ❌ | recordings/p2-nav-pacing-recording, test_p2_nav_pacing |
| **慢写响应 5–30s（响应等待预算双链同判）** | ✅（2026-10-31 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 expected 双链失败 + 禁用修复重放真实录制 TimeoutError 复现） | ❌ | recordings/p2-slow-response-recording, test_p2_slow_response |
| **count=0 锚在 navigate 步骤（生成门对称 + afterStepId 绑定）** | ✅（2026-10-31 本轮） | ✅ headed | ✅ | ✅ | ✅（fire/污染口径双链失败 actual=1 + 禁用修复重生成 SpecGenerationError 复现） | ❌ | recordings/p2-navigate-anchored-recording, test_p2_navigate_anchored_net |

## 轮次记录

### 2026-10-31 — P2.12 count=0 锚在 navigate 步骤：生成门对称 + 注入器 lastStepId 缺陷（真实录制连环暴露三缺陷）

- 提交：`822f73c`（修复，分支 maa-dp；作业区
  /Users/edr-test/ai-projects/edr-cloud-recorder-p2）。
- 选择缺口：P2.11 剩余风险 #2「两链生成门不对称」——调研证实该风险成立：
  count=0 断言锚在 reload 步骤上时，generate_spec 的 navigate 分支只 emit
  `page.reload()`、从不领走 `_network_by_action` 里挂在自己名下的断言 →
  leftover 检查拒发整份 spec；generate_trace 的 Navigate 是独立节点、
  count=0 观察者预扫照常 → 接受。同一录制一拒一收。
- 真实 headed 录制连环暴露**三个缺陷**（单测调研只找到第一个；真实产物
  的价值再次兑现）：
  1. **生成门不对称**（调研预期）：spec 链 navigate 分支不领网络断言 →
     拒发整份 spec；trace 链接受。修复：navigate 分支走 `emit_action`
     领走 `_network_by_action.pop(s["id"])`，观察窗/等待块生成与 DOM
     动作同一管线（reload/go_back 表达式落在 with 块内层，pre 观察者
     创建在动作行之前 —— 窗起点=动作前，与 click 锚语义同构）。
  2. **注入器 lastStepId 漏更新**（真实录制暴露）：`recorder-inject.mjs`
     的 navigate 步骤上报块从不更新 `lastStepId`（普通步骤路径在
     pushStepRecord 里做）—— reload 后立刻下断言时 `afterStepId` 丢失
     （实测：断言 afterStepId=null），generate_spec 拒发整份录制
     （afterStepId 缺失 → 无法确定观察起点，P0 红线保住了原子性：不落
     半套产物）。修复：navigate 上报块补 `lastStepId = step.id`。
  3. **trace 生成门收 afterStepId 缺失的网络断言**（真实录制暴露的
     不对称另一半）：generate_trace 预分组要求 `afterStepId` 真值，缺失
     的断言静默不进分组、不报错 —— 回放链 count=0 预扫描
     `src_to_node[None]` 直接 KeyError，status/body 断言被静默丢弃。
     修复：缺失 afterStepId 的网络断言进 `orphan_network`，与
     leftover 检查合并统一 SpecGenerationError 拒发 —— 与 spec 链
     对称。
- 失败测试先行：test/test_p2_navigate_anchored_net.py（4 条，修复前红）：
  - T1 同一录制（click → navigate(reload) → count=0 锚在 nav 上）两链都
    必须接受（修复前 spec 链 SpecGenerationError）；
  - T2 生成链真实可执行：观察者定义在 reload 之前、稳定窗轮询在 reload
    之后（真 Chromium 静默环境正向通过）；
  - T3 双链同判：正向（静默环境）双链通过；反向（页面每次加载发 POST）
    双链都失败且失败信息指回断言（请求数 actual=1）；
  - T4 afterStepId 缺失的网络断言：两链都必须拒发（修复前 trace 链
    DID NOT RAISE）。
- 反向验证（三类，全部复现）：
  a) 单测级：禁用修复 1（emit_action 传 None）→ T1/T2/T3 红；
     禁用修复 3（orphan 判定收窄回「只有 afterStepId 悬空才拒」）→ T4 红；
     均为 Edit-only + SHA-verified 恢复。
  b) 真实录制验收脚本内：禁用修复 1 重新生成 spec → SpecGenerationError
     复现（"因果动作步骤 '…' 不在生成后的步骤序列里"）—— 证明真实录制
     产物确实暴露了被修复的缺陷。
  c) 防测试空转：fire 环境（reload 后新文档自己发 POST）双链失败
     actual=1；污染口径（reqUrl 指向真实存在的 GET /api/ping）双链失败
     actual=1。
- 真实 headed 录制闭环（scripts/record_p2_navigate_anchored_acceptance.py →
  recordings/p2-navigate-anchored-recording/）：headed Chrome 真录
  （evaluate 真发 POST /api/save 进菜单清单 → page.reload() 产出
  navigate/reload 步骤 → 菜单「最近请求」范围 count=0 绑定 POST → 保存），
  afterStepId 实锚在 reload 步骤上（6008yf-2 after 6008yf-1）；record.py
  管线原子落盘三产物；录制核验（reload 步骤存在、断言锚 reload、
  三产物断言 id 一致 rec==spec==trace == ['6008yf-2']、trace validator
  ok:true 2 节点 ready）通过；生成链正向 PASS、trace 链正向 PASS、
  fire 环境双链失败、污染口径双链失败、禁用修复重生成拒发 ——
  ALL GATES PASS。
- 验收脚本自身的教训（记给后续轮次）：
  a) **setTimeout 定时器不能当 reload 场景的负向触发器** —— reload 会把
     挂起的定时器一起抹掉，点击发的迟到 POST 在 reload 后根本不存在，
     负断言假绿（T3 第一版实测 DID NOT RAISE）。触发器必须是「reload 后
     新文档自己发出的请求」（load 脚本）——这也正是这个场景的真实语义：
     reload 窗抓的就是新文档的请求，观察窗起点在 reload 之前，
     reload 前后两个文档的请求都在窗内。
  b) 单 handler 的 do_GET 必须按路径 dispatch（先 return 再发页面），
     顺序写反会让 ping 响应叠加两个 HTTP 头（污染口径反向假绿）。
  c) 正/反向环境的 load 脚本差异要用同一个条件表达式生成，分开赋值会
     在改一处时漏另一处（第二版实测正向链误发 POST 失败）。
- 门禁：定向 test_p2_navigate_anchored_net 4 passed（修复后）；p2 家族
  37 passed；test_trace_replay + test_review_generation_consistency
  164 passed；全量 pytest 918 passed / 2 skipped / 0 failed；check_visual
  通过；git diff --check 通过；trace validator ok:true（2 节点 ready）；
  三产物断言一致性 PASS。
- 设计文档同步：common-web-assertion-design.md §5 afterStepId 字段规则
  补「合法因果动作包括页面级步骤（navigate/page_close）；两链对称义务；
  afterStepId 缺失两链都必须拒发」。
- Windows headed：本机 macOS，未验证（记入剩余风险，**未宣称最终完成**）。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（连续多轮遗留，最高优先）；
  2. page_close 锚定的网络断言与 navigate 同族（ClosePage 分支同样不领
     `_network_by_action`）—— 本轮只修了 navigate；页面被关后观察窗语义
     （窗到 close 为止还是到断言节点为止）需要专项定义后闭环；
  3. 30s 响应预算与 20s 导航预算仍是常量而非从录制数据推导（P2.10/P2.11
     遗留）；>30s 响应 / >20s 导航双链同败（同判但都红）；
  4. 非必发（读请求）等待 `min(timeout_ms, 2000)` 与慢读请求的交互
     （P2.11 遗留）：双链 responses 记录形态不同，未专项验证。

### 2026-10-31 — P2.11 慢写响应（5–30s）双链同判（trace 链响应等待错用单步定位预算）

- 提交：`5facd20`（分支 maa-dp；作业区 /Users/edr-test/ai-projects/edr-cloud-recorder-p2）。
- 选择缺口：P2.10 风险 #3「慢导航 × 网络断言共存的观察窗起点」——
  调研结论：count=0 观察窗两链都在因果动作前建观察者、动作后轮询
  （菜单 afterStepId 恒绑同文档 lastStepId），窗语义一致，**该风险不成立**；
  真正的缝隙在**响应等待预算**（P2.8 风险 #2 的姊妹形态）：
  - 生成链：`context.expect_event("request", ...)` 用 Playwright 缺省
    30s；抓到后 `req.response()` 是无 timeout 参数的 channel 调用，
    同样没有 5s 上限；
  - trace 链：`_response_waiter(page, pred, timeout_ms)` 把单步定位预算
    （缺省 5s）同时用在响应等待上。
- 失败测试先行：test/test_p2_slow_response.py（2 条，修复前**双链分歧**红：
  同一录制 pytest 链 PASS（等满 7s）、trace 链 5.0s
  `TimeoutError: Timeout 5000ms exceeded while waiting for event "response"`
  failureCategory=automation，durationMs≈5011 实测）。
  - T1 慢写响应（无用户断言）：点 #save 发 POST /api/save，服务器延迟
    7s 回 201 —— 自动写断言（status==201 + assert_subset 请求体）落在
    等待块里，生成链照常等到、trace 链提前放弃。
  - T2 慢响应 + 用户网络断言：同上 + 菜单录 status=201（afterStepId=
    save 点击）。用户 expected 合并进 verification.responses 后，trace
    链在响应等待上校验 —— 同一预算缝隙。
- 根因与最小修复（scripts/replay_trace.py）：「等写请求的响应」是
  **网络等待**不是元素定位，预算必须与生成链同源。新增
  `NET_RESPONSE_WAIT_MS = 30_000`（与 Playwright 缺省一致），
  `_perform` 的必发响应等待改用它；非必发（读请求）的
  `min(timeout_ms, 2000)` 上限不变（语义是「状态已变就不重发」，不是
  慢响应）；元素定位仍按 timeout_ms。
- 反向验证：
  a) **禁用修复**（NET_RESPONSE_WAIT_MS → 5_000，等价回旧预算）跑 2 条
     单测 → 双双以 TimeoutError 复现（红灯形态与修复前一致，
     SHA-verified 恢复）；注：设 0 不是禁用 —— Playwright 把 0 当
     「无超时」，第一版反向假绿，已换正确突变。
  b) 真实录制验收脚本内第 4 道反向：用真实录制产物 trace 重放、
     禁用修复 → trace 链以 TimeoutError 失败 —— 证明真实录制确实暴露
     了被修复的缺陷；
  c) 篡改 expected（用户断言 201→500；T1 篡改请求体 p:1→p:2）→ 双链
     都失败（防测试空转）。篡改位教训：**状态码不能当自动写断言的
     篡改位** —— generate_spec 对 ≥400 的响应不生成自动写断言（只留
     注释），改 net 事件状态只会让断言消失（DID NOT RAISE），改请求体
     才是真篡改。
- 真实 headed 录制闭环（scripts/record_p2_slow_response_acceptance.py →
  recordings/p2-slow-response-recording/）：headed Chrome 真录
  （点 #save 真发 POST → 等 7s 慢 201 回来 → 菜单「最近请求」绑定该
  POST → status=201，net-status 亲手填即显式确认），record.py 管线原子
  落盘三产物；录制核验（click css 兜底 button#save、net req→res 间隔
  7.0s ≥ 6s 下限、断言 afterStepId=点击、expectedConfirmed）通过；
  生成链正向 PASS（等满 7s）、trace 链正向 PASS（7.1s）、篡改双链失败、
  禁用修复重放 TimeoutError 复现 —— ALL GATES PASS。三产物断言 id
  一致（rec==spec==trace == 'b0luxc-2'）；trace validator ok:true
  （2 节点 ready）。
- 验收脚本自身的教训（记给后续轮次）：
  a) 断言菜单驱动：先 `#sc` 选 network 范围，`#netReq` 才显示并填充；
     绑定请求用 option 的 value（JSON.stringify({m,u})）逐个匹配，别按
     index 选（占位项会漂）；
  b) net-status 类型**没有**确认框：#confirm 只在 string 类型下显示，
     非 string/bool 类型「用户亲手填/选即显式确认」（injector
     confirmed=true）——对它 check() 会 30s 超时（与 P2.10 轮
     「#es 先于 #confirm」教训同源但相反：这次是**不该**碰 #confirm）；
  c) 录制器对 getByRole 优先：点 #save 落盘 `getByRole("button", …)`，
     产物核验按 css 兜底字段（`css: 'button#save'`）匹配，不按 sel。
- 门禁：定向 test_p2_slow_response 2 passed（修复后）；相邻回归
  test_trace_replay 45 passed（含 _NetworkPage 响应等待预算钉子更新：
  `assert timeout == NET_RESPONSE_WAIT_MS`）+ p2 全家族 48 passed；
  check_visual 通过；git diff --check 通过；trace validator ok:true
  （2 节点 ready）；三产物断言一致性 PASS。
- Windows headed：本机 macOS，未验证（记入剩余风险，**未宣称最终完成**）。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（连续多轮遗留，最高优先）；
  2. 锚在 reload/navigate 步骤上的 count=0 网络断言：generate_spec 的
     navigate 分支不 emit 动作等待块 → 断言走「因果动作不在生成序列」
     拒发整份 spec；generate_trace 却接受（Navigate 是独立节点）——
     **两链生成门不对称**（一拒一收，同录制不同判）未专项闭环；
  3. 30s 响应预算与 20s 导航预算都是常量而非从录制数据推导（同 P2.10
     风险 #2）；>30s 响应双链同败（同判但都红）；
  4. 非必发（读请求）等待 `min(timeout_ms, 2000)` 与慢读请求的交互：
     读请求确实 3s 才回且状态真的变化时，trace 链会记「未出现」警告
     而生成链（无读断言）不受影响 —— 警告不改变 success，双链仍同判，
     但 responses 记录形态不同，未专项验证。

### 2026-10-31 — P2.10 回放节奏 / 慢导航（>5s）双链同判（trace 链导航等待预算错用单步定位预算）

- 提交：`e68edbb`（分支 maa-dp；作业区 /Users/edr-test/ai-projects/edr-cloud-recorder-p2）。
- 选择缺口：矩阵唯一整行空白「回放节奏 / 导航等待」（P2.8 风险 #2）——
  录制↔回放节奏差无专项场景，加载缓慢 / 导航迟到形态未钉死。
- 失败测试先行：test/test_p2_nav_pacing.py（2 条，修复前**双链分歧**红：
  同一录制 pytest 链 PASS、trace 链 FAIL）。
  - T1 同页慢导航：fill → 点按钮（setTimeout 300ms 后跳 /slow-next，
    服务器延迟 7s）→ /slow-next 上 fill + value 断言。生成链
    `wait_for_recorded_url` 20s 预算等到；trace 链 `_page_for_node` 的路线
    等待用**单步定位预算**（timeout_ms=5s）——导航 ~7.3s 才 commit，
    5s 放弃 → 报「页面路径不符: 当前 /，轨迹此步应在 /slow-next」
    （failureCategory=infrastructure，durationMs≈5016 实测）。
  - T2 慢 popup：target=_blank 开 /slow-popup（延迟 7s）。生成链
    `recorded_page_for` 20s 缺省等到；trace 链候选扫描同样 5s 放弃 →
    同形态红。
- 根因与最小修复（scripts/replay_trace.py）：「等页面到达录制路线」是
  **导航**不是元素定位，预算必须与生成链同源。新增
  `NAV_ROUTE_WAIT_MS = 20_000`（与 rec_helpers.wait_for_recorded_url /
  recorded_page_for 缺省一致），`_page_for_node` 两条路线等待路径
  （known-page `_wait_page_matches`、popup 候选扫描 deadline）改用它；
  元素定位仍按 timeout_ms 不变。
- 反向验证：
  a) **禁用修复**（NAV_ROUTE_WAIT_MS → 0）跑 2 条单测 → 双双以「双链分歧 /
     页面路径不符」复现（单测红灯形态与修复前一致，SHA-verified 恢复）；
  b) 真实录制验收脚本内第 4 道反向：用真实录制产物 trace 重放、
     禁用修复 → trace 链以「路径不符」失败 —— 证明真实录制确实暴露了
     被修复的缺陷，不是测试构造的假场景；
  c) 篡改 expected（world → tampered）→ 双链都失败（防测试空转）。
- 真实 headed 录制闭环（scripts/record_p2_nav_pacing_acceptance.py →
  recordings/p2-nav-pacing-recording/）：headed Chrome 真录慢导航流
  （fill hello / → 点慢按钮 /slow-next 7s → fill world + value 断言
  expectedConfirmed=true），record.py 管线原子落盘三产物；录制核验
  （慢导航因果：hello 点击落 /、导航后步骤落 /slow-next）通过；
  生成链正向 PASS（wait_for_recorded_url 等到）、trace 链正向 PASS、
  篡改双链失败、禁用修复重放「路径不符」复现 —— ALL GATES PASS。
- 门禁：定向 test_p2_nav_pacing 2 passed（修复后）+ 相邻回归
  test_nav_events + test_cross_origin + test_p2_page_network +
  test_page_close 24 passed；test_trace_replay + popup-network +
  trace-edges 99 passed；replay-check + generated_execution +
  generation_consistency 190 passed；check_visual 通过；git diff --check
  通过；skill validator ok:true（6 节点 ready）；**全量 pytest
  912 passed, 2 skipped, 0 failed**（含并行会话同轮落盘的
  orchestrate 基线修复，双会话并发跑全量互证）。
- Windows headed：本机 macOS，未验证（记入剩余风险，**未宣称最终完成**）。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（连续多轮遗留，最高优先）；
  2. 20s 导航预算是常量而非从录制数据推导 —— 录制时导航实际耗时 >20s
     的极端站点仍会双链同败（同判但都红）；若要支持需录制侧记录导航
     实际耗时并落进 trace（暂不做，先钉住同判性）；
  3. 慢导航 × 网络断言共存的观察窗起点未专项验证（观察者建在动作前，
     慢导航下动作前时长被拉长，窗语义可能被拉宽）。

### 2026-10-31 — 全量门禁 0-failed 首阻：test_orchestrate 身份匹配「基线」判定与修复（测试夹具/脱敏问题，非产品缺陷）

- 提交：`9fe1496`（分支 maa-dp；作业区 /Users/edr-test/ai-projects/edr-cloud-recorder-p2）。
- 选择缺口：全量 pytest 唯一失败
  `test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive`
  —— 多轮被当「既有基线」跳过，本轮按指令不再跳过、单独稳定复现并读真实契约。
- 契约复核（orchestrate/endpoint.py）：`assert_matches` 用 `_valid_ipv4s`
  只认**合法 IPv4**（正则提取 + ipaddress.IPv4Address 校验）；匹配是 IP **精确
  相等**（set 成员），hostname 用词边界正则 —— 都不是 startswith 子串匹配。
  端侧 `identity()` 也只用 IPv4 正则取回 ip。
- 根因判定：**测试夹具 / 环境脱敏问题，非产品缺陷**。旧夹具（自 633c2db 起）
  把 IP 写成了脱敏占位串 `REDACTED-INTERNAL-IP-1` —— 它不是合法 IPv4，
  `_valid_ipv4s` 正确返回空集 → 「精确 IP 匹配」断言永远无法满足（假红）。
  产品行为（对非 IPv4 当 IP 拒绝）是正确的；与仓库 redaction 纪律
  （test_windows_smoke 用 `REDACTED-INTERNAL-IP-` 当 banned 标记）不冲突。
- 反例与修复（测试先行，不改产品代码）：
  - 用真实 IPv4 重写原测试（保留意图：`192.168.1.5` 不得匹配前缀
    `192.168.1.50`；精确一致必须匹配）；
  - 新增边界覆盖 `test_identity_match_ip_and_hostname_boundaries`：
    相似前缀更长 octet、IPv6 期望串不触发 v4 匹配、hostname 词边界（全词匹配、
    前缀 WIN-A ⊄ WIN-ABC 拒）、端口 `192.168.1.5:8080` 精确 IP 仍匹配；
  - 新增 `test_identity_match_refuses_non_ipv4_placeholder`：把旧夹具的假想行为
    （占位串当 IP 能匹配）钉成「必须拒绝」，防未来再把占位串当 IP。
- 验证（回填）：test_orchestrate **13 passed**；check_visual 通过；全量 pytest
  **912 passed / 2 skipped / 0 failed**（长期「基线失败」已消除）。
- Windows headed：本机 macOS，未验证 —— **单列为最终阻塞**，不伪造成过。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（最终阻塞）；
  2. 录制↔回放节奏差专项、request_count+自动写断言共存 trace 语义未专项；
  3. `identity()` 目前只认 IPv4，IPv6-only 端侧主机身份无法匹配（记录，未做）。

### 2026-10-30 — P2.9 网络断言 × popup 的**真实 headed 录制**证据（status/body 正向绑定）

- 提交：`2970665`（分支 maa-dp；作业区 /Users/edr-test/ai-projects/edr-cloud-recorder-p2）。
- 选择缺口：P2.8 剩余风险 #4 —— 正向网络断言的**真实 headed 录制**证据未补
  （P2.8 为手构 steps + 双链真实 Chromium）。本轮用 record.py 真录补足。
- 证据：scripts/record_p2_popup_positive_acceptance.py → 产物
  recordings/p2-popup-positive-recording/（recording.json / trace.json /
  test_p2_popup_positive_recording.py；recordings/ 按仓库策略 gitignore，
  由验收脚本复现）。驱动 on_ready：opener 点 #open（target=_blank）开 popup →
  popup 加载即发 POST /api/save（body `{"p":"1"}`，201 `{}`）→ **opener 页**
  开断言菜单选「最近请求」绑定这条 popup-load 的 POST，逐个加 status=201 /
  request_body={"p":"1"} / response_body={} → 确认。
- 录制产物核验：三条网络断言均 pageId=opener、afterStepId=opener 的 #open 点击
  （因果动作；popup 页无步骤，afterStepId 走 lastStepId=opener 最后一步）、
  reqMethod=POST、reqUrl 含 /api/save、expectedConfirmed 非 false；
  三产物断言 id/数量一致（spec_ids == rec_ids == tr_ids）；因果序
  #open.t < 请求.t < 断言.t。
- 门禁（脚本内 ALL GATES PASS）：
  - 正向（与录制一致，popup 真发 POST）：生成链 PASS + 回放链 PASS；
  - 篡改 status 201→500 → 两链都失败；篡改 request_body {p:1}→{p:2} → 两链都失败；
  - 移除反向（quiet 环境 popup 不发请求）→ 两链都失败（读取异常不假通过）。
  定向 test_p2_popup_network 10 passed（含 P2.8 5 条 + P2.7 count=0）；check_visual
  通过；全量 pytest **907 passed / 2 skipped / 1 failed** —— 唯一失败为基线
  test_orchestrate（干净 maa-dp HEAD 已有，非本轮引入；本轮仅加验收脚本，产品
  代码零改动）。
- Windows headed：本机 macOS，未验证（记入剩余风险，**不宣称最终完成**）。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（连续多轮遗留）；
  2. 录制↔回放节奏差（setTimeout 迟到的请求落不落观察窗）无专项场景；
  3. request_count 与自动写断言同动作共存的 trace 链语义未对齐验证；
  4. 网络断言三契约合一（同一请求上 status+body 在一个断言节点表达）未专项。

### 2026-10-30 — P2.8 网络断言 × popup 的**正向**配对（status/body 绑 popup 发出的请求，双链假红）

- 提交：`8653629`（分支 maa-dp；作业区 /Users/edr-test/ai-projects/edr-cloud-recorder-p2）。
- 选择缺口：P2.7 剩余风险 #2 —— 正向网络断言（status/body 绑 popup 发出的
  请求）在生成链的 `expect_request` 是 page 级；popup 发的请求、断言锚在 opener
  动作时 `expect_request` 等不到（**正向假红**：请求明明 201，等待却超时）。
  P2.7 只闭环了负断言（count=0），正向配对归属未动。
- 失败测试先行：test/test_p2_popup_network.py::TestPopupPositiveNetworkAssert
  （5 条，实现前 status 双链假红：`TimeoutError waiting for event "request"`）。
- 反例 fixture：opener 点 #openload（target=_blank）打开「加载即发 POST
  /api/save」的 popup —— Chrome 88+ 对 target=_blank 隐式 rel=noopener，
  popup 自身 fetch，opener 页的 `page.expect_request` 永远看不到它；断言
  scope=network status=201 anchored on opener 动作。无测试单链不覆盖。
- 根因与最小修复（观察口径对齐录制侧 context 级）：
  1. **generate_spec._emit_action**：`page.expect_request(...)` →
     `page.context.expect_event("request", nth_request(...))` —— 等待跨所有
     页面，popup 发出的请求照常被捕获（录制侧 record.py on_request 本就在
     **browser context 级**抓请求）。
  2. **replay_trace._perform**：`page.expect_response(...)` → `_response_waiter(
     page, ...)` —— 有真实 context（`hasattr(ctx,"expect_event")`）时用
     context 级（跨页面抓 popup 响应，保证与生成链同判）；历史 mock 测试页
     （_SilentPage/_NetworkPage 无 .context 或 `page.context=Context()` 假对象）
     退回 `page.expect_response`，保住单测（P2.7 `_assert_node_page` 教训延伸）。
- 反向验证：
  a) **篡改 expected 201→500** → 两条链都失败（context 观察不因放宽而假绿）；
  b) **无请求事件**（net=[]，popup 不发）→ 两条链都失败（读取异常不假通过；
     生成端对不可绑定的正向断言诚实拒发 = 合法非通过，测试辅助把
     SpecGenerationError 转成双失败而非误判错误）；
  c) **修复前**（page 级 expect_request）→ status 正向假红 TimeoutError（已实测）。
- 门禁：定向 test_p2_popup_network 全绿（含 P2.7 count=0 5 条回归）；
  相邻回归：test_trace_replay + popup-network 55 passed、generation/menu/network
  217 passed、test_verify 116 passed；check_visual 通过；skill validator 通过。
  全量 pytest 906 passed / 2 skipped / 2 failed —— 两处失败：(a) test_verify 的
  写请求结构断言仍查 `page\.expect_request`（本轮升级为 context 级 expect_event
  的回归，已在本轮修复后定向通过）；(b) 基线
  test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive
  （干净 maa-dp HEAD 已有，非本轮引入）。故最终仅剩基线失败，
  **不宣称最终完成**（orchestrate 基线 + Windows headed 未验收两道关卡未过）。
- Windows headed：本机 macOS，未验证（记入剩余风险，**未宣称最终完成**）。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（连续多轮遗留）；
  2. 录制↔回放节奏差（setTimeout 迟到的请求落不落观察窗）无专项场景；
  3. request_count 与自动写断言同动作共存的 trace 链语义未对齐验证；
  4. 正向网络断言的**真实 headed 录制**（record.py 真录 popup → 断言 → 回放）
     证据未补（本轮为手构 steps + 双链真实 Chromium 运行）。

### 2026-10-30 — P2.7 网络断言 × popup（跨页请求归属，三缺陷：观察口径、产码缩进、回放观察窗锚定）

- 提交：`4c66a7c`（分支 maa-dp；验收录制产物
  recordings/p2-popup-network-recording/ 由
  scripts/record_p2_popup_network_acceptance.py 在该提交工作树上复现）。
- 起点基线：`maa-dp` @ 23b456c（edr-cloud-recorder-p2 worktree）。
- 选择缺口：矩阵「网络断言 × popup」——连续五轮遗留（P2.2 起就在候选清单）。
- 失败测试先行：test/test_p2_popup_network.py（5 条，实现前 2 条失败——
  生成链与 trace 链对 popup/opener 发出的违规请求双链假绿 DID NOT RAISE）。
- 根因与修复（本轮 3 个独立缺陷）：
  1. **D1 观察口径与录制侧不一致**：录制侧（record.py on_request）在
     browser context 级抓请求，popup 里发的请求照常进菜单、照常可绑定
     断言；但 NetRequestWatcher 只 page.on("request") 单页监听——
     「不应发请求」的「不」被回放端观察窗口缩小，观察者页面之外的请求
     永远数不到，负断言假绿。修复：NetRequestWatcher 升级
     context.on("request")（probe 证实 ctx 级事件能收到 popup 请求）。
  2. **D2 同动作共存自动写断言 + count=0 产码语法错误**（P2.6 风险清单
     第 5 条专项覆盖）：_emit_network_assert 的观察者创建行固定 4 空格，
     落进 expect_request 的 with 块后缩进少一层——生成的 pytest 一执行
     IndentationError。修复：pre 行缩进跟 with 内层（inner 参数）走。
  3. **D3 trace 链观察窗锚定错位**：replay_trace 在断言节点本身才建
     观察者——因果动作（afterStepId 指向的节点）自己发出的请求在断言
     节点之前发生，已经漏掉。修复：主循环预扫描 count=0 断言节点的
     afterStepId → 触发节点映射，在触发节点执行前建观察者
     （validate_trace 已保证因果序），断言节点复用。
- 真实录制闭环（scripts/record_p2_popup_network_acceptance.py →
  recordings/p2-popup-network-recording/，headed Chrome 真录）：
  opener 点 write（真发 POST /api/save）→ 开 popup（target=_blank）→
  popup 点 quiet（静默动作=观察窗因果锚）→ **在 popup 页**开菜单选
  count=0 绑定 POST → 确认。断言步骤落在 popup（pageId=page-0002、
  afterStepId=popup 的 quiet 点击）。四环境验收：
  A 正向双链 PASS；B 口径隔离（opener 发 GET ping，POST 口径不计）双链
  PASS；C 缺陷反向（opener 发 POST /api/save）双链失败 actual=1；
  D 污染口径（reqUrl 篡改成真会发的 GET /api/ping）双链失败 actual=1。
- 验收脚本自身的教训（记给后续轮次）：
  a) **Chrome 88+ 对 target=_blank 隐式 rel=noopener**——popup 里
     window.opener 恒为 null，opener.fetch 静默失败，反向永远打不响。
     跨页触发要经服务端信号（popup fetch /signal?set=1，opener 轮询
     拿信号自己发违规请求）；
  b) 信号轮询间隔必须 < 负断言稳定窗（300ms），否则信号落在稳定窗
     之外，反向假绿（200ms 实测稳定）；
  c) 回放链生成 spec 时必须把录制 net 事件一并 retarget 传进去——
     否则自动写断言等待块被丢，#write 的 POST 在回放节奏下迟到、
     落进观察窗，正向假红（录制↔回放节奏差的真实形态）；
  d) 录制环境不要掺轮询噪音（/signal 每 500ms 一条会把菜单最近请求
     清单冲掉）——录制环境关轮询，只在回放反向环境开。
- 反向验证（3 组突变，全部先失败后恢复，恢复后 SHA 校验字节一致）：
  1. NetRequestWatcher 去掉 context 挂接 → 双链失败测试回归（2 条）；
  2. generate_spec 缩进修复回滚 → 生成的 spec compile 抛
     IndentationError（与原始缺陷同形态）；
  3. replay_trace 观察者锚定回滚 → trace 链假绿（两链不同判）。
- 门禁：定向 test_p2_popup_network 5 passed；相邻回归 12 文件
  212 passed；check_visual 通过；git diff --check 通过；
  trace validate ok（5 节点 ready）；全量 pytest 902 passed / 2 skipped /
  1 failed——唯一失败仍是基线
  test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive
  （干净 maa-dp HEAD 已有，非本轮引入）。
- Windows headed：本机 macOS，未验证（记入剩余风险）。
- 剩余风险（下一轮候选）：
  1. Windows headed 未验证（连续七轮遗留）；
  2. 正向网络断言（status/body 绑 popup 发出的请求）在生成链的
     expect_request 也是 page 级——popup 发的请求、断言锚在 opener 动作
     时 expect_request 等不到（正向假红形态）；本次只闭环了负断言
     （count=0 观察口径已修），正向配对归属未动；
  3. 录制↔回放节奏差（setTimeout 迟到的请求落不落窗）无专项场景；
  4. request_count 与自动写断言同动作共存的 trace 链表现（D2 只修了
     生成链产码，trace 链 verifier 不受 with 块影响但语义未对齐验证）。

### 2026-10-30 — P2.6 request_count=0 有界观察窗真实闭环（五缺陷：产码 NameError、泵饥饿、trace 静默丢、菜单两处、稳定窗丢失）

- 提交：`fc93c7f`（分支 maa-dp；验收录制产物
  recordings/p2-request-count-zero-recording/ 由
  scripts/record_p2_request_count_zero_acceptance.py 在该提交工作树上复现）。
- 起点基线：`maa-dp` @ 1feedaa（edr-cloud-recorder-p2 worktree，干净起步）。
  主 checkout `codex/fix-windows-recorder-storage-restore` 的并行未提交修改未触碰。
- 选择缺口：矩阵「request_count=0 有界观察窗」——连续四轮只有单元证据
  （test_p2_page_network 用假 watcher 验证窗口过滤，从不真跑浏览器）。
- 失败测试先行：test/test_p2_request_count_zero.py（6 条，实现前 4 失败；
  另 2 条记录缺陷形态，修复后继续有效）。
- 根因与修复（本轮 5 个独立缺陷）：
  1. **D1 生成链产码不可执行**：_emit_network_assert 发出
     `request_count_zero(_net_watcher, ...)`，但 `_net_watcher` 从未定义
     （注释谎称「由模板提供」）、HEADER 未导入 request_count_zero、调用缺
     观察窗参数 —— 生成的 pytest 一执行就 NameError，负断言从未真正跑过。
     修复：HEADER 导入 + 每断言发 `_net_watcher<id> = NetRequestWatcher(page,
     method=...)`（动作前）+ `request_count_zero(...)`（动作后）+ rec-assert
     标记（一致性门禁从此能查到这条断言）。
  2. **D2 事件泵饥饿（本轮最重要发现）**：sync Playwright 的
     page.on("request") 回调只在事件循环被驱动时分发 —— time.sleep 轮询
     期间一条都不来（探针实测：延迟 fetch 触发后 600ms 内计数恒 0）。
     负断言「数不到」= 永远假绿。修复：新 helper
     rec_helpers.NetRequestWatcher.count() 里 page.wait_for_timeout(1) 驱动
     事件分发；观察窗起点 = watcher 创建时刻（动作前）。
  3. **D3 trace 链静默丢/反向等待**：_merge_user_network_expectation 没有
     request_count 分支 —— 要么被丢（一致性门禁必拒真实产物），要么落成
     「必有响应」等待（语义相反：正确应用回放失败、坏应用反而通过）。
     修复：request_count 不走 responses 合并；_assertion_spec 带 reqUrl/
     reqMethod 进 trace，replay_trace 的 network 分支真正执行有界观察
     （watcher + request_count_zero，稳定窗下限 300ms）。
  4. **D4 trace 侧孤儿因果静默丢**：afterStepId 指向被丢弃/不存在步骤的
     网络断言，generate_spec 拒发而 generate_trace 静默丢 —— 同一录制两链
     不同判。修复：generate_trace 循环后对残留 steps_by_action 拒发
     （SpecGenerationError，与 spec 侧同一红线）。
  5. **D5 菜单两处（只有真实录制闭环才暴露）**：a) net-zero 无期望值但
     「Expected 为空」校验只排除 bool/is_blank —— count=0 断言保存恒禁用，
     根本录不下来；b) 负断言稳定窗判定漏 request_count（只认
     `type==='count' && expected===0`）—— 记成 stableMs=0，回放一进窗读到 0
     就放行，迟到的请求永远数不到（实测：回放进程在 fetch 发出前就跑完）。
     修复：injector 两个判定都补上；generate_spec/replay_trace 对
     request_count 强制 stableMs>=300 下限（老录制兼容）。
- 真实录制闭环（scripts/record_p2_request_count_zero_acceptance.py →
  recordings/p2-request-count-zero-recording/，headed Chrome 真录）：
  点 write（真发 POST /api/save）→ 点 net zero check（观察窗因果动作）→
  菜单选最近请求 + 不应发请求(count=0)，绑定 POST → 确认。产物：recording.json
  + trace.json[ready,3 节点] + test_*.py + assets/step-*.png；三产物断言
  一致（rec-assert: <id> 双链同序）。正向：pytest 链 PASS + trace 链 PASS。
  反向 1：服务端换成「check 也发请求」→ 双链失败，生成链报
  `请求数为 0 断言失败: 观察窗 内实际请求数 actual=1（期望 0）`。
  反向 2：篡改 reqUrl 口径（指向真实会发的 GET /api/ping）→ 双链失败。
- 验收脚本自身的教训（记给后续轮次）：
  a) 想在页面里关掉请求（onclick=null）验证正向 —— 生成链第一行
  page.goto("/") 会重置补丁，「关请求」必须在服务端做；
  b) 换端口回放时 _retarget 只该动 pageOrigin/reqUrl，且必须先 retarget 再
  做数据污染 —— 顺序反了污染被 retarget 冲掉（反向假通过的又一形态）；
  c) 正向「通过」可能是 vacuous：观察窗断言立刻读 0 通过、浏览器随即关闭，
  迟到的 fetch 根本没机会发出 —— 没有稳定窗下限的正向证据一文不值。
- 反向验证（4 组突变，全部先失败后恢复，恢复后 SHA 校验字节一致）：
  1. generate_spec 只发调用不发观察者定义 → 4 条生成链测试失败
     （NameError 形态，与原始缺陷一致）；
  2. NetRequestWatcher.count() 去掉事件泵 → 迟到请求测试失败，失败形态
     是「DID NOT RAISE」（假绿被测试抓住）；
  3. replay_trace 的 request_count 分支禁用 → 双链同判测试失败（trace 链
     对真发请求假通过）；
  4. generate_trace 残留归属检查禁用 → 孤儿因果测试失败（DID NOT RAISE）。
- 门禁：定向 test_p2_request_count_zero 6 passed；相邻回归
  （network-scope/page-network/assert-menu/windows-compat/headless-smoke/
  generation-consistency/replayability）165 passed；check_visual 通过；
  git diff --check 通过；validate_trace ok（3 节点，ready）；全量 pytest
  895 passed / 2 skipped / 1 failed——唯一失败仍是基线
  test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive
  （干净 maa-dp HEAD 已有，非本轮引入）。
- Windows headed：本机 macOS，未验证（记入剩余风险）。
- 剩余风险（下一轮候选）：
  1. 网络断言 × popup 组合（连续四轮遗留）——popup 里发的请求归属 opener
     还是 popup、pageId 归属与 scope=network 的组合未覆盖；
  2. Windows headed 未验证（连续六轮遗留）；
  3. 同 origin 但 scheme 不同（http→https）的导航行为未专项覆盖；
  4. NetRequestWatcher 只统计 resource_type 由 page.on("request") 给出的
     事件（record.py 侧的 wanted() 还限 xhr/fetch）——回放链的观察口径与
     录制侧 api_filter 不一致时（录制时过滤了某 URL、断言又绑它）行为
     未验证；
  5. request_count 断言与自动写请求断言同动作共存时（等待块里既有
     expect_request 又有 watcher）的相互影响未专项覆盖。

### 2026-10-30 — P2.5 同主机不同端口 / 跨 origin 导航（双缺陷：生成侧等待 + 录制侧归属）

- 提交：`62e28cd`（分支 maa-dp；验收录制产物 recordings/p2-cross-origin-recording/
  由 scripts/record_p2_cross_origin_acceptance.py 在该提交工作树上复现）。
- 起点基线：`maa-dp` @ 16f2135（edr-cloud-recorder-p2 worktree，干净起步）。
  主 checkout `codex/fix-windows-recorder-storage-restore` 的并行未提交修改未触碰。
- 选择缺口：矩阵「同主机不同端口 / 跨 origin」——无真实录制、无反向验证的
  ⚠️ 行。跨 origin 同路径导航（端口 A / → 端口 B /）在步骤 url（纯路径）上
  完全不可见，唯一区分依据是 pageOrigin。
- 失败测试先行：test/test_cross_origin.py（4 条，实现前 3 条失败、1 条防放宽）。
- 根因与修复（两个独立缺陷，共 3 文件）：
  1. **D1 生成侧（generate_spec.py）**：「同页地址变化 → wait_for_recorded_url」
     的判定只比较 pathname+search+hash，不比较 pageOrigin —— 跨 origin 同路径
     导航被当成「没动」，生成的 pytest 不等导航就执行后续步骤，全部落在旧
     origin 页面上假绿、随后被跳转抹掉；trace 链按 pageOrigin 校验会等待，
     **双链对同一录制结论相反**。修复：last_top_urls 基准与比较都改用完整
     URL（expected_page_url = urljoin(pageOrigin, url)）。
  2. **D2 录制侧（record.py + recorder-inject.mjs）**：pageOrigin 由泵循环
     事后补写（resolve_page_identity 读 page.url）。泵间隙里页面已跨 origin
     导航时，旧 origin 上的步骤被记到新 origin 名下（与 P2.2 afterStepId
     同款「只有真实泵节奏才暴露」的缺陷；离线手拼步骤测不出）。修复：
     注入层 5 个步骤构造点在事件现场写 pageOrigin（location.origin），
     宿主泵结算只在步骤缺失该值时兜底。
- 确定性触发形态（关键经验）：`<a href>` 直跳不会暴露 D1 —— Playwright 的
  click 对链接导航有内建等待，导航在下一步前已完成。必须用 **setTimeout
  延迟跳转**（点击本身不导航）：fill/断言落在 A 上假绿，跳转后状态被抹掉。
- 反向验证（全部先失败后恢复，恢复后 cp 回字节一致）：
  1. generate_spec 判定精确回退（基准存路径 + 比较路径，即原始缺陷形态）
     → T1 + T3 失败（T3 报「fill 落在导航前被抹掉」）；
  2. record.py 宿主无条件覆盖注入层 pageOrigin → T2 失败（fill 的
     pageOrigin 变成导航后的 B）。
- 真实 headed 录制闭环：scripts/record_p2_cross_origin_acceptance.py →
  recordings/p2-cross-origin-recording/（recording.json + trace.json[ready,
  6 节点] + test_p2_cross_origin_recording.py + assets/step-*.png）。
  流程：A 填值 → delayed-go 按钮（800ms 后跳 B，B 慢 1s）→ B 填值 →
  B 上 value=world 断言（显式确认）。录制产物核验：A 的 fill pageOrigin=A、
  B 的 fill/断言 pageOrigin=B（D2 生效）；spec 含指向 B 的
  wait_for_recorded_url（D1 生效）。
  正向：pytest 链 PASS + trace 链 PASS。
  反向 1：篡改 expected（world→hello），双链失败且失败信息指向具体断言值。
  反向 2：把 B 的 fill pageOrigin 篡改成 A（模拟 D2 污染数据）→ 生成器把
  「B 上的步骤」转译成「等待导航回 A」→ 20s 超时，状态错位如实暴露
  （失败形态是 TimeoutError 而非 AssertionError —— 验收壳需接任意异常）。
- 门禁：定向 4 passed；相邻回归（nav/page-close/network-scope/page-network/
  generation-consistency/replayability）150 passed；全量 **889 passed /
  2 skipped / 1 failed**——唯一失败仍是基线
  test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive
  （干净 maa-dp HEAD 已有，非本轮引入）；check_visual 通过；git diff --check
  通过；trace validator ok:true（6 节点）。
- Windows headed：本机 macOS，未验证（记入剩余风险）。
- 剩余风险（下一轮候选）：
  1. request_count=0 真实录制闭环（连续四轮遗留）；
  2. 网络断言 × popup 组合（连续四轮遗留）；
  3. Windows headed 未验证（连续五轮遗留）；
  4. 同 origin 但 scheme 不同（http→https）的导航行为未专项覆盖；
  5. recorded_page_for（pytest 链按 pageId 换页）与 _page_for_node（trace 链）
     对「pageOrigin 与 pageId 组合消歧」的语义一致性未压测。

### 2026-10-30 — P2.4 页面关闭（tab/popup close）录制与双链回放

- 提交：`9c9bcf8`（分支 maa-dp；验收录制产物 recordings/p2-page-close-recording/
  由 scripts/record_p2_page_close_acceptance.py 在该提交工作树上复现）。
- 起点基线：`maa-dp` @ 7a9a77e（edr-cloud-recorder-p2 worktree，干净起步）。
  主 checkout `codex/fix-windows-recorder-storage-restore` 的并行未提交修改未触碰。
- 选择缺口：矩阵「页面关闭」——连续两轮最高风险。录制器对关标签页零步骤产出，
  回放终态比录制多一个标签页，page_count 类断言（双链共用 expect_page_count）
  会判成与录制相反的结论。
- 失败测试先行：test/test_page_close.py（4 条，实现前全部失败：T1 无 page_close
  步骤；T2 spec 侧 KeyError: 'sel' / trace 侧无 ClosePage；T3 回放链 popup 不关）。
- 根因与修复（4 个文件，逐个转绿）：
  1. **record.py**（两处）：a) on_page_closed 只翻结束位，不落步骤 → 现在落一条
     `page_close` 步骤（id=close-<pageId>），经 accept 走双通道+pageId 归属；
     只在「会话继续」（还有别的活页）时落 —— 关最后一页是收尾不是业务步骤，
     落了会让生成的用例在结尾关掉自己的页面；opener 关系在 listen 时记下
     （close 后 page.opener 不可靠）。b) 泵循环加固：等着的页面在 200ms 等待窗
     内被关（TargetClosedError）但 context 还有活页时 continue 而不是 break ——
     原实现把「popup 恰好在被泵的间隙被关」误判成浏览器已关，整个会话提前收尾，
     关页后 opener 上的操作全丢。
  2. **generate_spec.py**：page_close 转译 `recorded_pages[pageId].close()` ——
     按 pageId 从 recorded_pages 取，不认当前变量 page（顺序换了就错）；缺
     pageId 拒发整份 spec。
  3. **generate_trace.py**：ACTION_TYPES 增加 `page_close → ClosePage`，
     param.pageId/openerPageId 落录制身份；不在 MAA_ACTIONS，自动标 web-only。
  4. **replay_trace.py**：SUPPORTED_ACTIONS 增加 ClosePage；_execute_action 执行
     page.close()（对象由 _page_for_node 按 pageId/opener 关系解析）；ClosePage
     节点与 Navigate 一样跳过 _assert_node_page 预检路线断言。
  5. record.py accept 的打印行对无 sel 步骤（page_close/navigate）兜底空串。
- 设计取舍：不走 injector 的 pagehide 通道 —— pagehide 在每次导航（点链接/
  reload）都会触发，会把正常导航误记成关页；宿主侧 Playwright close 事件只在
  真正关页时触发，且能拿到完整页面身份。injector 保持零改动。
- 反向验证（4 组突变，全部先失败后恢复，恢复后 4 文件 SHA 校验字节一致）：
  1. record.py 关页落步骤禁用 → T1 失败（无步骤）；
  2. generate_trace ClosePage 映射禁用 → T2-trace 失败；
  3. replay_trace ClosePage 执行分支禁用 → T3 失败（popup 未关）；
  4. generate_spec 转译分支禁用 → T2-spec 失败。
- 真实 headed 录制闭环：scripts/record_p2_page_close_acceptance.py →
  recordings/p2-page-close-recording/（recording.json + trace.json[ready,7 节点] +
  test_p2_page_close_recording.py + assets/step-*.png，skill validator ok:true）。
  流程：开 popup（target=_blank）→ popup 填值 → 浏览器级关 popup（页面上无
  关闭按钮，合成步骤不可被「点击本身」替代）→ opener 填值 → 菜单 page_count
  equals 1 断言（expectedConfirmed=true）。正向：pytest 链 PASS（pages=1）+
  trace 链 PASS（ClosePage 执行，pages=1）。反向 1：篡改 1→2 双链失败，生成链
  报 `页面数量断言失败: actual=1 expected=2 op='equals'`。反向 2：删 page_close
  再生成，回放终态 pages=2（与录制终态 1 错位，因果证实）。
- 验收脚本自身教训（记录给后续轮次）：page_count 反向验证必须用**全新 browser
  context** —— 复用正向链的 context 会把正向残留的页面数进去（actual=2 恰好
  满足篡改后的 expected=2），反向被污染放行。第一版脚本就这么假通过的。
- 门禁：定向 137 passed（含新 4 条）；全量 **885 passed / 2 skipped / 1 failed**
  ——唯一失败仍是基线 test_orchestrate.py::test_identity_match_rejects_ip_prefix_
  false_positive（干净 maa-dp HEAD 已有，非本轮引入）；check_visual 通过；
  git diff --check 通过；skill validator ok:true（新 trace）。
- Windows headed：本机 macOS，未验证（连续四轮遗留）。
- 剩余风险（下一轮候选）：
  1. request_count=0 仍只有单元证据（连续三轮遗留）；
  2. 网络断言 × popup 组合（连续三轮遗留）；
  3. 关最后一页（= 会话收尾）不落步骤是有意取舍，但「录制中段关掉唯一 popup
     后又新开第二个 popup」的多页序场景未专项覆盖；
  4. Windows headed 未验证（连续四轮遗留）；
  5. page_close 与 trace 回放的 _page_for_node 组合：关闭后再定位 opener 依赖
     recorded_pages 缓存，跨多条 popup 开关的复杂序列未压测。

### 2026-10-30 — P2.2 网络范围断言端到端（scope=network）

- 提交：`c4ae305`（分支 maa-dp；验收录制产物路径
  recordings/p2-network-scope-recording/ 在该提交的工作树上可由
  scripts/record_p2_network_scope_acceptance.py 复现）。
- 基线：分支 `maa-dp` @ 9c8be5d（edr-cloud-recorder-p2 worktree，干净）。
  主 checkout `codex/fix-windows-recorder-storage-restore` 上有并行会话未提交修改
  （rec_session.py/record.py/test_record_headless_smoke.py + 未跟踪 test），未触碰。
- 选择缺口：P2 §9.2 items 2–3 — rec_assert 已有 expect_response_status /
  matches_body_subset / request_count_zero 单元测试，但菜单无「最近请求」范围、
  generate_spec/replay_trace 无 scope=network 路径 —— 能力断链，无法录制。
- 失败测试先行：test/test_p2_network_scope.py（8 条：status/request_body/
  response_body 编译、未确认 expected 拒发、无因果动作拒发、trace 携带 scope、
  两链同判、篡改双链失败）。实现前 1 条即 KeyError 失败。
- 根因与修复（本轮 5 个独立缺陷，TDD 逐个转绿）：
  1. generate_spec 无 scope=network 分支 → 预扫描 _network_by_action +
     _emit_network_assert：断言编译进因果动作的 expect_request 等待块（观察
     起点=动作，杜绝「最后一条同 URL」错配）；无动作/坏算子/未确认 expected
     拒发整份 spec。
  2. generate_trace 把 scope=network 覆写成 web-only → 保留 scope；用户 expected
     合并进因果动作的 verification.responses（userAssertId 指回断言）。
  3. replay_trace「断言步骤缺少 DOM 选择器」→ _execute_action scope=network
     分支（校验由本步响应等待块承担）；expectedConfirmed=false 独立拒绝。
  4. **录制侧真实缺陷（本轮最重要发现）**：菜单 afterStepId 取
     `steps[steps.length-1]?.id`，但 record.py 的 pump 周期性 drain 页面本地
     steps 数组 —— 真实人工操作时因果步骤已被 drain，afterStepId 恒缺失，
     网络断言永远无法归属动作。修复：injector 维护 lastStepId（drain 不清），
     三个 push*Assert 都用它兜底。离线测试测不出（菜单测试不 drain），
     只有真实 record_session 闭环才暴露。
  5. expose_binding 返回 Promise → 菜单 fillNetRequests 同步读取恒空。
     修复：Promise/同步双路径 + 每次切到 network 范围重新拉取。
- 反向验证：禁用 generate_trace 期望合并 → test_two_paths_agree_status_
  tampered_fails 失败（201 被当成 500 通过 = 假绿被测试抓住）。恢复后全绿。
- 真实 headed 录制闭环：scripts/record_p2_network_scope_acceptance.py →
  recordings/p2-network-scope-recording/（recording.json + trace.json +
  test_p2_network_scope_recording.py + assets/step-0001.*.png，trace ready）。
  正向：pytest 链 PASS + trace 链 PASS。反向：篡改 201→500，双链失败且
  失败信息含 `rec-assert: <id> actual=201 expected=500`（生成链）与
  `201 != 500`（回放链）。
- 已知基线失败（非本轮引入，maa-dp 干净 HEAD 复现）：
  test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive —
  _valid_ipv4s 只认真实 IPv4，测试用的 REDACTED-INTERNAL-IP-* 占位串不匹配。
  属 orchestrate/端云身份匹配领域，留给并行会话。
- 门禁：定向 184 passed；check_visual 130 passed + visual checks passed；
  git diff --check 通过；skill validator ok:true（新 trace）；全量 2 skipped
  中 1 failed（即上述基线），其余全过。
- 剩余风险（下一轮候选）：
  1. request_count=0 只有单元证据，缺真实录制闭环（观察窗起止边界在真实
     交互节奏下的行为未验证）；
  2. response_body subset 在 4xx/5xx + 脱敏字段组合下的失败信息可读性；
  3. Windows headed 未验证本轮新功能（本机为 macOS）；
  4. 网络断言与 popup 场景组合（新标签页里发的请求归属 opener 还是 popup）
     未覆盖。

### 2026-10-30 — P2.3 页面导航事件（reload / 前进后退）录制与双链回放

- 提交：`070b244`（分支 maa-dp；验收录制产物 recordings/p2-nav-events-recording/
  由 scripts/record_p2_nav_events_acceptance.py 在该提交工作树上复现）。
- 起点基线：`maa-dp` @ b54620e（worktree 干净）（edr-cloud-recorder-p2 worktree，干净起步）。
  主 checkout `codex/fix-windows-recorder-storage-restore` 的并行未提交修改未触碰。
- 选择缺口：矩阵「页面 reload/前进后退/关闭」——录制器对 F5/后退零步骤产出，
  历史导航在 recording.json 里完全不可见，回放少做导航且失败误导。
- 失败测试先行：test/test_nav_events.py（先 5 条后 6 条，实现前全部失败）。
- 根因与修复（4 处最小改动）：
  1. **recorder-inject.mjs**：init script 时读 Navigation Timing 的
     `performance.getEntriesByType('navigation')[0].type`，reload/back_forward
     推一条 `type=navigate, navType` 步骤（navigate 首载不记——由 start_url 承担）。
     实测覆盖 bfcache 恢复的跨文档 back（init script 会重新执行）。
  2. **generate_spec.py**：navigate 步骤转译 `page.reload()/page.go_back()`；
     未知 navType 拒发整份 spec（与未知断言同一红线）。
  3. **generate_trace.py**：ACTION_TYPES 增加 `navigate → Navigate`，
     param.navType 细分；Navigate 不在 MAA_ACTIONS，自动标 actionScope=web-only。
  4. **replay_trace.py**：SUPPORTED_ACTIONS 增加 Navigate；_execute_action
     执行 page.reload()/go_back()；**_assert_node_page 对 Navigate 节点跳过
     路线断言**（本缺陷由真实录制闭环暴露：back_forward 执行前路线是 /next，
     预检把它报成「页面路径不符」，真正该做的导航被吞掉）。
- 真实录制过程中顺带确认的既有行为（非本轮缺陷，已写入验收脚本注释）：
  Playwright 的 fill() 不发 change 事件 —— 填值前先点击聚焦、填完 blur 才有
  change；菜单 value 断言 expected='' 必须走 is_blank + 勾确认框（P0 红线正确拦截）。
- 反向验证（4 组突变，全部先失败后恢复，恢复后 SHA 校验字节一致）：
  1. 注入层 gate 禁用（`if false &&`）→ 2 条录制测试失败；
  2. generate_spec 转译分支禁用 → 3 条失败（含 KeyError 证明步骤贯穿全链）；
  3. 回放 Navigate 分支禁用 → 回放后输入框非空（状态错位被抓住）；
  4. _assert_node_page 的 Navigate 跳过禁用 → test_replay_navigate_skips_pre_route_assert
     失败（报「页面路径不符」）。
- 真实 headed 录制闭环：scripts/record_p2_nav_events_acceptance.py →
  recordings/p2-nav-events-recording/（recording.json + trace.json[ready,6 节点] +
  test_p2_nav_events_recording.py + assets/step-*.png）。流程：聚焦点击 → fill →
  blur → F5 重载 → 菜单 is_blank 断言（勾确认框）→ 点链接 /next → 后退。
  正向：pytest 链 PASS（含 page.reload/go_back 文本）+ trace 链 PASS（Navigate 2 次）。
  反向 1：篡改 is_blank→hello，双链明确失败。反向 2：删掉 navigate 步骤再生成，
  回放后输入框仍是 hello（状态错位如实暴露）。
- 门禁：定向 223 passed / 1 skipped；replay 回归 103 passed；全量
  **881 passed / 2 skipped / 1 failed**——唯一失败仍是基线
  test_orchestrate.py::test_identity_match_rejects_ip_prefix_false_positive
  （干净 maa-dp HEAD 已有，非本轮引入）；check_visual 通过；git diff --check 通过。
- Windows headed：本机 macOS，未验证（记入剩余风险）。
- 剩余风险（下一轮候选）：
  1. 页面关闭（tab close 后 opener 侧后续步骤归属）仍未专项覆盖；
  2. request_count=0 仍只有单元证据（上一轮遗留）；
  3. 网络断言 × popup 组合（上一轮遗留）；
  4. Windows headed 未验证（连续三轮遗留）。
