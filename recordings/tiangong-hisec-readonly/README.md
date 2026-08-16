# 天工云侧 + HiSec 端侧用例

这是第一条端云结合用例：

1. 云侧使用 `trace.json` 纯视觉回放“7天 -> 30天 -> MiniMax-M3 断言”。
2. 端侧通过 edr-wd 精确连接并激活 `HiSecEndpoint` 主窗口。
3. 根据主窗口矩形点击“日志中心”，严格等待同一进程的“日志中心”窗口。
4. `execution.json` 同时保存云侧评分、场景步骤和端侧窗口证据。

本地验证时会产生以下端侧操作证据。它们包含机器运行态信息，公开仓库不提交：

- `endpoint.execution.json`：激活 HiSec、点击“日志中心”、确认目标窗口。
- `endpoint-after-click.png`：点击后的日志中心截图。
- 上述激活、点击、窗口断言和截图均由 edr-wd 执行，不使用 Computer Use。

运行前需启动 edr-wd MCP server，并设置 `EDR_WD_ALLOW_REAL_CLICKS=1`。可用环境变量：

- `RECORDER_HOME`：本仓库路径。
- `EDR_WD_HOME`：edr-wd 仓库路径。
- `EDR_WD_TARGET`：目标名，默认 `mac-77`。
- `EDR_ENDPOINT_PROCESS`：端侧进程窗口所有者，默认 `HiSecEndpoint`。
- `EDR_ENDPOINT_MAIN_WINDOW`：主窗口标题，默认 `HiSec Endpoint`。
- `EDR_ENDPOINT_RESULT_WINDOW`：点击后的窗口标题，默认“日志中心”。
