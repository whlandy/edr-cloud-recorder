import json
import os
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page


CASE_DIR = Path(__file__).parent
RECORDER_HOME = Path(
    os.environ.get("RECORDER_HOME", Path(__file__).resolve().parents[2])
)
EDR_WD_HOME = Path(
    os.environ.get("EDR_WD_HOME", Path.home() / "ai-projects/edr-wd")
)
for path in (EDR_WD_HOME, RECORDER_HOME / "scripts", RECORDER_HOME / "orchestrate"):
    sys.path.insert(0, str(path))

from agent.mcp_manager import call_mcp_tool, initialize, unwrap_tool_result
from replay_trace import evaluate_trace, load_trace, replay_trace
from scenario import Scenario


def test_tiangong_cloud_and_hisec_endpoint(authed_page: Page):
    state = {}
    golden = load_trace(CASE_DIR / "trace.json")

    def replay_cloud():
        execution = replay_trace(
            authed_page,
            golden,
            template_root=CASE_DIR,
            targeting="visual_only",
            execution_path=CASE_DIR / "cloud.execution.json",
            raise_on_error=True,
        )
        state["cloudReport"] = evaluate_trace(golden, execution)
        if not state["cloudReport"]["taskSuccess"]:
            raise AssertionError("云侧成功轨迹回放失败")
        return f"视觉轨迹评分 {state['cloudReport']['score']:.0f}"

    def open_endpoint_logs():
        target = os.environ.get("EDR_WD_TARGET", "mac-77")
        process = os.environ.get("EDR_ENDPOINT_PROCESS", "HiSecEndpoint")
        main_title = os.environ.get("EDR_ENDPOINT_MAIN_WINDOW", "HiSec Endpoint")
        result_title = os.environ.get("EDR_ENDPOINT_RESULT_WINDOW", "日志中心")
        session = initialize(target)
        if not session.get("ok"):
            raise AssertionError(f"edr-wd 初始化失败: {session.get('error')}")
        connection = session["data"]

        def call(tool, arguments):
            return unwrap_tool_result(call_mcp_tool(
                connection["session_id"],
                connection["mcp_url"],
                tool,
                arguments,
                timeout=20,
            ))

        observed = call("list_windows", {})
        windows = observed.get("windows", []) if isinstance(observed, dict) else []
        result_matches = [
            window for window in windows
            if window.get("app_name") == process and window.get("title") == result_title
        ]
        click = None
        activation = None
        if len(result_matches) != 1:
            main_matches = [
                window for window in windows
                if window.get("app_name") == process and window.get("title") == main_title
            ]
            if len(main_matches) != 1:
                raise AssertionError(f"端侧主窗口 {main_title!r} 匹配 {len(main_matches)} 个")
            main = main_matches[0]
            rectangle = main.get("rectangle") or {}
            if not rectangle.get("w") or not rectangle.get("h"):
                raise AssertionError("端侧主窗口缺少可点击矩形")

            connected = call("connect", {
                "process_name": process,
                "title_re": f"^{main_title}$",
            })
            if not connected.get("ok"):
                raise AssertionError(f"连接端侧主窗口失败: {connected}")
            call("unlock_window", {})
            activation = call("activate_app", {"app_name": process})
            if not activation.get("ok"):
                raise AssertionError(f"激活端侧主窗口失败: {activation}")

            click = call("click_at", {
                "x": round(rectangle["x"] + rectangle["w"] * 0.112),
                "y": round(rectangle["y"] + rectangle["h"] * 0.748),
                "expected_process_name": process,
            })
            if not click.get("ok") or click.get("event_dispatched") is False:
                raise AssertionError(f"点击日志中心失败: {click}")
            opened = call("wait_window", {
                "process_name": process,
                "title_re": f"^{result_title}$",
                "timeout": 5,
            })
            result_matches = opened.get("windows", []) if opened.get("found") else []

        if len(result_matches) != 1:
            raise AssertionError(f"端侧窗口 {result_title!r} 匹配 {len(result_matches)} 个")
        window = result_matches[0]
        state["endpoint"] = {
            "driver": "edr-wd",
            "activation": activation,
            "click": click,
            "window": {
                key: window.get(key)
                for key in ("title", "class_name", "process_id", "rectangle")
                if window.get(key) is not None
            },
        }
        return f"edr-wd 已打开端侧窗口 {result_title}"

    scenario = Scenario("天工云侧统计 + HiSec 端侧日志中心")
    scenario.cloud("回放天工统计成功轨迹", replay_cloud)
    scenario.endpoint("激活 HiSec 并打开日志中心", open_endpoint_logs)
    passed = scenario.run()

    combined = scenario.execution(state)
    combined["name"] = "tiangong-hisec-readonly"
    combined["finishedAt"] = datetime.now().isoformat()
    (CASE_DIR / "execution.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert passed
