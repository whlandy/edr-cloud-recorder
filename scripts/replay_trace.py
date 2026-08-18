"""Replay a golden E2E trace and evaluate the resulting agent execution trace."""

from __future__ import annotations

import base64
import json
import os
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from rec_assert import (
    LOCALTIME_KIND, ANY_STR, assert_subset, expect_local_time, local_time_value,
)
from rec_secrets import REDACTED
from rec_visual import (
    VisualAbsent, VisualAmbiguous, VisualMatchError, locate_visual_target,
)
from selector_py import to_python


TRACE_SCHEMA = "edr.success-trace/v1"
EXECUTION_SCHEMA = "edr.execution-trace/v1"
SUPPORTED_ACTIONS = {
    "Click", "DoubleClick", "InputText", "Check", "Uncheck",
    "SetSwitch", "PressKey", "Assert",
}


class TraceReplayError(RuntimeError):
    pass


def load_trace(path: str | Path) -> dict:
    trace = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_trace(trace)
    return trace


def validate_trace(trace: dict) -> list[str]:
    if trace.get("schema") != TRACE_SCHEMA:
        raise TraceReplayError(f"不支持的 trace schema: {trace.get('schema')!r}")
    steps = trace.get("steps")
    if not isinstance(steps, dict):
        raise TraceReplayError("trace.steps 必须是对象")
    entry = trace.get("entry")
    if not steps:
        if entry is not None:
            raise TraceReplayError("空 trace 的 entry 必须是 null")
        return []
    if entry not in steps:
        raise TraceReplayError(f"trace.entry 不存在: {entry!r}")

    order, seen, current = [], set(), entry
    while current is not None:
        if current in seen:
            raise TraceReplayError(f"trace 存在环: {current}")
        if current not in steps:
            raise TraceReplayError(f"next 指向不存在的步骤: {current}")
        seen.add(current)
        order.append(current)
        action = (steps[current].get("action") or {}).get("type")
        if action not in SUPPORTED_ACTIONS:
            raise TraceReplayError(f"{current} 使用不支持的动作: {action!r}")
        current = steps[current].get("next")
    unreachable = set(steps) - seen
    if unreachable:
        raise TraceReplayError(f"trace 包含不可达步骤: {sorted(unreachable)}")
    return order


def _locator(page, selector: dict):
    source = selector.get("sel")
    if not source:
        return None
    root = page
    if selector.get("inFrame") and selector.get("framePath"):
        tail = selector["framePath"].split("/")[-1]
        root = page.frame_locator(f'iframe[src*="{tail}"]')
    expression = "root." + to_python(source)
    return eval(expression, {"__builtins__": {}}, {"root": root})


def _visual_ui(node: dict) -> dict:
    recognition = node.get("recognition") or {}
    relative = (node.get("action") or {}).get("param", {}).get("relativePoint") or {}
    return {
        **(node.get("geometry") or {}),
        "templates": recognition.get("templates") or {},
        "click": {"rx": relative.get("x", 0.5), "ry": relative.get("y", 0.5)},
    }


