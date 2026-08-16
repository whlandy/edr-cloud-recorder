# 云端 + 端侧编排：edr-wd 是什么、怎么配合、怎么装

录制器解决的是「云端控制台做了什么」。但很多东西**只在终端上才看得见效果** ——
下发一条策略，控制台返回 200 只说明它记下了，终端有没有真的生效是另一回事。

`orchestrate/` 就是把这两边串起来的框架，端侧能力来自 **edr-wd**。

## 分工

| | 负责 | 手段 |
|---|---|---|
| 本 skill 的录制器 | 摸清云端接口契约 | 浏览器录制 → Playwright + 接口记录 |
| `orchestrate/scenario.py` | 编排云端与端侧交替的步骤 | 纯 Python，无外部依赖 |
| `orchestrate/endpoint.py` | 驱动终端 GUI | 调用 **edr-wd** 的 MCP 工具 |
| `orchestrate/recording_contract.py` | 把录制请求交给云端客户端 | 唯一选择 + 显式写入闸门 |
| **edr-wd** | 在 Windows/macOS 上操作 GUI | pywinauto / AX + MCP over HTTP |

**edr-wd 是独立项目，不属于本仓库。** 这里只提供一层薄封装 —— 建会话、调工具、
拆结果。业务判断留在场景脚本里，免得封装层长成第二个测试框架。

## edr-wd 的架构（用之前需要知道的）

```
你的电脑                          目标机器（Windows / macOS）
┌──────────────────┐  SSH        ┌────────────────────────┐
│ agent/           │ ──────────► │ MCP 服务（FastMCP HTTP）│
│  target_manager  │             │  ↓ pywinauto / AX      │
│  mcp_manager     │ ◄────────── │ 被测程序的 GUI          │
└──────────────────┘  MCP/HTTP   └────────────────────────┘
```

两层握手，顺序不能反：

1. **`ensure_server_running(target)`** —— 通过 SSH 确认目标机器上的 MCP 服务在跑，
   不在就拉起。这一步需要 SSH 凭据。
2. **`initialize(target)`** —— 建立 MCP 会话，拿到 `session_id`。这一步不需要 SSH。

跳过第一步直接 initialize，在服务没起来时只会得到一个语焉不详的连接错误 ——
`endpoint.py` 的 `connect()` 已经按正确顺序封好了。

## 安装

### 1. 装 edr-wd 本体

```bash
git clone <edr-wd 仓库> ~/ai-projects/edr-wd
cd ~/ai-projects/edr-wd
pip install -r requirements.txt        # paramiko 等
```

装在别处就设环境变量，`endpoint.py` 会据此找它：

```bash
export EDR_WD_HOME=/path/to/edr-wd
```

### 2. 配置目标机器

edr-wd 用一份 JSON 描述每台目标机器（`config/targets.local.json`），
照着 `config/targets.example.json` 改：

```jsonc
{
  "targets": {
    "win-dev": {
      "platform": "windows",
      "ssh": {
        "host": "<目标机器 IP>",
        "port": 22,
        "user": "<账号>",
        "auth": { "type": "password", "password": "<密码>" }
      },
      "mcp": {
        "host": "0.0.0.0", "port": 8765, "path": "/mcp",
        "connect_mode": "tunnel",
        "tunnel": { "enabled": true, "local_port": 18765 }
      },
      "windows": {
        "python_path": "C:\\...\\python.exe",
        "target_root": "C:\\Users\\<用户>\\Desktop\\edr-wd",
        "task_name": "StartEDRMCP",
        "run_with_highest_privileges": true
      }
    }
  }
}
```

**凭据放这个文件里，别放进任何仓库。** 建议 `chmod 600`。

### 3. 目标机器上的准备

- 开 SSH（Windows 用 OpenSSH Server）
- 装 Python
- 把 edr-wd 的 `target/` 目录部署过去（`deploy_target()`），并安装计划任务

### 4. 验证

```python
from agent.target_manager import health_detail
print(health_detail("win-dev"))
# 期望 ready_level 到 mcp_ready 或 gui_ready
```

## 计划任务必须是「交互式」，不要改成「不管用户是否登录都运行」

这是个反直觉的点，值得单独说。

常规服务应该配成「不管用户是否登录都运行」，这样机器重启也能自恢复。**但 GUI 自动化
恰恰相反** —— 那个选项会把进程放进 Session 0，那里**没有桌面**，pywinauto 什么都
点不到。

