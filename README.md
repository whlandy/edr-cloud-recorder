# web-record

把网页上的手工操作录制成可重复执行的 Playwright (Python) 脚本，同时抓取每一步触发的
HTTP 接口（含请求体、状态码、失败响应）。

这是一个可安装的 Agent Skill，
但脚本本身**不依赖任何特定 agent 或浏览器扩展** —— 只要 Python + Playwright，
在终端里直接跑也可以。

## 和 `playwright codegen` 的区别

codegen 只回答「点了哪里」，不回答「点完之后发生了什么」。本工具把两件事绑在一起：

```python
page.get_by_role("button", name="确认", exact=True).click()
#   ↳ POST /api/v1/policy/apply -> 200
#   ↳ GET  /api/v1/policy/status -> 200
```

当你的目的是搞清楚接口契约、或者想让脚本断言「这一步应该触发某个 POST 并返回 200」时，
codegen 给不了答案。

## 用法

```bash
# 在目标项目里准备环境
cp -r <此仓库>/assets/* .
python -m pip install -r requirements.txt

# 录制
python <此仓库>/scripts/record.py --url https://app.example.com --name login-flow
```

浏览器窗口弹出 → 正常操作 → 关闭窗口 → 输出到 `recordings/<name>/`：

| 文件 | 内容 |
|---|---|
| `recording.json` | 原始记录：步骤、点击元素的渲染特征、网络事件，都带毫秒时间戳 |
| `trace.json` | 与脚本一一对应的完整成功轨迹；包含模板匹配和网络成功条件。
  节点表用 MaaFramework 形状（`edr.success-trace/v2`），maa-fw 的 `MaaNodeRunner` 可直接加载 |
| `.auth/` | 本次录制的 cookies、localStorage 和 sessionStorage；仅供本机回放，权限为 `0600` 且默认忽略提交 |
| `assets/step-*.png` | 点击元素的黑盒 UI 模板，可供模板、SSIM 或特征匹配使用 |
| `test_<name>.py` | 可跑的脚本草稿，接口调用作为注释挂在对应步骤下 |

回放：

```bash
pytest recordings/login-flow/test_login_flow.py
```

成功 trace 也可以作为 Agent 的黄金路径回放，并生成独立的执行轨迹和评分：

```python
from replay_trace import evaluate_trace, load_trace, replay_trace

case_dir = "recordings/login-flow"
execution = replay_trace(
    recorded_page,
    f"{case_dir}/trace.json",  # 自动加载同目录 .auth 中的录制会话
    template_root=case_dir,
    targeting="visual_only",  # 或 dom_first
    execution_path=f"{case_dir}/execution.json",
)
golden = load_trace(f"{case_dir}/trace.json")
report = evaluate_trace(golden, execution)
assert report["taskSuccess"]
```

`replay_trace.py` 运行时需要 `assets/` 和 `scripts/` 都在 Python import path 中。runner 会先
恢复 `trace.json` 同目录 `.auth` 中的录制会话，再进入 `startUrl`，并在动作之前建立网络监听；
任何模板定位、动作或响应断言失败都会终止路径，
不会把只执行了一部分的轨迹记成成功。

## 结构

```
SKILL.md              技能定义：触发条件、工作流、录制后必做的三件事
agents/openai.yaml     Codex 技能列表与默认调用提示
scripts/
  record.py           录制驱动
  recorder-inject.mjs 页面内录制器（唯一的 JS —— 它在浏览器里跑）
  generate_spec.py    从录制数据生成 pytest 草稿
  generate_trace.py   把一次完整录制编译成一条黄金成功轨迹
  trace_schema.py     轨迹形状的唯一定义处（生成、回放、测试都走它）
  replay_trace.py     回放黄金轨迹，产出执行轨迹并计算 Agent 指标
  selector_py.py      把 JS 语法的选择器转成 Python 语法
  recorder_loader.py  把注入层原样喂给 add_init_script
assets/               可直接复制到目标项目的模板
  conftest.py         录制会话/登录态 page + 超时 + 产物清理
  auth_setup.py       登录流程（要改的地方都在这里）
  rec_helpers.py      dismiss_overlays / confirm_and_capture / is_present / nth_request …
  rec_assert.py       assert_subset / ANY_STR / ANY_NUM / poll_until
  rec_config.py       配置加载与凭据解析（env > config.json）
  rec_visual.py       DOM 点击失败后的多尺度视觉模板回退
  chrome_path.py      跨平台探测本机 Chromium（避免为版本号重下 170MB）
  manual_login.py     站点要求验证码时的兜底
  pytest.ini          trace/录像/截图、重试、超时、默认收集范围
test/
  test_verify.py      自检：每项守一条 SKILL.md 里的承诺
  fixture_drive.py    造一个含全部边界情况的页面并驱动一遍
orchestrate/
  scenario.py         云端+端侧交替编排：cloud / endpoint / until 三种步骤
  endpoint.py         edr-wd 薄封装（驱动终端 GUI）
  recording_contract.py  从录制 JSON 唯一选择并安全重放云端请求
  example_scenario.py 可照抄的模板
references/           按录制、trace、视觉、选择器、登录等专题渐进加载
```