def _target(page, node: dict, template_root: Path, targeting: str, timeout_ms: int,
            focused_selector: str | None):
    action_type = (node.get("action") or {}).get("type")
    if action_type == "Assert":
        locator = _locator(page, node.get("selector") or {})
        if locator is None:
            raise TraceReplayError("断言步骤缺少 DOM 选择器")
        return "verifier", locator, None, None
    if action_type == "PressKey" and targeting == "visual_only":
        selector = (node.get("selector") or {}).get("sel")
        if not selector or selector != focused_selector:
            raise TraceReplayError("视觉按键步骤没有同目标动作建立的可信焦点")
        return "keyboard", None, None, None

    locator = None
    dom_error = None
    if targeting != "visual_only":
        locator = _locator(page, node.get("selector") or {})
    if locator is not None:
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return "dom", locator, None, None
        except Exception as error:
            # DOM 的失败原因必须留下来。以前这里直接吞掉回退视觉，
            # 于是执行记录里只剩「视觉匹配分数不足」—— 分不清是选择器写错了、
            # 元素本来就不该出现、还是整个页面根本不对（会话超时最典型）。
            dom_error = f"{type(error).__name__}: {error}"
            if not node.get("recognition"):
                raise
    if not node.get("recognition"):
        raise TraceReplayError("DOM 定位失败且步骤没有视觉模板")
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            visual = locate_visual_target(
                page, template_root=template_root, ui=_visual_ui(node)
            )
            break
        except VisualMatchError as visual_error:
            if time.monotonic() >= deadline:
                # 两条路都失败时，DOM 的原因最有价值 —— 偏偏这时候它最容易丢：
                # 异常在 target_info 构造之前就抛了，之前那版只在**视觉成功**时
                # 才记得下 domError，等于在最需要它的场合没有它。
                # 用同一个异常类重抛，保住 VisualAbsent / VisualAmbiguous 的区分。
                if dom_error:
                    raise type(visual_error)(
                        f"{visual_error}\nDOM 先失败于：{dom_error}"
                    ) from visual_error
                raise
            time.sleep(0.1)
    return "visual", None, visual, dom_error



def _target_absent(page, node: dict, error: Exception, targeting: str = "dom_first") -> bool:
    """判断这一步的目标是不是**真的不在页面上**。

    只有拿得出缺席证据时才返回 True。判据按可靠性排：

      1. VisualAmbiguous            —— 「存在」的直接证据，一票否决缺席
      2. DOM 命中数 > 0             —— 元素在，那就不是缺席（点不动是另一回事）
      3. DOM 命中数为 0             —— 只在没有存在证据时才当缺席
      4. 没有 DOM 依据时            —— 只认 VisualAbsent（分数不足）

    注意「点击超时」不能当缺席：那说明元素在、只是被挡住了，必须失败。

    visual_only 模式下**不看 DOM**。那个模式的全部意义就是隔离 DOM 知识、
    只评视觉定位能力；用 DOM 判缺席会让评测结果被 DOM 信息污染，
    而且那一步的视觉匹配根本没被执行过。
    """
    # 「存在」的证据优先于「查不到」。
    #
    # VisualAmbiguous 是**直接证据**：屏幕上有好几个长得很像的候选，目标显然在。
    # 而 DOM 命中数为 0 只说明**那条选择器**失效了 —— 页面结构和录制时不同、
    # 或者选择器本来就撞车，都会命中 0，它证明不了元素不在。
    #
    # 实测栽过：弹窗明明在（视觉 best=0.976），但 CSS 路径匹配不到 → DOM 命中 0
    # → 判缺席 → 跳过 → 遮罩没关掉，下一步点击被吞，20 秒超时。
    if isinstance(error, VisualAmbiguous):
        return False

    selector = node.get("selector") or {}
    if targeting != "visual_only" and selector.get("sel"):
        try:
            locator = _locator(page, selector)
            if locator is not None:
                return locator.count() == 0
        except Exception:
            pass    # 选择器都算不出来，退回看视觉证据
    return isinstance(error, VisualAbsent)


# 按**路径段**匹配，不是子串。子串匹配会把业务路径误判成认证页：
# /user/authorization 含 "auth"、/sessions/42 含 "session" —— 于是会话完全有效
# 却被判「登录态失效」并中断回放，和这个检查本来要消灭的误导性报错是同一类。
# 段匹配下 "authorization" != "auth"、"sessions" != "session"，
# 而 Keycloak 那种 /auth/realms/... 仍然认得出来。
AUTH_URL_SEGMENTS = frozenset({
    "login", "signin", "sign-in", "sso", "oauth", "auth", "logout", "session",
})