必须用 `InteractiveToken`，让 MCP 服务跑在有桌面的会话里：

```
services     0  Disc          ← 没有桌面，别把服务放这里
console      1  Conn
rdp-tcp#2    2  Active        ← MCP 服务要在这种会话里
```

代价是：RDP 断开后服务会停。这是 GUI 自动化的固有取舍，不是配置错误。

## 写一个场景

```python
from scenario import Scenario
from endpoint import Endpoint

ep = Endpoint("win-dev", process_name="YourClient.exe", home_window="主窗口标题")

sc = Scenario("某策略的云→端联动")
sc.cloud("读取当前配置", read_cloud)
sc.endpoint("连接并校验身份", lambda: ep.connect().assert_matches(cloud_object_name))
sc.endpoint("采集端侧基线",  lambda: snapshot(ep.table_rows(refresh_text="Refresh")))
sc.cloud("下发变更",        apply_change)
sc.until("等待端侧出现新记录", probe, timeout=90, interval=5)
sc.endpoint("断言新记录符合预期", assert_new)
sc.cleanup("还原",          restore)
sc.run()
```

三种步骤的语义：

- **`cloud`** —— 调云端接口
- **`endpoint`** —— 操作或读取终端界面
- **`until`** —— 轮询等待，**云端到端侧那段时间差的唯一正确等法**
- **`cleanup`** —— 无论主流程成功、失败或中断都执行；用于还原真实写入

还原不能写成最后一个普通 `cloud` 步骤；主流程在前面失败时，普通步骤会被跳过。
`cleanup` 失败也会让场景失败，必须人工确认目标当前状态。

## 使用录制请求实现 CloudClient

录制器的 `<name>.json` 已保存 method、URL、请求体和响应状态。不要再手工抄请求体：

```python
from orchestrate.recording_contract import RecordingContract

contract = RecordingContract.load("recordings/policy-flow/recording.json")
write = contract.one(method="POST", url_contains="/api/v1/policy")
assert write.response_status == 200
print(write.json_body)

# sender 由业务项目提供，负责认证、环境和超时；建议传测试环境 URL。
contract.replay(
    sender,
    method="POST",
    url_contains="/api/v1/policy",
    url="https://staging.example/api/v1/policy",
    allow_write=True,  # 已采基线并注册 Scenario.cleanup 后才能打开
)
```

选择器必须只匹配一条请求，否则直接报错。写请求默认拒绝，而且必须显式给出目标 URL，
避免加载录制时误触生产环境。
录制器不保存认证头；`sender` 应使用业务项目已有的认证客户端。

## 为什么"等生效"只能朝端侧轮询

云端接口返回 200 只代表服务端记下了配置。终端要等下一次心跳才拉到新策略。

而**云侧未必有可用的收敛信号**。实测过一个产品：一次真实下发前后，策略详情接口和
资产树里的 `enableStatus` 都纹丝不动，只有 `creationType` 从"继承"变成"自定义" ——
那只在首次转换时变，重复下发同样不动，没法用来判断这一次是否落地。

所以只能反复读终端，直到它反映出新配置。固定 `sleep` 是错的：短了必然偶发失败，
长了每个场景白等几分钟。

## 断言点怎么选

优先选**留痕**（操作日志），而不是**状态**（某个开关的显示值）。

理由在 [ui-assertions.md](ui-assertions.md) 里展开了，简单说：留痕带时间戳，
能和基线做差集，天然区分"这次产生的"和"本来就有的"；而状态类断言既可能在界面上
根本不显示，显示了也分不清是不是本次改的。

实测中一个「自保护」开关在客户端主界面上**根本没有对应控件**，最初凭想象写的
`text_present("自保护")` 永远跑不通；换成读操作日志做差集之后，一次命中。

## 常见故障

| 现象 | 多半是 |
|---|---|
| `MCP 服务起不来：Port did not open` | 目标机器上服务没部署，或进程活着但没 bind |
| `Connection reset by peer` | 本机隧道端口有残留监听，`lsof -ti:<port>` 清掉 |
| `No connectable top-level windows` | 被测程序没有界面在前台；先把它打开 |
| `click_context_required` | 该产品要求点击时带上 `expected_process_name` |
| 界面读到的值一直不变 | 表格没刷新，见 [ui-assertions.md](ui-assertions.md) |
| 端侧"一直没反应" | 先确认云端操作的对象和端侧连的机器是同一台 |
