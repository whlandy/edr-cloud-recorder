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
| **request_count=0 有界观察窗** | ⚠️ | ❌ | ✅ 单元 | ✅ 单元 | ⚠️ 单元 | ❌ | test_p2_page_network |
| **网络断言「最近请求」菜单交互** | ✅（本轮） | ✅ headed | n/a | n/a | ✅ | ❌ | test_assert_menu（真实菜单 iframe 交互） |
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
| 回放节奏 / 导航等待 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ | 无专项场景 |

## 轮次记录

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