def _assert_landed(page, start_url: str, execution: dict) -> None:
    """确认真的落在了 trace 的起点，而不是被踢到登录页。

    为什么值得单独一步：登录态失效时，页面会被重定向到一个只有几个 div 的
    会话超时页。于是**每一个**步骤的 DOM 定位都找不到元素，回退到视觉又在
    那张陌生的页面上匹配不到模板，最终报出来的是「视觉匹配分数不足」——
    整条链路没有一处提到「你没登录」。

    实测被这个坑带偏过一整轮：据此去查选择器和视觉阈值，而真正的原因是会话超时。
    与其让它伪装成 9 个定位失败，不如在第一步就说清楚。

    只有重定向到**像认证页**的地址才判失败；其他重定向（尾斜杠、语言前缀）
    只记一条警告，不打断回放 —— 免得对正常跳转的站点误报。
    """
    # 拿不到当前地址就不做判断。这个检查是**附加的诊断**，
    # 不能因为它自己取不到值就把回放打断。
    landed = getattr(page, "url", None)
    if not isinstance(landed, str) or not landed:
        return
    if urlsplit(landed).path == urlsplit(start_url).path:
        return
    segments = {seg for seg in urlsplit(landed).path.lower().split("/") if seg}
    # login.action / signin.jsp 这类带后缀的入口也要认出来
    stems = {seg.split(".")[0] for seg in segments}
    if (segments | stems) & AUTH_URL_SEGMENTS:
        raise TraceReplayError(
            f"回放起点被重定向到 {landed}\n"
            f"（起点应为 {start_url}）\n"
            "最常见的原因是登录态失效。先刷新登录态再回放 —— "
            "否则后续每一步都会以「找不到元素 / 视觉匹配不足」的形式失败，"
            "而那些报错都指不到真正的原因。"
        )
    execution.setdefault("warnings", []).append(
        f"起点发生重定向：{start_url} → {landed}"
    )


def _response_predicate(expected: dict, occurrence: int):
    seen = [0]
    expected_url = urlsplit(expected["url"])

    def predicate(response):
        actual = urlsplit(response.url)
        same_url = (
            response.url == expected["url"]
            or (actual.path, actual.query) == (expected_url.path, expected_url.query)
        )
        if response.request.method == expected["method"] and same_url:
            seen[0] += 1
            return seen[0] == occurrence
        return False

    return predicate


def _response_body(response):
    try:
        return response.text()
    except Exception:
        return ""


def _request_body(request):
    try:
        value = request.post_data_json
        return value() if callable(value) else value
    except Exception:
        return request.post_data


def _request_body_base64(request) -> str:
    value = request.post_data_buffer
    data = value() if callable(value) else value
    return base64.b64encode(data or b"").decode("ascii")


def _redacted_matchers(value):
    if value == REDACTED:
        return ANY_STR
    if isinstance(value, dict):
        return {key: _redacted_matchers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_matchers(item) for item in value]
    return value


def _validate_response(response, expected: dict) -> dict:
    actual = {
        "method": response.request.method,
        "url": response.url,
        "status": response.status,
        "ok": False,
    }
    if response.status != expected["expectedStatus"]:
        raise AssertionError(
            f"响应状态不符: {response.status} != {expected['expectedStatus']}"
        )
    expected_request = expected.get("request") or {}
    if "body" in expected_request:
        assert_subset(
            _request_body(response.request),
            _redacted_matchers(expected_request["body"]),
        )
    if "bodyBase64" in expected_request:
        actual_body = _request_body_base64(response.request)
        if actual_body != expected_request["bodyBase64"]:
            raise AssertionError("二进制请求体与成功 trace 不一致")
    if "expectedBody" in expected:
        actual_body, expected_body = _response_body(response), expected["expectedBody"]
        try:
            assert_subset(
                json.loads(actual_body),
                _redacted_matchers(json.loads(expected_body)),
            )
        except (TypeError, ValueError):
            if REDACTED not in expected_body and actual_body != expected_body:
                # 把两边都摘一段出来。只说「不一致」等于让人再跑一遍去抓包 ——
                # 而这类差异往往就是一个字段（msg 文案、多出来的 data），
                # 看一眼就知道该不该改断言。
                raise AssertionError(
                    "响应体与成功 trace 不一致\n"
                    f"  期望: {expected_body[:200]}\n"
                    f"  实际: {actual_body[:200]}"
                )
    actual["ok"] = True
    return actual


