# 断言层硬化设计（对照测试平台规范 V1.0）

- 日期：2026-10-19
- 范围：`scripts/generate_spec.py`（生成侧）、`scripts/replay_trace.py`（回放侧）、
  `assets/rec_assert.py`（断言库，本设计不改）
- 依据：2026-10-19 对《企业内部一体化软件测试平台技术设计规范 V1.0》的断言层
  审计。审计结论：**断言核心语义满足规范**（算子集、动态期望值 localtime/timewindow、
  易变数据不误报、`validate_trace` 静态校验、optional 语义），测试覆盖完整
  （277 passed）。存在三个真实缺口 G1–G3，两个架构边界项 G4–G5（不做）。

## 一、缺口清单

| 编号 | 严重度 | 缺口 | 规范条款 |
|---|---|---|---|
| G1 | 高 | 未知断言类型 / 不可转译选择器 → 生成的用例**静默少断言**，spec 照常通过（假绿路径） | §6.3.7 发布时静态校验；§11.2 不误报通过 |
| G2 | 中 | 断言失败无结构化 `failure_category`，质量报告的「失败类型分布」算不出 | §7.1 failure_category；§3.5 失败类型分布 |
| G3 | 中 | cleanup 无独立记录，清理失败会搅浑业务结果 | §6.6 `cleanup_status` 独立记录，不得覆盖原始业务结果 |
| G4 | 边界 | 无取消信号（checkCancellation） | §8.1 / AT-013 |
| G5 | 边界 | 无 `command_id`/attempt 消息幂等 | §6.5 / AT-011 |

**G4/G5 非目标**：取消信号与结果消息幂等属于平台编排层/Worker 层职责。
recorder 的定位是「用例生成 + 本地回放」，执行结果是文件协议
（`edr.execution-trace/v1`），由 Worker 层包装成任务消息。此处显式声明边界，
不认领实现。

## 二、阶段 1（P1 / G1）：生成时硬拒未知断言 —— 消灭假绿

### 问题
`generate_spec.py` 遇到未知 `assertion` 类型时写一行
`# ⚠ 未知断言类型 …，已跳过` 然后继续生成；选择器 `SelectorError` 同理
（动作行注释掉）。生成的 spec 里这条断言**不存在**，测试照常通过 ——
录制者以为守住了，其实没守。桌面侧转换器已有「不猜」纪律
（未知 actionId → DoNothing + unmappedAction），web 生成侧缺同等的门。

实现勘察（2026-10-20）补记**第三处**同类跳过点：动作发射器对未知步骤
类型（click/dblclick/fill/check/uncheck/press 之外）走 `else: continue`，
整步动作被静默丢弃。与未知断言同一红线，P1 一并收口。

### 设计
1. 新增 `SpecGenerationError`（`generate_spec.py` 内），携带问题列表
   （步骤序号 + 步骤 id + 原因，如 `step#5: 未知断言类型 'html'`）。
2. 生成过程中**收集**所有问题（不 fail-fast，一次报全），结束前统一抛出；
   **不写出任何 spec 文件**（原子性：宁可不生成，不生成半份）。
3. 合法的语义改写**不算跳过**，保持现状：
   - 同义反复断言（getByText 锚 + 文本等于自身）→ 存在性断言
     （`_wasTextTautology` 路径）；
   - 动态期望值断言（localtime/timewindow）的改写路径。
4. 错误消息带机器可读前缀（`生成失败（N 步无法转译）：…`），便于 CI 解析。

### 兼容性（已核实）
- 全语料扫描（recordings/ 全部 recording.json）：断言类型只有
  `text`（静态）与 `text`+localtime，**无未知类型** → 对现有录制零回归。
- 桌面转换器（desktop_to_v2.py）不受影响（独立路径）。

### 验收测试（缺了该能力必挂）
| # | 测试 | 修前行为 | 修后行为 |
|---|---|---|---|
| 1 | 含未知断言类型的录制 → 生成 | 正常写出 spec（假绿） | 抛 `SpecGenerationError`，无文件产出 |
| 2 | 含不可转译选择器的录制 → 生成 | 正常写出（少动作） | 同上硬拒 |
| 3 | 只含已知类型的录制 → 生成 | 正常 | 输出与旧行为**逐字节一致**（回归） |
| 4 | 同义反复改写仍生效 | 生效 | 不变（既有测试守住） |

### 完成标准
全量 pytest 绿（277 + 新增）；一个 commit；commit+push 按惯例重试循环。

## 三、阶段 2（P2 / G2）：execution 步骤结构化 failureCategory

### 问题
replay 的 per-node result 只有 `status`（success/failed/skipped）+ 自由文本
`error`。ASSERTION / AUTOMATION / INFRASTRUCTURE 的信息其实都在（错误类型
可判定），但没有结构化字段 → §3.5 的失败类型分布、§7.1 的 failure_category
无法计算。

### 设计
1. `replay_trace.py` 的 per-node result：`status == "failed"` 时新增
   `"failureCategory"` 字段，取值 `assertion | automation | infrastructure |
   unknown`。判定规则**按异常类型/来源确定性映射，不做字符串匹配**：
   - `AssertionError` → `assertion`
     （覆盖 `_validate_response` 网络断言、`expect_local_time` /
     `expect_time_window` / `poll_until`、Playwright `expect()` 断言失败 ——
     它们全部抛 `AssertionError`）
   - 定位/交互失败（超时、被遮挡、元素缺席且非 optional、视觉歧义）→ `automation`
   - `TraceReplayError`、截图解码失败、模板/环境缺失 → `infrastructure`
   - 其余 → `unknown`（**宁可 unknown，不猜** —— 与桌面转换器同一纪律）
