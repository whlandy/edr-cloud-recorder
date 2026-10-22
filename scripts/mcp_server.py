"""
scripts/mcp_server.py — edr-cloud-recorder 的 MCP server（web 录制生态的黑盒出口）。

把 web-record 的「确定性、无浏览器依赖」能力暴露成 MCP 工具，让 eco（编排层）
和其它 MCP 客户端直接拿到：trace 校验、桌面黄金轨迹→v2 转换、mma-fw 契约检查。
交互式录制（record.py 需要真人操作浏览器）和被 `page` 承载的回放不在此列——
它们由 eco/agent 通过命令行或已登录会话驱动。

返回一律是 `json.dumps(..., ensure_ascii=False)`（与 edr-wd / trace-audit 契约一致）。

启动：
  python3 -m mcp_server            # stdio（默认）
  python3 -m mcp_server --http --host 127.0.0.1 --port 8791
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# scripts/ 里的脚本都用 `from trace_schema import ...` 这种同目录相对导入，
# 所以把 scripts/ 加进 sys.path 再加载（与它们直接被运行时一致）。
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
# replay_trace 还依赖 assets/ 下的 rec_assert / rec_visual / rec_session。
_ASSETS = _SCRIPTS.parent / "assets"
if str(_ASSETS) not in sys.path:
    sys.path.insert(0, str(_ASSETS))

import trace_schema as _ts  # noqa: E402

mcp = FastMCP("web-record")


@mcp.tool(
    name="validate_trace",
    description=(
        "Validate a web-record success trace (v2). Input: path to a trace.json. Returns "
        "ok plus a list of structural problems (schema/entry/node-chain). A trace can be "
        "loadable yet structurally broken, so validate before feeding it downstream."
    ),
)
def validate_trace_tool(trace_path: str) -> str:
    path = Path(trace_path)
    if not path.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {trace_path}"},
                          ensure_ascii=False)
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
        meta = _ts.check_schema(trace)
        problems: list[str] = []
        ids = _ts.node_ids(trace)
        entry = meta.get("entry")
        if not ids and entry is not None:
            problems.append("empty trace must have a null $meta.entry")
        if ids and entry not in ids:
            problems.append(f"$meta.entry does not name a step: {entry!r}")
        payload = {
            "ok": True,
            "path": str(path),
            "problems": problems,
            "valid": len(problems) == 0,
            "schema": meta.get("schema"),
            "step_count": len(ids),
        }
    except _ts.TraceFormatError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool(
    name="convert_desktop_to_v2",
    description=(
        "Convert an edr-wd desktop golden trace into a MaaFramework-shaped v2 node table. "
        "Input: path to a golden-trace.json, or a recording directory containing one; "
        "optional output path. Returns the converted v2 dict (node ids, meta, step count) "
        "and, when out_path is given, writes it there. This is the mandatory gate before "
        "feeding an edr-wd trace to maa-fw — a raw v1 trace silently becomes DirectHit/"
        "DoNothing nodes under maa-fw."
    ),
)
def convert_desktop_to_v2(golden: str, out_path: str | None = None) -> str:
    from desktop_to_v2 import convert as _convert

    src = Path(golden)
    if src.is_dir():
        candidate = src / "golden-trace.json"
        if not candidate.is_file():
            return json.dumps({"ok": False, "error": f"no golden-trace.json in {src}"},
                              ensure_ascii=False)
        src = candidate
    if not src.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {golden}"}, ensure_ascii=False)
    try:
        desktop = json.loads(src.read_text(encoding="utf-8"))
        nodes = _convert(desktop)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)

    destination = Path(out_path) if out_path else src.parent / "trace.json"
    try:
        if out_path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "ok": False,
            "error": f"converted but write failed: {exc}",
            "node_ids": list(nodes),
            "has_meta": "$meta" in nodes,
        }, ensure_ascii=False)

    return json.dumps({
        "ok": True,
        "source": str(src),
        "out": str(destination),
        "written": bool(out_path),
        "step_count": len([k for k in nodes if not k.startswith("$")]),
        "has_meta": "$meta" in nodes,
        "node_ids": list(nodes),
    }, ensure_ascii=False)


@mcp.tool(
    name="contract_check",
    description=(
        "Check a v2 trace against the node-contract maa-fw accepts (charset, strict attach "
        "keys, skip rule, next-chain shape). Input: path to a trace.json. Returns the list "
        "of contract problems; empty means maa-fw will interpret the nodes as intended "
        "rather than loading them as inert DirectHit/DoNothing."
    ),
)
def contract_check(trace_path: str) -> str:
    from maa_contract import contract_problems

    path = Path(trace_path)
    if not path.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {trace_path}"},
                          ensure_ascii=False)
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
        problems = contract_problems(trace)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "path": str(path),
        "problems": problems,
        "valid_for_maa": len(problems) == 0,
    }, ensure_ascii=False)


@mcp.tool(
    name="replay_check",
    description=(
        "Read-only replay readiness gate for a web-record success trace (v2) — the same "
        "criteria as `replay_trace --check` (format / acyclic / entry / supported actions / "
        "maa contract + $meta status == ready). Returns verdict READY, NOT READY (incomplete; "
        "missing templates/selectors) or NOT REPLAYABLE (non-v2 / contract damaged) with an "
        "actionable reason. Does not replay — that needs a browser and login."
    ),
)
def replay_check(trace_path: str) -> str:
    from replay_trace import TraceReplayError, validate_trace as _validate_trace

    path = Path(trace_path)
    if not path.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {trace_path}"},
                          ensure_ascii=False)
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "ok": True, "path": str(path), "replayable": False,
            "verdict": "NOT REPLAYABLE",
            "reason": f"trace.json 不是合法 JSON 或读不出：{exc}", "node_count": 0,
        }, ensure_ascii=False)
    try:
        order = _validate_trace(trace)
    except TraceReplayError as exc:
        # validate_trace 对旧 v1 已带「重录 / 转 v2」的可操作提示，原样透传。
        return json.dumps({
            "ok": True, "path": str(path), "replayable": False,
            "verdict": "NOT REPLAYABLE", "reason": str(exc), "node_count": 0,
        }, ensure_ascii=False)
    status = _ts.meta(trace).get("status")
    if status != "ready":
        n_missing = sum(
            1 for node_id in order
            if _ts.provenance(trace[node_id]).get("status") == "missing_template"
        )
        return json.dumps({
            "ok": True, "path": str(path), "replayable": False,
            "verdict": "NOT READY",
            "reason": (f"$meta.status={status!r}：{n_missing} 个定位步骤缺模板/选择器。"
                       "先用 record.py 补录或补齐模板再回放。"),
            "node_count": len(order),
        }, ensure_ascii=False)
    return json.dumps({
        "ok": True, "path": str(path), "replayable": True,
        "verdict": "READY", "reason": "", "node_count": len(order),
    }, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="web-record MCP server")
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