def _assert_locator(locator, param: dict, timeout_ms: int) -> None:
    assertion, expected = param.get("assertion"), param.get("expected")
    dynamic = param.get("expectedFrom") or {}
    if dynamic.get("kind") == LOCALTIME_KIND:
        # 期望值由回放此刻的时钟决定。轮询里每次重算 —— 页面可能几秒后才刷新出
        # 新时间，拿一个固定下来的期望值去等，等到的是过时的比较基准。
        expect_local_time(
            locator, dynamic.get("format") or "%H:%M",
            match=dynamic.get("match") or "contains",
            # 输入框的时间在 value 上，inner_text 恒为空
            read="value" if assertion == "value" else "text",
            timeout=timeout_ms / 1000,
        )
        return
    readers = {
        "visible": lambda: locator.is_visible(),
        "text": lambda: locator.inner_text(),
        "value": lambda: locator.input_value(),
        "checked": lambda: locator.is_checked(),
        "attribute": lambda: locator.get_attribute(param.get("attribute")),
    }
    if assertion not in readers:
        raise TraceReplayError(f"不支持的断言: {assertion!r}")

    deadline = time.monotonic() + timeout_ms / 1000
    actual = None
    while True:
        try:
            actual = readers[assertion]()
            matches = actual == expected
            if assertion == "text" and isinstance(actual, str) and isinstance(expected, str):
                matches = " ".join(actual.split()) == " ".join(expected.split())
            if matches:
                return
        except Exception as error:
            actual = f"{type(error).__name__}: {error}"
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"断言失败: {assertion} actual={actual!r} expected={expected!r}"
            )
        time.sleep(0.1)


def _switch_reader(locator, via: dict):
    state_target = locator.locator(via["within"]).first if via.get("within") else locator
    if via.get("type") == "checked":
        return state_target.is_checked
    if via.get("type") == "class":
        token = via.get("token")
        def read_class():
            present = state_target.evaluate(
                "(e, token) => e.classList.contains(token)", token
            )
            return not present if via.get("polarity") == "off" else present
        return read_class
    return lambda: state_target.get_attribute("aria-checked") == "true"


def _set_switch(locator, param: dict, timeout_ms: int) -> bool:
    """拨到指定状态。返回是否真的点了 —— 已经在目标状态就什么都不做。"""
    desired = bool(param.get("state"))
    via = param.get("via") or {}
    read_state = _switch_reader(locator, via)
    if read_state() == desired:
        return False
    locator.click()
    # 需要二次确认的开关，class 要等后面那一下「确认」才变。堵在这里等状态
    # 到达，等的东西永远不会来 —— 确认按钮就在后续步骤里。录制时已经看到
    # 「状态变化之前还录了别的步骤」，那就点完即走，由后续步骤把它落地。
    if via.get("gated"):
        return True
    deadline = time.monotonic() + timeout_ms / 1000
    while read_state() != desired:
        if time.monotonic() >= deadline:
            raise AssertionError(f"开关未到达目标状态: expected={desired}")
        time.sleep(0.1)
    return True