2. `evaluate_trace`：新增 `failureCategories` 计数字段（如
   `{"assertion": 1}`），**透传**，不进评分公式（分类是元数据不是罚分；
   score 权重零变化）。
3. Schema 兼容：`execution-trace/v1` 保持版本不变，`failureCategory` 是
   可选新增字段；老 execution 文件缺字段时 `evaluate_trace` 照常工作
   （计数为 0）。消费方核实：trace-eval 与 maa-fw 均不消费
   execution-trace 的 step 明细（maa-fw 只消费 golden），纯增量安全。

### 验收测试
| # | 测试 | 断言 |
|---|---|---|
| 1 | 合成 execution（failed 步骤带类别）→ evaluate_trace | 字段透传 + summary 计数正确 |
| 2 | fixture 驱动回放一个**故意断言失败**的轨迹 | failed 步骤 `failureCategory == "assertion"` |
| 3 | 基础设施类失败（如引用不存在模板/坏 schema 路径） | `failureCategory == "infrastructure"` |

### 完成标准
全量绿；**评分快照不变**（既有 score 断言全过即证公式未动）；一个 commit。

## 四、阶段 3（P3 / G3）：cleanup 独立记录

### 勘察结论（2026-10-19，影响本阶段范围）
`replay_trace.py` **不执行任何 cleanup 节点** —— golden 的 `cleanup: []`
目前只被生成侧使用（pytest 模板的 try/finally + 注释建议
「建议放 try/finally，中途失败也能还原」）。即：

- **P3a（生成侧，小）**：生成的模板里清理动作与业务断言**结构分离** ——
  清理失败不再可能覆盖业务判定：业务结果先定案，清理异常单独捕获并
  输出为独立的失败记录（不改变业务 PASS/FAIL 结论）。
- **P3b（回放侧，新能力，范围更大）**：replay 在主体循环后执行 cleanup
  节点，结果独立记录：`execution["cleanup"] = {"status": ..., "steps": [...]}`，
  **不影响** `execution["status"]`（业务）；`evaluate_trace` 新增
  `cleanupSuccess` 字段。需要复用现有节点的执行/视觉定位/optional 语义，
  是本三阶段中唯一可能**扩设计**的项 —— 实现前若发现 cleanup 节点结构与
  golden steps 不兼容（缺 selector/verifiers 字段等），先回来补设计，
  不边写边定。

### 实现勘察（2026-10-20）与范围修正（方案 A：契约先行）

更深一层的勘察推翻了两个前提：

1. **web 录制数据模型没有 cleanup 概念** —— recording.json 无标记、
   无字段；生成器旧模板只有一行注释建议。SKILL.md 的真实流程是
   **录制后用户手写 cleanup**（「add cleanup for writes」是显式步骤）。
   原 P3a「隔离机器录制的 cleanup 步骤」无数据源可隔离。
2. **全部语料的 `cleanup` 列表为空**（9 条桌面 golden + 全部 web
   trace）——P3b 若现在实现 = 零实例纯发明节点结构。

修正后（用户批准方向 A）：

- **P3a（已实现，契约先行）**：生成模板建立结构红线 —— 业务段整体进
  `try`，清理在独立函数 `_run_cleanup` 里于 `finally` 运行，清理异常
  只 print 告警不外抛。数据源暂为空（TODO 占位），用户手写清理落进
  隔离结构；红线（清理失败不推翻业务结论）从此由模板保证而非靠自觉。
- **P3b（降级为设计附录，延后实现）**：cleanup 执行协议（节点字段、
  状态语义、`cleanupSuccess` 评分独立）保留设计，**触发条件 = 首个
  非空 cleanup 语料出现**；届时先按附录补完节点结构定义再写码。

### 验收测试
| # | 阶段 | 测试 | 断言 |
|---|---|---|---|
| 1 | P3a | 生成的 spec 结构检查 | 清理异常被独立捕获，业务结论不被覆盖 |
| 2 | P3b | golden 含会失败的 cleanup 节点 → 回放 | 业务 success 不变；`execution["cleanup"]["status"] == "failed"` |
| 3 | P3b | cleanup 全成功 → evaluate_trace | `cleanupSuccess == True`，taskSuccess 不受影响 |

### 完成标准
P3a、P3b 各自一个 commit（可独立回滚）；全量绿。

## 五、阶段目标总表

| 阶段 | 改动文件 | 新增测试 | 完成标准 | 回滚 |
|---|---|---|---|---|
| P1 | `generate_spec.py` | 3 | 假绿路径关闭；语料零回归 | 1 commit |
| P2 | `replay_trace.py` | 3 | 失败可分类；评分零变化 | 1 commit |
| P3a | `generate_spec.py` | 3 | 模板级清理隔离（契约先行）；spec 仍可编译 | 1 commit |
| P3b | 延后（设计附录） | — | 首个非空 cleanup 语料出现时再实现 | — |

执行顺序 P1 → P2 → P3a → P3b；每阶段一个 commit 并推送
（`git -c http.version=HTTP/1.1 -c http.lowSpeedLimit=0 push` 重试循环）。
纪律沿用：**每项新能力必须有一个在缺少该能力时会失败的测试**；
不猜（未知类别/未知类型一律显式化，不静默降级）。
