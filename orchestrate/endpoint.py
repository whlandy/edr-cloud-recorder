"""
端侧：edr-wd 的薄封装

edr-wd（https://github.com/multica-ai/…，见 references/endpoint-orchestration.md）
是一套通过 MCP 驱动 Windows/macOS GUI 的工具。这一层只做三件事 ——
建立会话、调工具、把结果拆出来。业务判断留给场景脚本，免得它变成第二个测试框架。

**两层握手不能跳**：
  1. ensure_server_running  确保目标机器上的 MCP 服务在跑（要 SSH 凭据）
  2. initialize             建立 MCP 会话（不要 SSH 凭据）
跳过第一层直接 initialize，在服务没起来时只会得到一个语焉不详的连接错误。

被测程序的进程名、窗口标题这些**因产品而异**，通过构造参数传入，不写死在这里。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# edr-wd 的位置。装在别处就设 EDR_WD_HOME。
EDR_WD = Path(os.environ.get("EDR_WD_HOME", Path.home() / "ai-projects" / "edr-wd"))
if str(EDR_WD) not in sys.path:
    sys.path.insert(0, str(EDR_WD))


class EndpointError(RuntimeError):
    """端侧操作失败。与云侧错误分开，便于一眼看出问题出在哪一边。"""


class Endpoint:
    def __init__(self, target: str, process_name: str, *, home_window: str | None = None):
        """
        target        edr-wd 配置里的目标名（对应一台机器）
        process_name  连接哪个进程的窗口
        home_window   主界面窗口标题的匹配式。同一进程常有多个顶层窗口
                      （设置、日志、详情），不指定的话读到的是当前最上层那个。
        """
        self.target = target
        self.process_name = process_name
        self.home_window = home_window
        self.session_id: str | None = None
        self.mcp_url: str | None = None

    # ---------- 连接 ----------

    def connect(self) -> "Endpoint":
        from agent.target_manager import ensure_server_running
        from agent.mcp_manager import initialize

        r = ensure_server_running(self.target)
        if not r.get("ok"):
            raise EndpointError(f"{self.target} 的 MCP 服务起不来：{r.get('error')}")

        r = initialize(self.target)
        if not r.get("ok"):
            raise EndpointError(f"{self.target} MCP 握手失败：{r.get('error')}")

        self.session_id = r["data"]["session_id"]
        self.mcp_url = r["data"]["mcp_url"]
        self.attach(self.process_name)
        return self

    def attach(self, process_name: str) -> dict:
        """把后续操作切到另一个进程的窗口。同一产品常有多个进程各带界面。"""
        return self.call("connect", {"process_name": process_name})

    # ---------- 调用 ----------

    def call(self, tool: str, args: dict | None = None, timeout: float | None = None) -> dict:
        from agent.mcp_manager import call_mcp_tool, unwrap_tool_result

        if not self.session_id:
            raise EndpointError("尚未 connect()")
        out = unwrap_tool_result(
            call_mcp_tool(self.session_id, self.mcp_url, tool, args or {}, timeout=timeout))
        if isinstance(out, dict) and out.get("ok") is False:
            raise EndpointError(f"{tool} 失败：{out.get('error') or out.get('raw')}")
        return out

    # ---------- 读界面 ----------

    def tree(self, window_re: str | None = None, depth: int = 10) -> dict:
        # 可选参数要省略，不能传 None —— 工具的 schema 会拒绝 null
        args: dict = {"max_depth": depth}
        if window_re:
            args["window_title_re"] = window_re
        return self.call("dump_tree", args, timeout=300)

    def texts(self, window_re: str | None = None) -> list[str]:
        """界面上所有文本节点，去重排序。写探针前先看一眼这个，别凭空猜。"""
        s = json.dumps(self.tree(window_re), ensure_ascii=False)
        return sorted({x for x in re.findall(r'"(?:title|name|text)":\s*"([^"]{1,80})"', s) if x.strip()})

    def click(self, text: str, process_name: str | None = None, **extra) -> dict:
        """
        按文本点击。

        某些产品要求同时给出 expected_process_name 来消歧（同名窗口分属不同进程），
        漏了会直接被拒绝。默认带上当前连接的进程名。
        """
        args = {"text": text, "expected_process_name": process_name or self.process_name}
        args.update(extra)
        return self.call("click", args, timeout=120)

    def screenshot(self, path: str | None = None) -> dict:
        return self.call("screenshot", {"path": path} if path else {})

    # ---------- 身份校验 ----------

    def identity(self, window_re: str | None = None) -> dict:
        """
        从主界面读出这台机器的 IP 和主机名 —— 多数管理类客户端会把它们显示在首页。
        """
        s = json.dumps(self.tree(window_re or self.home_window), ensure_ascii=False)
        ip = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", s)
        host = re.search(r'"((?:DESKTOP|WIN)-[A-Z0-9-]+)"', s)
        return {"ip": ip.group(0) if ip else None, "hostname": host.group(1) if host else None}

    def assert_matches(self, expected_name: str, window_re: str | None = None) -> str:
        """
        确认端侧连的机器就是云端要操作的那台。

        不做这个校验的代价是真实发生过的：云端一直往 A 下发、端侧连的是 B，
        整条链路每步都"成功"，但验证的是两台无关的机器，而现象是"端侧一直没反应"——
        往心跳、日志机制方向查了很久，根因只是参数传错。
        报错必须把两边都打出来，否则人还是得自己去查。
        """
        me = self.identity(window_re)
        hits = [v for v in (me["ip"], me["hostname"]) if v and v in expected_name]
        if not hits:
            raise EndpointError(
                f"端侧机器与云端对象不是同一台：\n"
                f"  端侧 {self.target} → ip={me['ip']} hostname={me['hostname']}\n"
                f"  云端 → {expected_name}"
            )
        return f"{self.target} ↔ {expected_name}（匹配 {', '.join(hits)}）"

    # ---------- 表格 / 日志 ----------

    def table_rows(self, *, refresh_text: str | None = None,
                   row_start: str = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                   window_re: str | None = None, cols: int = 6) -> list[str]:
        """
        把界面上的表格读成行。默认按时间戳切行 —— 日志类表格几乎都以时间开头。

        refresh_text 是刷新按钮的文本，**强烈建议传**：表格是已渲染的界面，
        新记录到达后不会自己重画。不刷新就轮询，等多久都是旧内容 ——
        实测因此把 5 秒就到的记录误判成 240 秒都没出现。
        """
        if refresh_text:
            self.click(refresh_text)
            time.sleep(2)
        s = json.dumps(self.tree(window_re), ensure_ascii=False)
        vals = re.findall(r'"(?:title|name|text)":\s*"([^"]{1,80})"', s)
        rows, cur = [], None
        for v in vals:
            if re.fullmatch(row_start, v):
                if cur:
                    rows.append(cur)
                cur = [v]
            elif cur is not None:
                cur.append(v)
        if cur:
            rows.append(cur)
        return [" | ".join(r[:cols]) for r in rows]