def _execute_action(page, node: dict, template_root: Path, targeting: str,
                    timeout_ms: int, env: dict,
                    focused_selector: str | None) -> dict:
    action = node["action"]
    action_type, param = action["type"], action.get("param") or {}
    mode, locator, visual, dom_error = _target(
        page, node, template_root, targeting, timeout_ms, focused_selector
    )
    target_info = {"mode": mode}
    if dom_error:
        # 回退到视觉时，把 DOM 为什么失败一并留在执行记录里
        target_info["domError"] = dom_error
    if visual is not None:
        target_info.update(
            templateKind=visual.kind,
            matchScore=visual.match.score,
            verifyScore=visual.match.verify_score,
            scale=visual.match.scale,
            point={"x": visual.x, "y": visual.y},
        )

    if action_type == "Assert":
        _assert_locator(locator, param, timeout_ms)
    elif action_type == "PressKey":
        (locator or page.keyboard).press(param.get("key", "Enter"))
    elif action_type == "InputText":
        source = param.get("valueFromEnv")
        dynamic_value = param.get("valueFrom") or {}
        if source:
            text = env.get(source)
            if not text:
                raise TraceReplayError(f"缺少输入所需的环境变量 {source}")
        elif dynamic_value.get("kind") == LOCALTIME_KIND:
            text = local_time_value(
                dynamic_value.get("format") or "%Y-%m-%d",
                offset_days=int(dynamic_value.get("offsetDays") or 0),
            )
        else:
            text = param.get("text", "")
        if locator is not None:
            locator.fill(text)
        else:
            page.mouse.click(visual.x, visual.y)
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.insert_text(text)
    elif action_type == "SetSwitch" and locator is not None:
        _set_switch(locator, param, timeout_ms)
    elif action_type in {"Click", "DoubleClick", "Check", "Uncheck", "SetSwitch"}:
        if locator is not None:
            method = {
                "Click": "click", "DoubleClick": "dblclick",
                "Check": "check", "Uncheck": "uncheck", "SetSwitch": "click",
            }[action_type]
            getattr(locator, method)()
        else:
            kwargs = {"click_count": 2} if action_type == "DoubleClick" else {}
            page.mouse.click(visual.x, visual.y, **kwargs)
    else:
        raise TraceReplayError(f"不支持的动作: {action_type}")
    return target_info


POINTER_INTERCEPT = "intercepts pointer events"


def _intercepted(error: Exception) -> bool:
    """这一步是「找到了但点不动」——有别的元素挡在上面，不是找不到。"""
    return POINTER_INTERCEPT in str(error)


def _perform(page, node: dict, root: Path, targeting: str, timeout_ms: int,
             env: dict, focused_selector: str | None) -> tuple[dict, list]:
    """执行一步，并校验它应当触发的响应。返回 (target, responses)。"""
    responses: list = []
    optional_waiters = []
    try:
        expected_responses = (node.get("expect") or {}).get("responses") or []
        occurrences: dict[tuple[str, str], int] = {}
        # 必发的（写请求）用 ExitStack 等到底；非必发的（读请求）单独接住超时。
        #
        # 读请求只在状态真的变化时才重发：回放时如果页面已经处于目标状态，
        # 同样的点击一个包都不发 —— 那一步其实是成功的，不该因此判失败。
        required = [e for e in expected_responses if e.get("required", True)]
        optional = [e for e in expected_responses if not e.get("required", True)]
        with ExitStack() as stack:
            response_infos = []
            for expected in required:
                key = (expected["method"], expected["url"])
                occurrences[key] = occurrences.get(key, 0) + 1
                response_infos.append(stack.enter_context(page.expect_response(
                    _response_predicate(expected, occurrences[key]), timeout=timeout_ms
                )))
            # 非必发的也必须在动作**之前**布置监听，否则请求会漏掉；
            # 只是退出时单独处理，不让它的超时打断整步。
            for expected in optional:
                key = (expected["method"], expected["url"])
                occurrences[key] = occurrences.get(key, 0) + 1
                waiter = page.expect_response(
                    _response_predicate(expected, occurrences[key]),
                    timeout=min(timeout_ms, 2_000),
                )
                optional_waiters.append((waiter, waiter.__enter__(), expected))
            target = _execute_action(
                page, node, root, targeting, timeout_ms, env, focused_selector,
            )
        for info, expected in zip(response_infos, required):
            responses.append(_validate_response(info.value, expected))
        return target, responses
    finally:
        # 非必发响应的收尾必须在 finally 里。放在正常路径上的话，动作一旦
        # 抛异常就直接跳出 with 块，这些等待器永远不会被退出 —— 实测留下
        # 一串 "Future exception was never retrieved"，既是噪音也是泄漏。
        for waiter, info, expected in optional_waiters:
            try:
                waiter.__exit__(None, None, None)
                responses.append(_validate_response(info.value, expected))
            except Exception as missing:
                responses.append({
                    "method": expected["method"], "url": expected["url"],
                    "ok": False, "required": False,
                    "note": f"未出现（非必发）：{type(missing).__name__}",
                })


