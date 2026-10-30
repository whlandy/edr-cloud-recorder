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
| 同主机不同端口 / 跨 origin | ⚠️ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ | 无专项场景 |
| Cookie/localStorage/sessionStorage 恢复 | ⚠️ | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | rec_session 快照有测试（test_session_restore 属并行会话） |
| **页面 reload/前进后退导航事件** | ✅（2026-10-30 本轮） | ✅ headed | ✅ | ✅ | ✅（篡改 + 删导航步骤 + 路线断言突变） | ❌ | recordings/p2-nav-events-recording, test_nav_events |
| 页面关闭（窗口/tab close 后续步骤归属） | ⚠️ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ | popup close 分支未见专项录制 |
| 网络失败 / 读取异常非通过 | ✅ | n/a | ✅ | ✅ | ✅ | ❌ | test_review_trace_edges, test_failure_categories |
| Windows headed Chrome 启动/录制/回放 | ⚠️ | ✅（旧轮） | ✅ | ✅ | ⚠️ | ⚠️ | test_windows_compat, test_windows_smoke |
| 短命标签页闪开闪关 | ✅ | ✅（旧轮） | ✅ | ✅ | ⚠️ | ✅ | aa941af（Windows storage restore tab 修复） |
| 回放节奏 / 导航等待 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ | 无专项场景 |

## 轮次记录

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
