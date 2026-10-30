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
| 页面 reload/前进后退/关闭 | ⚠️ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ | 无专项场景 |
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