def replay_trace(page, trace: dict | str | Path, *, template_root: str | Path | None = None,
                 targeting: str = "dom_first", timeout_ms: int = 5_000,
                 env: dict | None = None, execution_path: str | Path | None = None,
                 navigate: bool = True, raise_on_error: bool = False) -> dict:
    if targeting not in {"dom_first", "visual_only"}:
        raise ValueError("targeting 必须是 dom_first 或 visual_only")
    trace_path = Path(trace) if isinstance(trace, (str, Path)) else None
    golden = load_trace(trace_path) if trace_path else trace
    order = validate_trace(golden)
    if golden.get("status") != "ready":
        raise TraceReplayError("trace 尚未 ready，存在缺失模板的定位步骤")
    root = Path(template_root) if template_root else (
        trace_path.parent if trace_path else Path.cwd()
    )
    execution = {
        "schema": EXECUTION_SCHEMA,
        "goldenSchema": golden["schema"],
        "name": golden.get("name"),
        "startedAt": datetime.now().isoformat(),
        "status": "running",
        "steps": [],
    }
    runtime_env = os.environ if env is None else env
    if navigate and golden.get("startUrl"):
        try:
            page.goto(golden["startUrl"])
        except Exception as error:
            raise TraceReplayError(f"无法进入 trace 起始页面: {error}") from error
        _assert_landed(page, golden["startUrl"], execution)
    first_error = None
    focused_selector = None
    # 被跳过的 optional 步骤：目标当时不在，但可能只是还没出现
    pending_optional: list[tuple[str, dict]] = []
    # 因为挡路而被提前做掉的关浮层步骤：轨迹走到它时会发现已经没得关
    performed_early: set[str] = set()
    for node_id in order:
        node = golden["steps"][node_id]
        started = time.perf_counter()
        result = {
            "nodeId": node_id,
            "expectedAction": node["action"]["type"],
            "actualAction": node["action"]["type"],
            "status": "running",
            "responses": [],
            "retries": 0,
        }
        try:
            result["target"], result["responses"] = _perform(
                page, node, root, targeting, timeout_ms, runtime_env, focused_selector,
            )
            result["status"] = "success"
            if targeting == "visual_only" and result["target"]["mode"] == "visual":
                focused_selector = (node.get("selector") or {}).get("sel")
        except Exception as error:
            # 「找到了但点不动」是单独一类：有东西挡在上面。
            #
            # 最常见的成因是**晚出现的浮层**：首启弹窗在页面加载好几秒后才弹，
            # 而轨迹里关它的那一步排在最前面 —— 回放走到那一步时它还没出现，
            # 于是按 optional 跳过；几秒后它弹了出来，遮罩把后面每一次点击都吞掉，
            # 最后报的是一堆「点击超时」，看不出根因。
            #
            # 所以跳过 optional 步骤只是**暂缓**，不是就此作废：真被挡住了，
            # 就把这些暂缓的步骤补做一遍，再重试当前这一步。补做和重试都记在
            # retries 里，效率分照扣 —— 多做的动作就该算多做。
            if _intercepted(error):
                recovered = []
                while pending_optional:
                    skipped_id, skipped_result = pending_optional.pop(0)
                    try:
                        _perform(page, golden["steps"][skipped_id], root, targeting,
                                 timeout_ms, runtime_env, focused_selector)
                    except Exception:
                        continue                    # 补做也不成，按原样报错
                    skipped_result["laterPerformed"] = True
                    recovered.append(skipped_id)
                # 关浮层的那一步也可能排在**后面**：录制时弹窗是在这一下之后
                # 才出现的，回放时它提前出现了。挡路的东西和关它的那一步就这么
                # 错开了 —— 那就把它提前做掉。录制时观察到「点完浮层就没了」的
                # 步骤才有这个资格，不是随便挑一步来试。
                for later_id in order[order.index(node_id) + 1:]:
                    if later_id in performed_early:
                        continue
                    if not golden["steps"][later_id].get("dismissesOverlay"):
                        continue
                    try:
                        _perform(page, golden["steps"][later_id], root, targeting,
                                 timeout_ms, runtime_env, focused_selector)
                    except Exception:
                        continue
                    performed_early.add(later_id)
                    recovered.append(later_id)
                    break                           # 一次只提前一步，别越做越多
                if recovered:
                    result["retries"] += 1
                    result["recoveredOptional"] = recovered
                    try:
                        result["target"], result["responses"] = _perform(
                            page, node, root, targeting, timeout_ms, runtime_env,
                            focused_selector,
                        )
                        result["status"] = "success"
                    except Exception as retry_error:
                        error = retry_error
            # 可选步骤：**确认目标不存在**时才跳过。
            #
            # 首启弹窗、提示条这类元素出现与否取决于账号状态和历史操作 ——
            # 录制时出现过，回放时往往已经不再出现（第一次关掉后应用记住了）。
            #
            # 但「找不到」和「找到了却用不了」必须分开。第一版把两者混成一类，
            # 结果实测栽了：弹窗**确实在**（视觉 best=0.995），只是因为页面上有
            # 两个几乎一样的关闭图标而被判为歧义 —— 于是这一步被跳过，弹窗没关掉，
            # 遮罩把后面每一次点击都吞了，最后报的是「点击超时」。
            #
            # 测试里把失败当跳过，等于把问题往后推，而且推到一个报错不知所云的地方。
            if result["status"] == "success":
                pass                                # 补做浮层之后重试成功了
            elif node.get("optional") and _target_absent(page, node, error, targeting):
                result["status"] = "skipped"
                result["error"] = f"{type(error).__name__}: {error}"
                result["skippedReason"] = (
                    "已提前执行（用于解除遮挡）" if node_id in performed_early
                    else "optional 步骤未出现"
                )
                if node_id in performed_early:
                    result["performedEarly"] = True
                else:
                    # 只是暂缓：它可能只是还没出现。后面某一步被挡住时会回来补做。
                    pending_optional.append((node_id, result))
            else:
                result["status"] = "failed"
                result["error"] = f"{type(error).__name__}: {error}"
                first_error = error
        finally:
            result["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
            execution["steps"].append(result)
        if first_error:
            break
    execution["status"] = "failed" if first_error else "success"
    execution["finishedAt"] = datetime.now().isoformat()
    if execution_path:
        Path(execution_path).write_text(
            json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if first_error and raise_on_error:
        raise TraceReplayError(str(first_error)) from first_error
    return execution


def evaluate_trace(golden: dict, execution: dict) -> dict:
    order = validate_trace(golden)
    if execution.get("schema") != EXECUTION_SCHEMA:
        raise TraceReplayError(
            f"不支持的 execution schema: {execution.get('schema')!r}"
        )
    if execution.get("goldenSchema") != golden["schema"]:
        raise TraceReplayError("execution trace 与黄金 trace schema 不一致")
    execution_steps = execution.get("steps")
    if not isinstance(execution_steps, list):
        raise TraceReplayError("execution.steps 必须是数组")

    actual = {}
    for step in execution_steps:
        if not isinstance(step, dict) or not step.get("nodeId"):
            raise TraceReplayError("execution step 缺少 nodeId")
        retries = step.get("retries", 0)
        if type(retries) is not int or retries < 0:
            raise TraceReplayError("execution step 的 retries 必须是非负整数")
        actual.setdefault(step["nodeId"], step)
    # 被正确跳过的 optional 步骤算「已满足」。
    #
    # 它的要求本来就是「出现了才做」，没出现而跳过就是按要求执行了。
    # 只认 success 的话，任何含 optional 步骤的轨迹都永远拿不到 taskSuccess ——
    # 实测：可选弹窗未出现、其余全成功，仍报 taskSuccess=False、score=45。
    #
    # 只对**golden 里标了 optional** 的节点放行；执行侧自称 skipped 不算数，
    # 否则回放器将来多一种跳过路径就能悄悄抬高分数。
    def _satisfied(node_id: str) -> bool:
        step = actual.get(node_id, {})
        if step.get("status") == "success":
            return True
        return (
            step.get("status") == "skipped"
            and bool(golden["steps"][node_id].get("optional"))
        )

    completed = sum(_satisfied(node) for node in order)
    action_hits = sum(
        actual.get(node, {}).get("actualAction") == golden["steps"][node]["action"]["type"]
        for node in order if node in actual
    )
    # 只把**必发**的响应计入分母。非必发的（读请求）在页面已处于目标状态时
    # 本来就不会重发，把它算进分母等于为一件正常的事扣分；
    # 但它真的出现时仍然会被校验，出错照样算失败。
    def _required(node_id: str) -> list:
        expects = (golden["steps"][node_id].get("expect") or {}).get("responses") or []
        return [e for e in expects if e.get("required", True)]

    expected_network = sum(len(_required(node)) for node in order)
    network_hits = 0
    for node in order:
        expected_count = len(_required(node))
        responses = actual.get(node, {}).get("responses", [])
        hits = sum(bool(item.get("ok")) for item in responses if item.get("required", True))
        network_hits += min(expected_count, hits)
    visual_scores = [
        step["target"]["matchScore"]
        for step in execution_steps
        if step.get("target", {}).get("mode") == "visual"
    ]
    total = len(order)
    observed_path = [
        step["nodeId"]
        for step in execution_steps
        if step["nodeId"] in golden["steps"]
    ]
    order_hits = sum(
        expected == observed for expected, observed in zip(order, observed_path)
    )
    order_rate = (
        order_hits / max(total, len(observed_path))
        if total or observed_path else 1.0
    )
    extra_actions = max(0, len(execution_steps) - total)
    retries = sum(step.get("retries", 0) for step in execution_steps)
    efficiency = (
        total / (total + extra_actions + retries)
        if total else float(not execution_steps)
    )
    completion_rate = completed / total if total else 1.0
    action_accuracy = action_hits / total if total else 1.0
    network_rate = network_hits / expected_network if expected_network else 1.0
    task_success = (
        execution.get("status") == "success"
        and completed == total
        and observed_path == order
        and (bool(total) or not execution_steps)
    )
    score = 100 * (
        0.45 * float(task_success)
        + 0.20 * completion_rate
        + 0.10 * action_accuracy
        + 0.10 * network_rate
        + 0.10 * order_rate
        + 0.05 * efficiency
    )
    return {
        "taskSuccess": task_success,
        "score": round(score, 2),
        "stepCompletionRate": round(completion_rate, 4),
        "actionAccuracy": round(action_accuracy, 4),
        "networkAssertionRate": round(network_rate, 4),
        "trajectoryOrderRate": round(order_rate, 4),
        "trajectoryEfficiency": round(efficiency, 4),
        "extraActionCount": extra_actions,
        "retryCount": retries,
        "averageVisualMatchScore": (
            round(sum(visual_scores) / len(visual_scores), 4) if visual_scores else None
        ),
    }
