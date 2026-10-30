"""Replay a golden E2E trace and evaluate the resulting agent execution trace."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from rec_assert import (
    LOCALTIME_KIND, TIMEWINDOW_KIND, TIMETRANGE_KIND, ANY_STR, assert_subset,
    expect_local_time, expect_time_window, local_time_value,
    expect_count, expect_value, expect_attribute, expect_time_range,
    expect_page_url, expect_page_title, expect_page_count, expect_state, matches_assert,
    COUNT_OPS, STRING_OPS, VALUE_OPS, _poll_stable, request_count_zero,
)
from rec_helpers import NetRequestWatcher
from rec_session import restore_context_session
from rec_secrets import REDACTED
from rec_visual import (
    VisualAbsent, VisualAmbiguous, VisualMatchError, locate_visual_target,
)
from selector_py import to_python
import trace_schema as ts


TRACE_SCHEMA = ts.SCHEMA
EXECUTION_SCHEMA = "edr.execution-trace/v1"
VOLATILE_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$",
    re.I,
)
# 断言步骤的动作类型是 DoNothing —— 断言不产生动作，识别/校验本身就是结论。
# 「这一步是不是断言」改看 attach.verification.assertion 有没有东西。
SUPPORTED_ACTIONS = {
    "Click", "DoubleClick", "InputText", "Check", "Uncheck",
    "SetSwitch", "PressKey", "DoNothing", "Navigate", "ClosePage",
}

# 布尔 UI 状态断言：统一走 rec_assert.expect_state（与生成链同核心同稳定语义）
_STATE_ASSERTIONS = frozenset({
    "visible", "checked", "enabled", "editable", "selected",
})


class TraceReplayError(RuntimeError):
    pass


def load_trace(path: str | Path) -> dict:
    trace = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_trace(trace)
    return trace


def validate_trace(trace: dict) -> list[str]:
    try:
        meta = ts.check_schema(trace)
    except ts.TraceFormatError as error:
        message = str(error)
        # 旧版 v1 轨迹没有 $meta，check_schema 会把它当成「不是 v2」。这类
        # 轨迹不是「坏了」，是另一种格式 —— 报错要指明去向，否则会让人白查
        # 生成器。只扩文案，不动校验逻辑。
        if "缺少 $meta" in message or "不是 v2" in message:
            message += ("\n提示：这是旧版非 v2 轨迹（无 $meta）。用 record.py 重新录制，"
                        "或用 /v2 导出器转出后再回放。")
        raise TraceReplayError(message) from error
    ids = ts.node_ids(trace)
    entry = meta.get("entry")
    if not ids:
        if entry is not None:
            raise TraceReplayError("空 trace 的 entry 必须是 null")
        return []
    if entry not in ids:
        raise TraceReplayError(f"$meta.entry 不存在: {entry!r}")

    order, seen, current = [], set(), entry
    while current is not None:
        if current in seen:
            raise TraceReplayError(f"trace 存在环: {current}")
        if current not in ids:
            raise TraceReplayError(f"next 指向不存在的步骤: {current}")
        seen.add(current)
        order.append(current)
        action = (trace[current].get("action") or {}).get("type")
        if action not in SUPPORTED_ACTIONS:
            raise TraceReplayError(f"{current} 使用不支持的动作: {action!r}")
        current = ts.next_of(trace[current])
    unreachable = ids - seen
    if unreachable:
        raise TraceReplayError(f"trace 包含不可达步骤: {sorted(unreachable)}")
    # maa-fw 那两个用 ** 反序列化的字典也在这里守 —— 见 strict_attach_errors。
    # 它们坏了只有 maa-fw 会炸，我们自己的回放跑得通，所以必须主动查。
    problems = [
        problem
        for node_id in order
        for problem in ts.strict_attach_errors(node_id, trace[node_id])
    ]
    if problems:
        raise TraceReplayError("轨迹不符合 maa-fw 的节点约定：\n  " + "\n  ".join(problems))
    # F6（审查 2026-10-29）：因果校验 —— 断言绑定了 afterStepId（录制时的触发步骤）。
    # 它在回放顺序里必须排在那个触发步骤**之后**；若找不到触发节点或排在它前面，
    # 说明断言会抢跑在触发动作之前求值（可能假通过），直接拒绝。
    src_to_node = {
        ts.provenance(trace[node_id]).get("sourceStepId"): node_id
        for node_id in order
    }
    pos = {node_id: i for i, node_id in enumerate(order)}
    for node_id in order:
        after = ts.provenance(trace[node_id]).get("afterStepId")
        if not after:
            continue
        trigger_node = src_to_node.get(after)
        if trigger_node is None:
            raise TraceReplayError(
                f"{node_id} 的 afterStepId={after!r} 指向不存在的录制步骤 —— 因果断裂")
        if pos[trigger_node] >= pos[node_id]:
            raise TraceReplayError(
                f"{node_id} 的断言出现在其因果前置步骤 {after!r}（{trigger_node}）"
                "**之前** —— 断言会在触发动作还没发生时求值，拒绝回放")
    return order


def _locator(page, selector: dict):
    source = selector.get("sel")
    if not source:
        return None
    root = page
    frame_chain = selector.get("frameChain") or []
    for frame in frame_chain:
        attr = next((key for key in ("id", "name") if frame.get(key)), None)
        if attr:
            value = str(frame[attr]).replace("\\", "\\\\").replace('"', '\\"')
            root = root.frame_locator(
                f'iframe[{attr}="{value}"], frame[{attr}="{value}"]'
            )
        elif isinstance(frame.get("index"), int):
            root = root.frame_locator("iframe, frame").nth(frame["index"])
        else:
            path = urlsplit(frame.get("src") or frame.get("url") or "").path
            value = path.replace("\\", "\\\\").replace('"', '\\"')
            root = root.frame_locator(f'iframe[src*="{value}"], frame[src*="{value}"]')
    if not frame_chain and selector.get("inFrame") and selector.get("framePath"):
        tail = selector["framePath"].split("/")[-1]
        root = page.frame_locator(f'iframe[src*="{tail}"]')
    expression = "root." + to_python(source)
    return eval(expression, {"__builtins__": {}}, {"root": root})


def _node_route(node: dict) -> tuple[str, str, str] | None:
    expected = ts.provenance(node).get("pageUrl")
    if not expected:
        return None
    parsed = urlsplit(expected)
    return parsed.path or "/", parsed.query, parsed.fragment


def _node_origin(node: dict) -> str | None:
    value = ts.provenance(node).get("pageOrigin")
    return value.rstrip("/") if isinstance(value, str) and value else None


def _page_route(page) -> tuple[str, str, str] | None:
    actual = getattr(page, "url", None)
    if not isinstance(actual, str) or not actual:
        return None
    parsed = urlsplit(actual)
    return parsed.path or "/", parsed.query, parsed.fragment


def _page_matches_node(page, node: dict) -> bool:
    if _page_route(page) != _node_route(node):
        return False
    expected_origin = _node_origin(node)
    if expected_origin is None:
        return True
    actual = urlsplit(getattr(page, "url", ""))
    return f"{actual.scheme}://{actual.netloc}" == expected_origin


def _wait_page_matches(page, node: dict, timeout_ms: int) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while not _page_matches_node(page, node):
        if time.monotonic() >= deadline or not hasattr(page, "wait_for_timeout"):
            return False
        page.wait_for_timeout(50)
    return True


def _page_for_node(page, node: dict, page_map: dict | None = None,
                   timeout_ms: int = 0):
    """Select a stable recorded page; URL is validation, not page identity."""
    provenance = ts.provenance(node)
    page_id = provenance.get("pageId")
    opener_id = provenance.get("openerPageId")
    if page_map is not None and page_id:
        known = page_map.get(page_id)
        if known is not None:
            _wait_page_matches(known, node, timeout_ms)
            return known
        if not opener_id and not page_map:
            page_map[page_id] = page
            return page

    want_route = _node_route(node)
    if want_route is None or _page_matches_node(page, node):
        if page_map is not None and page_id:
            page_map[page_id] = page
        return page
    context = getattr(page, "context", None)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        candidates = []
        for candidate in (getattr(context, "pages", None) or []):
            if not _page_matches_node(candidate, node):
                continue
            if page_map is not None and opener_id and page_map.get(opener_id) is not None:
                try:
                    opener_attr = candidate.opener
                    candidate_opener = (
                        opener_attr() if callable(opener_attr) else opener_attr
                    )
                    if candidate_opener is not page_map[opener_id]:
                        continue
                except Exception:
                    continue
            candidates.append(candidate)
        if len(candidates) == 1:
            if page_map is not None and page_id:
                page_map[page_id] = candidates[0]
            return candidates[0]
        if time.monotonic() >= deadline or not hasattr(page, "wait_for_timeout"):
            break
        page.wait_for_timeout(50)
    return page


def _assert_node_page(page, node: dict) -> None:
    """Fail before acting when no unique page is on the recorded route."""
    # Navigate 节点跳过路线断言：reload/前进后退**就是**把页面带回录制路线的
    # 动作，执行前的路线必然与目标不一致（reload 前是当前页，back 前是前进页）。
    # 拦下它只会报「路线不符」，把真正该做的导航吞掉。
    if (node.get("action") or {}).get("type") in ("Navigate", "ClosePage"):
        return
    want_route = _node_route(node)
    got_route = _page_route(page)
    if want_route is None or got_route is None:
        return
    if not _page_matches_node(page, node):
        expected_text = want_route[0]
        if want_route[1]:
            expected_text += f"?{want_route[1]}"
        if want_route[2]:
            expected_text += f"#{want_route[2]}"
        raise TraceReplayError(
            f"页面路径不符: 当前 {getattr(page, 'url', None)}，"
            f"轨迹此步应在 {expected_text}。"
            "前一步导航可能未发生，或录制中使用了浏览器返回/地址栏操作。"
        )


def _visual_ui(node: dict) -> dict:
    relative = ts.relative_point(node)
    return {
        **ts.geometry_of(node),
        "templates": ts.templates_of(node),
        "click": {"rx": relative["x"], "ry": relative["y"]},
    }


def _target(page, node: dict, template_root: Path, targeting: str, timeout_ms: int,
            focused_selector: str | None):
    action_type = (node.get("action") or {}).get("type")
    assertion = ts.assertion_of(node)
    if assertion:
        locator = _locator(page, ts.selector_of(node))
        if locator is None:
            raise TraceReplayError("断言步骤缺少 DOM 选择器")
        return "verifier", locator, None, None
    if action_type == "PressKey" and targeting == "visual_only":
        selector = ts.selector_of(node).get("sel")
        if not selector or selector != focused_selector:
            raise TraceReplayError("视觉按键步骤没有同目标动作建立的可信焦点")
        return "keyboard", None, None, None

    locator = None
    dom_error = None
    if targeting != "visual_only":
        locator = _locator(page, ts.selector_of(node))
    if locator is not None:
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return "dom", locator, None, None
        except Exception as error:
            # DOM 的失败原因必须留下来。以前这里直接吞掉回退视觉，
            # 于是执行记录里只剩「视觉匹配分数不足」—— 分不清是选择器写错了、
            # 元素本来就不该出现、还是整个页面根本不对（会话超时最典型）。
            dom_error = f"{type(error).__name__}: {error}"
            if not ts.has_template(node):
                raise
    if not ts.has_template(node):
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

    selector = ts.selector_of(node)
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


def _response_waiter(page, predicate, timeout_ms: int):
    """响应等待器：有真实 context 时用 context 级（跨所有页面，能抓到 popup 发出
    的请求）；mock/无 context 的测试页退回 page.expect_response（P2.8 双链通判的
    观察口径必须与录制侧 context 级一致，但历史 mock 页要么无 .context 要么 context
    是假对象没用 expect_event）。"""
    ctx = getattr(page, "context", None)
    if ctx is not None and hasattr(ctx, "expect_event"):
        return ctx.expect_event("response", predicate, timeout=timeout_ms)
    return page.expect_response(predicate, timeout=timeout_ms)


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
    if isinstance(value, str) and VOLATILE_DATETIME.match(value):
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


def _split_wait(param: dict, default_timeout_ms: int) -> tuple[float, float, float]:
    """把断言参数里的 wait 拆成 (timeout, poll, stable) 秒。

    wait 缺省时的语义（§6 统一 deadline + 稳定）：
      * timeout  用调用方默认（timeout_ms）
      * poll     100ms（不是 0.1ms —— 那是 busy-poll，会空转 CPU）
      * stable   0（首次满足即通过）
    显式传 poll/stable/timeout 才覆盖默认。
    """
    wait = param.get("wait") or {}
    timeout = (wait.get("timeoutMs") if "timeoutMs" in wait else default_timeout_ms) / 1000
    poll = (wait.get("pollMs") if "pollMs" in wait else 100) / 1000
    stable = (wait.get("stableMs") if "stableMs" in wait else 0) / 1000
    return timeout, poll, stable


def _reject_unconfirmed(param: dict) -> None:
    """P0（§5/§6）：需要静态 expected 的断言，expected 必须已确认；显式
    expectedConfirmed=false 即使绕过生成门直接喂回放也要独立拒绝 —— 系统不得把
    「当前看到什么」当作用户意图。老 trace 缺字段视为已确认（兼容）。
    """
    assertion = param.get("assertion")
    op = param.get("op")
    if param.get("expectedFrom"):
        return                       # 动态期望值无「预填自当前值」问题
    if op in ("is_blank", "is_not_blank"):
        return                       # 这类算子不需要 expected
    if param.get("expectedConfirmed") is False:
        raise TraceReplayError(
            f"断言 {assertion!r} 需要 expected 但 expectedConfirmed=false —— "
            "expected 未获用户确认，拒绝回放而不是当作意图")


def _assert_locator(locator, param: dict, timeout_ms: int) -> None:
    assertion, expected = param.get("assertion"), param.get("expected")
    _reject_unconfirmed(param)
    # 每条断言的等待预算与稳定窗口：_split_wait 处理缺省（F5：默认 poll 100ms）
    timeout, poll, stable = _split_wait(param, timeout_ms)
    dynamic = param.get("expectedFrom") or {}
    if dynamic.get("kind") == LOCALTIME_KIND:
        # 期望值由回放此刻的时钟决定。轮询里每次重算 —— 页面可能几秒后才刷新出
        # 新时间，拿一个固定下来的期望值去等，等到的是过时的比较基准。
        expect_local_time(
            locator, dynamic.get("format") or "%H:%M",
            match=dynamic.get("match") or "contains",
            # 输入框的时间在 value 上，inner_text 恒为空
            read="value" if assertion == "value" else "text",
            timeout=timeout,
        )
        return
    if dynamic.get("kind") == TIMEWINDOW_KIND:
        expect_time_window(
            locator, expected, dynamic.get("format") or "%H:%M",
            fluctuation_minutes=dynamic.get("fluctuationMinutes") or 0,
            read="value" if assertion == "value" else "text",
            timeout=timeout,
        )
        return
    if dynamic.get("kind") == TIMETRANGE_KIND:
        # 前缀 + 时长的时间区间：期望值本身是「一段时间」，不是字面文本。
        # 时长非法（0 / 非整数 / 越界）不能静默当缺省 —— 快速失败。
        duration = dynamic.get("durationSeconds")
        if (duration is not None
                and (isinstance(duration, bool) or not isinstance(duration, int)
                     or not 1 <= duration <= 86400)):
            raise TraceReplayError(
                f"时间区间断言的时长非法: {duration!r}（需 1–86400 的整数秒，或省略）")
        expect_time_range(
            locator, expected, duration,
            read="value" if assertion == "value" else "text",
            timeout=timeout,
        )
        return
    op = param.get("op") or "equals"
    # 快速失败：算子/期望值形状不对是录制或生成侧的错，轮询等满超时才报错，
    # 排障的人只会看到一条含糊的「断言失败」。
    if assertion in _STATE_ASSERTIONS and op not in ("equals", "not_equals"):
        raise TraceReplayError(f"{assertion} 断言不支持算子 {op!r}")
    if assertion in ("text", "value", "attribute") and op not in VALUE_OPS:
        raise TraceReplayError(f"不支持的比较算子: {op!r}")
    if assertion == "count":
        if (isinstance(expected, bool) or not isinstance(expected, int)
                or expected < 0 or op not in COUNT_OPS):
            raise TraceReplayError(
                f"count 断言要求非负整数期望值与算子 {'/'.join(COUNT_OPS)}: "
                f"expected={expected!r} op={op!r}")
        expect_count(locator, expected, op,
                     timeout=timeout, poll=poll, stable=stable)
        return
    if assertion in _STATE_ASSERTIONS:
        # 布尔状态统一走 rec_assert.expect_state —— 与生成链共用同一轮询/稳定
        want = bool(expected)
        if op == "not_equals":
            want = not want
        expect_state(locator, want, assertion,
                     timeout=timeout, poll=poll, stable=stable)
        return
    if assertion in ("value", "attribute"):
        if assertion == "value":
            expect_value(locator, expected, op,
                         timeout=timeout, poll=poll, stable=stable)
        else:
            expect_attribute(locator, param.get("attribute"), expected, op,
                             timeout=timeout, poll=poll, stable=stable)
        return
    if assertion != "text":
        raise TraceReplayError(f"不支持的断言: {assertion!r}")
    # text：保留空白归一化（非 strict），与生成链扩展算子共用同一比较定义
    err = (lambda last, last_error:
           (AssertionError(
               f"断言失败: text 读取持续失败 op={op!r} expected={expected!r}: "
               f"{type(last_error).__name__}: {last_error}")
            if last_error is not None else
            AssertionError(
                f"断言失败: text actual={last!r} expected={expected!r} op={op!r}")))
    _poll_stable(
        lambda: locator.inner_text(),
        lambda v: matches_assert(v, expected, op),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


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


def _assert_page(page, param: dict, timeout_ms: int) -> None:
    """页面级断言（scope="page"，P1）：URL / 标题，无 DOM 选择器。

    与生成链共用 expect_page_url / expect_page_title（同一 matches_assert +
    统一 deadline/stable 轮询），并照单执行录制的 wait 预算与稳定窗口。
    """
    assertion, expected = param.get("assertion"), param.get("expected")
    _reject_unconfirmed(param)
    timeout, poll, stable = _split_wait(param, timeout_ms)
    op = param.get("op") or "equals"
    if assertion == "page_count":
        # 页面数量：统计当前 browser context 的 page 数（count 类算子）
        if op not in COUNT_OPS:
            raise TraceReplayError(f"page_count 不支持算子 {op!r}")
        if (isinstance(expected, bool) or not isinstance(expected, int)
                or expected < 0):
            raise TraceReplayError(
                f"page_count 要求非负整数期望值与算子 {'/'.join(COUNT_OPS)}: "
                f"expected={expected!r}")
        expect_page_count(page, expected, op,
                          timeout=timeout, poll=poll, stable=stable)
        return
    if assertion not in ("url", "title"):
        raise TraceReplayError(f"不支持的页面断言: {assertion!r}")
    if not isinstance(op, str) or op not in STRING_OPS:
        raise TraceReplayError(f"{assertion} 不支持算子 {op!r}")
    helper = expect_page_url if assertion == "url" else expect_page_title
    helper(page, expected, op, timeout=timeout, poll=poll, stable=stable)


def _execute_action(page, node: dict, template_root: Path, targeting: str,
                    timeout_ms: int, env: dict,
                    focused_selector: str | None,
                    net_watcher: "NetRequestWatcher | None" = None) -> dict:
    action = node["action"]
    action_type, param = action["type"], action.get("param") or {}
    assertion = ts.assertion_of(node)
    # 页面级断言无需 DOM 定位：直接读当前 page 的 url / title
    if assertion and assertion.get("scope") == "page":
        _assert_page(page, assertion, timeout_ms)
        return {"mode": "verifier", "scope": "page"}
    # 网络范围断言无需 DOM 定位。status/request_body/response_body 在本步的
    # 响应等待块里按用户确认的 expected 校验；request_count=0 是负断言，
    # 在这里跑有界观察（稳定窗内持续为 0 才通过）。观察者**不在这里创建**：
    # 它的窗起点是因果动作（afterStepId 指向的节点）发生前，由主循环在执行
    # 那个动作之前就建好（net_watcher）—— 到本节点再建，动作自己发出的
    # 请求已经从指缝里漏掉了（popup 请求真录制闭环实测）。
    if assertion and assertion.get("scope") == "network":
        _reject_unconfirmed(assertion)
        if assertion.get("assertion") == "request_count":
            url_part = assertion.get("reqUrl")
            if not (isinstance(url_part, str) and url_part):
                raise TraceReplayError(
                    "request_count 断言缺少 reqUrl —— 观察口径不明，拒绝回放而不是猜")
            method = assertion.get("reqMethod") or None
            timeout, poll, stable = _split_wait(assertion, timeout_ms)
            # 稳定窗下限 300ms（§6 负断言，与生成链同一下限）：老录制把
            # request_count 记成 stableMs=0，直接回放会「动作刚发生就判 0」。
            stable = max(stable, 0.3)
            watcher = net_watcher or NetRequestWatcher(page, method=method)
            request_count_zero(watcher, url_part, timeout=timeout,
                               poll=poll, stable=stable)
        return {"mode": "verifier", "scope": "network"}
    # 页面导航（reload / 前进后退）同样无 DOM 目标：动作本身就是导航。
    # 做完再返回 —— 后续步骤的定位/URL 校验自然落在导航后的新文档上。
    if action_type == "Navigate":
        nav = param.get("navType", "reload")
        if nav == "reload":
            page.reload()
        elif nav == "back_forward":
            page.go_back()
        else:
            raise TraceReplayError(f"不支持的导航类型: {nav!r}")
        page.wait_for_load_state("load")
        return {"mode": "navigate", "navType": nav}
    # 页面关闭（Ctrl+W / 关标签页）：无 DOM 目标，动作本身就是关掉这一页。
    # 关闭对象按录制身份解析（_page_for_node 已按 pageId/opener 关系选页），
    # 不认当前变量 —— 关的是「录制时的那个标签页」，不是「现在的焦点页」。
    # 关完即返回：context 页面数回到录制终态，page_count 断言双链同判。
    if action_type == "ClosePage":
        try:
            page.close()
        except Exception as error:
            raise TraceReplayError(f"关闭页面失败: {error}") from error
        return {"mode": "close_page", "pageId": param.get("pageId")}
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

    if assertion:
        _assert_locator(locator, assertion, timeout_ms)
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


def _failure_category(error: BaseException) -> str:
    """失败类别的确定性映射 —— 按异常**类型/来源**，不做消息字符串匹配。

    平台规范 §7.1 的 failure_category：质量报告的「失败类型分布」
    只有落在结构化字段上才可统计。规则留了 unknown 出口：
    出现没登记过的新异常时先归 unknown 再回来补映射 ——
    未知比错标安全（不猜）。
    """
    if isinstance(error, TraceReplayError):
        return "infrastructure"     # 轨迹格式 / 环境契约错误
    if isinstance(error, AssertionError):
        return "assertion"         # 业务断言（含 expect_local_time / expect_time_window / 网络断言）
    if isinstance(error, (VisualAbsent, VisualAmbiguous)):
        return "automation"         # 元素缺席（非 optional）/ 视觉歧义
    if isinstance(error, VisualMatchError):
        return "infrastructure"     # 截图解码等视觉基设失败
    if type(error).__module__.startswith("playwright."):
        return "automation"         # 定位 / 交互 / 网络层超时与遮挡
    if isinstance(error, OSError):
        return "infrastructure"     # 模板文件 / 证据 IO
    return "unknown"


def _perform(page, node: dict, root: Path, targeting: str, timeout_ms: int,
             env: dict, focused_selector: str | None,
             net_watcher: "NetRequestWatcher | None" = None) -> tuple[dict, list]:
    """执行一步，并校验它应当触发的响应。返回 (target, responses)。"""
    responses: list = []
    optional_waiters = []
    try:
        expected_responses = ts.expected_responses(node)
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
                response_infos.append(stack.enter_context(_response_waiter(
                    page, _response_predicate(expected, occurrences[key]),
                    timeout_ms
                )))
            # 非必发的也必须在动作**之前**布置监听，否则请求会漏掉；
            # 只是退出时单独处理，不让它的超时打断整步。
            for expected in optional:
                key = (expected["method"], expected["url"])
                occurrences[key] = occurrences.get(key, 0) + 1
                waiter = _response_waiter(
                    page, _response_predicate(expected, occurrences[key]),
                    min(timeout_ms, 2_000),
                )
                optional_waiters.append((waiter, waiter.__enter__(), expected))
            target = _execute_action(
                page, node, root, targeting, timeout_ms, env, focused_selector,
                net_watcher=net_watcher,
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
                 navigate: bool = True, raise_on_error: bool = False,
                 session_state_dir: str | Path | None = None) -> dict:
    if targeting not in {"dom_first", "visual_only"}:
        raise ValueError("targeting 必须是 dom_first 或 visual_only")
    trace_path = Path(trace) if isinstance(trace, (str, Path)) else None
    golden = load_trace(trace_path) if trace_path else trace
    order = validate_trace(golden)
    meta = ts.meta(golden)
    if meta.get("status") != "ready":
        raise TraceReplayError("trace 尚未 ready，存在缺失模板的定位步骤")
    root = Path(template_root) if template_root else (
        trace_path.parent if trace_path else Path.cwd()
    )
    execution = {
        "schema": EXECUTION_SCHEMA,
        "goldenSchema": meta["schema"],
        "name": meta.get("name"),
        "startedAt": datetime.now().isoformat(),
        "status": "running",
        "steps": [],
    }
    runtime_env = os.environ if env is None else env
    start_url = meta.get("startUrl")
    session_root = (
        Path(session_state_dir)
        if session_state_dir is not None
        else (trace_path.parent / ".auth" if trace_path else None)
    )
    if session_root is not None and session_root.exists():
        context = getattr(page, "context", None)
        if context is None or not restore_context_session(context, session_root):
            raise TraceReplayError(f"无法恢复录制会话: {session_root}")
    elif session_state_dir is not None:
        raise TraceReplayError(f"录制会话目录不存在: {session_root}")
    if navigate and start_url:
        try:
            page.goto(start_url)
        except Exception as error:
            raise TraceReplayError(f"无法进入 trace 起始页面: {error}") from error
        _assert_landed(page, start_url, execution)
    first_error = None
    focused_selector = None
    focused_page = None
    # 被跳过的 optional 步骤：目标当时不在，但可能只是还没出现
    pending_optional: list[tuple[str, dict]] = []
    # 因为挡路而被提前做掉的关浮层步骤：轨迹走到它时会发现已经没得关
    performed_early: set[str] = set()
    recorded_pages: dict[str, object] = {}
    # request_count=0 的观察者必须赶在因果动作（afterStepId 指向的节点）执行
    # **之前**创建：到断言节点本身再建，动作自己发出的请求（以及 popup 打开
    # 后发出的请求）已经漏掉，负断言假绿（真实闭环实测）。预扫描把每个
    # count=0 断言节点映到它的触发节点，主循环在执行触发节点前建观察者。
    _countzero_trigger: dict[str, str] = {}
    for node_id in order:
        node = golden[node_id]
        assertion = ts.assertion_of(node)
        if (assertion and assertion.get("scope") == "network"
                and assertion.get("assertion") == "request_count"):
            after = ts.provenance(node).get("afterStepId")
            # afterStepId → 触发节点 id（validate_trace 已保证存在且在前）
            src_to_node = {
                ts.provenance(golden[n]).get("sourceStepId"): n
                for n in order
            }
            _countzero_trigger[node_id] = src_to_node[after]
    _pending_watchers: dict[str, "NetRequestWatcher"] = {}
    for node_id in order:
        node = golden[node_id]
        started = time.perf_counter()
        result = {
            "nodeId": node_id,
            "expectedAction": node["action"]["type"],
            "actualAction": node["action"]["type"],
            "status": "running",
            "responses": [],
            "retries": 0,
        }
        node_page = page
        try:
            node_page = _page_for_node(page, node, recorded_pages, timeout_ms)
            _assert_node_page(node_page, node)
            # 轮到本节点是某个 count=0 断言的因果动作：动作执行**前**建观察者
            # （窗起点=动作前），断言节点执行时用它轮询。
            for assert_id, trigger_id in list(_countzero_trigger.items()):
                if trigger_id == node_id and assert_id not in _pending_watchers:
                    assertion = ts.assertion_of(golden[assert_id]) or {}
                    method = assertion.get("reqMethod") or None
                    _pending_watchers[assert_id] = NetRequestWatcher(
                        node_page, method=method)
            node_focus = focused_selector if node_page is focused_page else None
            result["target"], result["responses"] = _perform(
                node_page, node, root, targeting, timeout_ms, runtime_env,
                node_focus,
                net_watcher=_pending_watchers.get(node_id),
            )
            result["status"] = "success"
            if targeting == "visual_only" and result["target"]["mode"] == "visual":
                focused_selector = ts.selector_of(node).get("sel")
                focused_page = node_page
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
                        skipped_page = _page_for_node(
                            page, golden[skipped_id], recorded_pages, timeout_ms)
                        skipped_focus = (
                            focused_selector if skipped_page is focused_page else None
                        )
                        _perform(skipped_page, golden[skipped_id], root, targeting,
                                 timeout_ms, runtime_env, skipped_focus)
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
                    if not ts.dismisses_overlay(golden[later_id]):
                        continue
                    try:
                        later_page = _page_for_node(
                            page, golden[later_id], recorded_pages, timeout_ms)
                        later_focus = focused_selector if later_page is focused_page else None
                        _perform(later_page, golden[later_id], root, targeting,
                                 timeout_ms, runtime_env, later_focus)
                    except Exception:
                        continue
                    performed_early.add(later_id)
                    recovered.append(later_id)
                    break                           # 一次只提前一步，别越做越多
                if recovered:
                    result["retries"] += 1
                    result["recoveredOptional"] = recovered
                    try:
                        node_page = _page_for_node(
                            page, node, recorded_pages, timeout_ms)
                        _assert_node_page(node_page, node)
                        node_focus = focused_selector if node_page is focused_page else None
                        result["target"], result["responses"] = _perform(
                            node_page, node, root, targeting, timeout_ms,
                            runtime_env, node_focus,
                            net_watcher=_pending_watchers.get(node_id),
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
            elif ts.is_optional(node) and _target_absent(
                node_page, node, error, targeting
            ):
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
                result["failureCategory"] = _failure_category(error)
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
    if execution.get("goldenSchema") != ts.meta(golden)["schema"]:
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
            and ts.is_optional(golden[node_id])
        )

    completed = sum(_satisfied(node) for node in order)
    action_hits = sum(
        actual.get(node, {}).get("actualAction") == golden[node]["action"]["type"]
        for node in order if node in actual
    )
    # 只把**必发**的响应计入分母。非必发的（读请求）在页面已处于目标状态时
    # 本来就不会重发，把它算进分母等于为一件正常的事扣分；
    # 但它真的出现时仍然会被校验，出错照样算失败。
    def _required(node_id: str) -> list:
        return [
            e for e in ts.expected_responses(golden[node_id])
            if e.get("required", True)
        ]

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
    reachable = set(order)
    observed_path = [
        step["nodeId"]
        for step in execution_steps
        if step["nodeId"] in reachable
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
    # 失败类型分布（平台规范 §3.5/§7.1）：只透传统计，不进评分公式。
    # 老 execution 文件没有 failureCategory 字段时计 unknown —— 缺字段不是失败。
    failure_categories: dict[str, int] = {}
    for node in order:
        step = actual.get(node, {})
        if step.get("status") == "failed":
            category = step.get("failureCategory") or "unknown"
            failure_categories[category] = failure_categories.get(category, 0) + 1

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
        "failureCategories": failure_categories,
        "averageVisualMatchScore": (
            round(sum(visual_scores) / len(visual_scores), 4) if visual_scores else None
        ),
    }


def _check_cli(argv: list[str] | None = None) -> int:
    """只读自检：一条命令确认 trace 能不能回放，不改任何文件。

    通过回放器真实的门槛（validate_trace：格式/无环/entry/支持动作/maa 契约），
    再叠加 $meta.status == 'ready'。不实际回放 —— 那需要浏览器与登录态。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="replay_trace --check",
        description="确认一条 golden trace 是否可回放（只读，不执行）",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="只读检查 trace 是否可回放（不执行）。这是默认行为，带上它只是让"
             "命令自述更明确（与 SKILL/设计文档里的调用一致）。",
    )
    parser.add_argument("trace", help="trace.json 路径")
    args = parser.parse_args(argv)

    try:
        trace = load_trace(args.trace)
        order = validate_trace(trace)
        status = ts.meta(trace).get("status")
        if status != "ready":
            n_missing = sum(
                1 for node_id in order
                if ts.provenance(trace[node_id]).get("status") == "missing_template"
            )
            print(
                f"NOT READY  {args.trace}: {n_missing} 个定位步骤缺模板/选择器"
                "（$meta.status != ready）。先用 record.py 补录或补齐模板再回放。"
            )
            return 1
        print(f"READY  {args.trace}: 可回放（{len(order)} 节点，过格式 + maa 契约）")
        return 0
    except TraceReplayError as error:
        print(f"NOT REPLAYABLE  {args.trace}: {error}")
        return 2
    except OSError as error:
        # F7（2026-10-23 评审）：目录/断链符号链接/不存在的深层路径/无权限等
        # 输入在 load_trace 的 read_text 处抛 OSError，之前未被捕获会裸栈
        # 崩溃。与 MCP 侧 replay_check 的 not-a-file 结构化处理对齐：沿用
        # 上一分支同一条错误输出通道，一行稳定诊断（含路径与 OS 错误类别），
        # 非 0 退出，不冒 traceback。
        if isinstance(error, IsADirectoryError):
            category = "路径是目录，不是文件"
        elif isinstance(error, FileNotFoundError):
            category = "文件不存在"
        elif isinstance(error, PermissionError):
            category = "没有读取权限"
        else:
            category = error.strerror or "读取失败"
        print(f"NOT REPLAYABLE  {args.trace}: 轨迹文件读取失败（{category}）")
        return 2


if __name__ == "__main__":
    raise SystemExit(_check_cli())