## 设计上的几个取舍

**注入层保持 JavaScript。** `recorder-inject.mjs` 是注入到被测页面里执行的浏览器脚本，
无论驱动侧用什么语言，这部分都只能是 JS。它没有被改写成别的形式，也不该被改写 ——
那 600 多行全是实测出来的 DOM 细节。驱动侧用 `add_init_script` 把它原样注入。

**凭据不进产物。** `type=password` 的输入只记录「填了密码」这个动作，值在生成的脚本里
替换成 `os.environ["REC_PASSWORD"]`。网络请求和响应里的密码、token、authorization、API key
也会在落盘前替换成 `<redacted>`，生成断言时转成类型匹配。运行时没提供密码会明确失败，
不会静默输入空字符串。

**运行时生成的 id 会被跳过。** 像 `tip_box_10059` 这种自增 id 每次加载都变，用它定位
必然在第二次运行时失败。含 3 位以上数字的 id 和 class 一律不用。

**文本撞车会被标出来，而且把隐藏元素算进去。** Playwright 的 `get_by_text` /
`get_by_placeholder` / `get_by_label` / `get_by_test_id` 都会匹配隐藏元素，只有
`get_by_role` 走无障碍树不匹配。只数可见元素会漏算「收起的浮层里的同名选项」，
产出回放时必然 strict mode 失败的选择器。

**开关录成「拨到指定状态」而不是盲点一下。** 只录一次 click 的话，回放时初始状态一旦
与录制时不同就会朝反方向拨 —— 脚本不报错，只是把开关设错了。

**定位型动作同时保存黑盒 UI 特征。** 点击、双击、勾选和开关会记录元素边界、相对落点、
viewport、DPR 和少量计算后样式，并将动作前画面裁成 `element` 与 `context` 两张 PNG。
截图失败只缺少 `ui.templates`，不会阻断操作或录制；模板路径和 SHA-256 也写在该字段中。

视觉定位相关代码可单独快速检测：

```bash
python scripts/check_visual.py
```

**只能用 CSS 兜底的点击会被包成「存在则点」。** 这类元素绝大多数是关闭弹窗，
弹窗不出现时脚本不该失败。

**失败响应体会被记录，成功的 GET 不会。** 排查 4xx/5xx 时响应体是唯一有用的信息；
成功的 GET 响应动辄几十 KB，全存没有价值。响应体在事件回调里**当场**取 —— 攒到最后
再取会撞上 Chromium 把 body 从网络缓存里淘汰。

**登录态边录边快照。** 用法是「操作完直接关浏览器窗口」，所以每个轮询周期都直接读取
cookies 和现有 frame 的 localStorage。这里不能反复调用 `storage_state()`：它会为第三方
origin 创建临时页面再关闭，导致有界面录制时窗口持续闪烁。

## 依赖

**录制器**：Python 3.10+、`playwright`、一个 Chromium 构建（系统装的或 Playwright
缓存里的都行）。

**回放**：另加 `pytest`、`pytest-playwright`、`pytest-rerunfailures`、`pytest-timeout`，
见 `assets/requirements.txt`。

**编排（可选）**：[edr-wd](references/endpoint-orchestration.md) —— 只有需要到终端上
验证效果时才用得上，录制器本身不依赖它。

## 分支

| 分支 | 内容 |
|---|---|
| `main` | 默认分支 |
| `py-format` | Python 实现的稳定线。黄金轨迹相关的成果都落在这里 |
| `maa-dp` | 最新开发代码 |
| `js-format` | 迁移到 Python 之前的纯 JavaScript 实现 |

`js-format-codex` 是 **tag** 不是分支，指向 JS 时代并行的那条分叉
（它只有一个提交不在 `js-format` 里，所以用 tag 留住，不占一条分支）。
两条线的注入层与本版本同源。

## 许可

[MIT](LICENSE)
