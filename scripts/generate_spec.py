"""从录制数据生成 pytest 脚本草稿（generate-spec.mjs 的 Python 版）。

与 JS 版逐条对应，差异只在语言本身：

  Promise.all([waitForResponse, action])  → with page.expect_response(...) 嵌套
  expect(x).toMatchObject(y)              → assert_subset(x, y)      （Python 没有）
  expect.any(String)                      → ANY_STR                  （Python 没有）
  expect.poll(fn).toBe(v)                 → poll_until(fn, v)        （Python 没有）
  locator.first()                         → locator.first            （属性不是方法）
  块作用域 { }                             → 变量名加序号             （Python 没有块作用域）

单独成文件同样是为了能脱离浏览器测试：喂一份录制 JSON 进来就能验证生成结果。
"""

import json
import re
from urllib.parse import urljoin, urlsplit

from rec_assert import COUNT_OPS, STRING_OPS, VALUE_OPS
from selector_py import SelectorError, to_python
from rec_secrets import REDACTED, redact_sensitive_values


class SpecGenerationError(ValueError):
    """生成失败：有步骤无法被**如实**转译。

    宁可整份不生成，也不生成一份少了断言/动作的 spec —— 断言不存在的
    用例跑绿是最危险的一种假绿（录制者以为守住了，其实没守）。
    问题在收集阶段**一次报全**，不 fail-fast：半份诊断不如全份诊断。
    """

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__(
            f"生成失败（{len(self.problems)} 步无法转译）：\n  "
            + "\n  ".join(self.problems)
        )


_ASSERT_MARKER = re.compile(r"^ *# rec-assert: (\S+)")


def pytest_assert_ids(spec_text: str) -> list:
    """从生成 spec 里按序提取每条断言的录制 id（见生成处的 `# rec-assert:` 标记）。"""
    return [m.group(1) for line in spec_text.splitlines()
            for m in [re.match(_ASSERT_MARKER, line)] if m]


def recording_assert_ids(steps) -> list:
    """recording 里断言步骤的 id 序列（按录制顺序）。"""
    return [s.get("id") for s in steps
            if s.get("type") == "assert" and s.get("id")]


def _trace_assert_ids(trace) -> list:
    """trace 里断言节点对应的 recording id 序列（按节点顺序）。"""
    from trace_schema import assertion_of, nodes, provenance
    return [provenance(node).get("sourceStepId")
            for _, node in nodes(trace)
            if assertion_of(node) and provenance(node).get("sourceStepId")]


def artifact_assert_consistency(recording_steps, spec_text, trace) -> list:
    """三产物断言 id/数量一致性（P0 §8）。

    返回问题列表；空列表 = recording / pytest / trace 三处断言 id 与数量一一对应。
    任何一处被删除或改写都必然落进列表，调用方据此硬拒 —— 只给数量对得上、
    但断言被换走的「假一致」不在话下：比较的是 id 序列本身。
    """
    rec = recording_assert_ids(recording_steps)
    spec = pytest_assert_ids(spec_text)
    tr = _trace_assert_ids(trace)
    problems = []
    if rec != spec:
        problems.append(
            f"pytest 断言与 recording 不一致: recording={rec} pytest={spec}")
    if rec != tr:
        problems.append(
            f"trace 断言与 recording 不一致: recording={rec} trace={tr}")
    return problems
    """生成失败：有步骤无法被**如实**转译。

    宁可整份不生成，也不生成一份少了断言/动作的 spec —— 断言不存在的
    用例跑绿是最危险的一种假绿（录制者以为守住了，其实没守）。
    问题在收集阶段**一次报全**，不 fail-fast：半份诊断不如全份诊断。
    """

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__(
            f"生成失败（{len(self.problems)} 步无法转译）：\n  "
            + "\n  ".join(self.problems)
        )

# 易变值：UUID、长数字（雪花 ID / 毫秒时间戳）和日期时间筛选边界。
VOLATILE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{10,})$",
    re.I,
)
VOLATILE_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$",
    re.I,
)

HEADER = '''import os
import re
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect

from rec_assert import (
    ANY_NUM, ANY_STR, COUNT_OPS, STRING_OPS, VALUE_OPS, assert_subset,
    expect_local_time, expect_time_window, expect_count, expect_value,
    expect_attribute, expect_time_range, expect_state, expect_text,
    expect_page_url, expect_page_title, expect_page_count, local_time_value,
    poll_until, request_count_zero,
    matches_assert, net_status_fail,
)
from rec_helpers import (
    NetRequestWatcher, dismiss_overlays, is_present, nth_request,
    recorded_page_for, wait_for_recorded_url,
)
from rec_visual import visual_click

# 由 web-record 生成：{name}
# 写请求已自动生成断言（状态码 + 请求体形态）；GET 保留为注释。
# 请求体里的 UUID / 长数字 ID / 时间戳 / 日期时间已放宽，避免每次运行都失效。
#
# 用 recorded_page 而不是裸 page：它加载本次录制随附的 cookies 和 web storage。
# 首启弹窗由 dismiss_overlays 统一关掉 —— 它们的遮罩会静默吞掉后续点击，
# 而失败会报在后面某个 expect_response 上，看着像「接口没发」。
#
# 仍需人工处理：
#   1. 收紧仍标着 AMBIGUOUS 的选择器（多数已自动加了作用域）
#   2. 删掉与意图无关的误操作步骤
#   3. 会产生数据的用例：把清理动作填进文件末尾 _run_cleanup 的占位
#      （失败独立记录，绝不推翻业务结论 —— 平台规范 §6.6）
'''

# 开头那段登录：几乎必然录进来（录制从登录页开始），也几乎必然回放不了 ——
# 登录表单常在 iframe 里、常有同 placeholder 的诱饵输入框、密码又不该写进脚本。
# 而它对用例的意图毫无贡献，只是让每条用例都多一个失败点。
LOGIN_FRAME = re.compile(r"login|signin|sso", re.I)

# 动态期望值的种类；与 rec_assert.LOCALTIME_KIND 同名同义
LOCALTIME_KIND = "localtime"
TIMEWINDOW_KIND = "timewindow"
TIMETRANGE_KIND = "timerange"
LOGIN_ACTION = re.compile(r"login|sign[ -]?in|登录|登入", re.I)

# 扩展文本算子（starts_with/ends_with/matches/is_blank/is_not_blank）轮询闭包里
# 元素读取（inner_text）的显式短超时（毫秒）。读取失败按「本轮未观测到」处理，
# 由 poll_until 的预算（默认 5s）决定失败总时长；不给短超时的话，一次读取就要
# 烧掉页面默认超时（通常 30s），失败形态虽然是 AssertionError，代价却翻了数倍。
ASSERT_READ_TIMEOUT_MS = 1000


def _wait_kwargs(step: dict, *, default_stable_ms: float = 0.0) -> str:
    """把录制的等待策略（§5 wait）落成 helper 关键字参数串。

    老录制没有 wait 字段 → 返回空串（helper 用各自缺省）；新录制用
    timeoutMs/pollMs/stableMs。default_stable_ms 用于负断言的无字段兜底
    （§6：负断言默认稳定窗口）。stableMs 单位毫秒，这里换成秒。
    """
    w = step.get("wait") or {}
    if not w:
        if not default_stable_ms:
            return ""
        return f"stable={default_stable_ms / 1000:g}"
    timeout_s = (w.get("timeoutMs") or 5000) / 1000
    poll_s = (w.get("pollMs") or 100) / 1000
    stable_ms = w.get("stableMs")
    if stable_ms is None:
        stable_ms = default_stable_ms
    return (f"timeout={timeout_s:g}, poll={poll_s:g}, "
            f"stable={stable_ms / 1000:g}")


def prepare_steps(steps):
    """Normalize recorded events into the action sequence used by generated scripts."""
    prepared = []
    for step in steps:
        if (
            step.get("type") == "dblclick"
            and prepared
            and prepared[-1].get("type") == "click"
            and prepared[-1].get("sel") == step.get("sel")
            and prepared[-1].get("inFrame") == step.get("inFrame")
        ):
            first_click = prepared[-1]
            prepared[-1] = {
                **step,
                "t": first_click["t"],
                "actionT": first_click.get("actionT", first_click["t"]),
                "ui": first_click.get("ui") or step.get("ui"),
            }
        else:
            prepared.append(step)

    for i in range(len(prepared) - 1):
        a, b = prepared[i], prepared[i + 1]
        if (a["type"] == "press" and a.get("inputOrder") != "value-before-key"
                and b["type"] == "fill"
                and a["sel"] == b["sel"] and a.get("inFrame") == b.get("inFrame")):
            prepared[i] = {**b, "t": a["t"]}
            prepared[i + 1] = {**a, "t": b["t"]}

    # 用户先点输入框、再输入时，点击已经完成了目标消歧和聚焦。尤其是视觉回退：
    # DOM selector 可能匹配两个组件，但屏幕坐标点中了其中正确的那个。fill 若再次
    # 用歧义 selector 定位，会丢掉刚刚建立的因果关系并触发 strict-mode violation。
    # 这里只认紧邻、同一 DOM 目标和同一 frame 的 click → fill，不跨步骤猜焦点。
    for i in range(1, len(prepared)):
        focus, step = prepared[i - 1], prepared[i]
        same_target = bool(
            (focus.get("css") and focus.get("css") == step.get("css"))
            or (focus.get("sel") and focus.get("sel") == step.get("sel"))
        )
        if (step.get("type") == "fill" and focus.get("type") == "click"
                and same_target and focus.get("inFrame") == step.get("inFrame")):
            prepared[i] = {**step, "_useFocusedElement": True}
        elif (step.get("type") == "press" and focus.get("type") == "fill"
                and focus.get("_useFocusedElement") and same_target
                and focus.get("inFrame") == step.get("inFrame")):
            # 连续 click → fill → Enter 保留已确认的输入目标。
            prepared[i] = {**step, "_useFocusedElement": True}

    # 「用文本定位元素，再断言它的文本等于那段文本」是同义反复：
    # expect(page.get_by_text("X")).to_have_text("X") 只有元素消失才会失败。
    # 实测录出来的两条断言都是这个形状 —— 看着像断言，其实什么都没断。
    #
    # 用户右键选「文本等于」的意思是「这段文字应该在」，那就如实生成存在性断言。
    # 顺带解掉一个假问题：这种断言撞车不是缺陷 ——「至少有一个可见」本来就是
    # 确定的命题，.first() 在这里是诚实的，不是掷骰子。
    for i, step in enumerate(prepared):
        if step.get("type") != "assert" or step.get("assertion") != "text":
            continue
        # 期望值在回放时算出来的断言不是同义反复：它比的不是「录到的那段文字」，
        # 而是「回放此刻的时间」。改写成存在性断言会把这个意图整个抹掉。
        if step.get("expectedFrom"):
            continue
        # 显式选了比较算子就不改：「等于」的默认语义才等价于「这段文字应该在」。
        # 用户选了包含/正则/为空等，比较方式本身就是命题的一部分。
        if step.get("op"):
            continue
        expected = step.get("expected")
        sel = step.get("sel") or ""
        if not expected or "getByText(" not in sel or expected not in sel:
            continue
        prepared[i] = {
            **step, "assertion": "visible", "expected": True,
            "_wasTextTautology": expected,
        }

    def is_login_step(step) -> bool:
        return bool(step.get("secret") is True or (
            step.get("inFrame") and LOGIN_FRAME.search(step.get("framePath") or "")))

    cut = 0
    while cut < len(prepared) and is_login_step(prepared[cut]):
        cut += 1
    if (0 < cut < len(prepared) and prepared[cut]["type"] == "press"
            and prepared[cut].get("inFrame")):
        cut += 1
    if cut == 0:
        secret_index = next(
            (i for i, step in enumerate(prepared[:10]) if step.get("secret") is True),
            None,
        )
        if secret_index is not None:
            secret_selector = prepared[secret_index].get("sel")
            for i, step in enumerate(prepared[secret_index + 1:], secret_index + 1):
                submits_with_enter = (
                    step.get("type") == "press"
                    and step.get("sel") == secret_selector
                )
                submits_with_button = (
                    step.get("type") == "click"
                    and LOGIN_ACTION.search(
                        f"{step.get('label') or ''} {step.get('sel') or ''}"
                    )
                )
                if submits_with_enter or submits_with_button:
                    cut = i + 1
                    break
    return prepared[:cut], prepared[cut:]


def _ident(name: str) -> str:
    """把录制名变成合法的 pytest 函数名。"""
    s = re.sub(r"\W+", "_", name, flags=re.UNICODE).strip("_")
    if not s or s[0].isdigit():
        s = "rec_" + s
    return s


def _lit(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if v is None:
        return "None"
    return json.dumps(v, ensure_ascii=False)


def _to_matcher(v, indent: int = 4) -> str:
    """把抓到的请求体变成断言用的字面量（易变值换成 ANY_STR）。"""
    pad = " " * indent
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, list):
        if not v:
            return "[]"
        items = ",\n".join(pad + "    " + _to_matcher(x, indent + 4) for x in v)
        return "[\n" + items + f",\n{pad}]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        items = ",\n".join(
            f"{pad}    {json.dumps(k, ensure_ascii=False)}: {_to_matcher(v[k], indent + 4)}"
            for k in v
        )
        return "{\n" + items + f",\n{pad}}}"
    if v == REDACTED:
        return "ANY_STR"
    if isinstance(v, str) and (VOLATILE.match(v) or VOLATILE_DATETIME.match(v)):
        return "ANY_STR"
    # 数字型的时间戳和雪花 ID 也要放宽。只处理字符串的话，像「最近 30 天」这种
    # 默认查询条件会把录制那一刻的毫秒时间戳原样钉进断言 —— 下一次运行必然对不上，
    # 而这恰恰是上面那段注释声称要防住的失效方式。
    # 门槛取 1e9：10 位是秒级时间戳，13 位是毫秒级，业务上的页码、数量都远小于它。
    if isinstance(v, int) and abs(v) >= 1_000_000_000:
        return "ANY_NUM"
    return json.dumps(v, ensure_ascii=False)


def pair_network_events(net):
    """Pair persisted responses with requests using IDs, then method/URL FIFO."""
    pairs = []
    by_id = {
        n["id"]: n
        for n in net
        if n["phase"] == "req" and n.get("id") is not None
    }
    pending: dict[str, list] = {}
    for event in sorted(net, key=lambda n: (n["t"], 0 if n["phase"] == "req" else 1)):
        key = f"{event['method']}\n{event['url']}"
        if event["phase"] == "req":
            pending.setdefault(key, []).append(event)
            continue
        request = by_id.get(event.get("requestId"))
        if request is None:
            queue = pending.get(key) or []
            request = queue.pop(0) if queue else None
        pairs.append((request, event))
    return pairs


def generate_spec(steps, net, start_url, name):
    parts = urlsplit(start_url)
    origin = f"{parts.scheme}://{parts.netloc}"

    absolute_urls = [urlsplit(n["url"]) for n in net if n.get("url")]
    absolute_urls = [u for u in absolute_urls if u.scheme or u.netloc]
    if absolute_urls and not any(
        (u.scheme, u.netloc) == (parts.scheme, parts.netloc) for u in absolute_urls
    ):
        raise ValueError(
            f"录制中没有任何 URL 属于 start_url 的 origin {origin!r}；"
            "请检查 start_url 是否指向了正确环境。"
        )

    def strip(u):
        parsed = urlsplit(u)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (parts.scheme, parts.netloc):
                return u
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            if parsed.fragment:
                path += f"#{parsed.fragment}"
            return path
        return u.replace(origin, "")

    def between(a, b):
        return [n for n in net if n["phase"] == "res" and a <= n["t"] < b]

    # Playwright 的 response 能直接拿到它的 request，但落盘后的录制数据没有对象引用。
    # 按 method + URL 为每条请求维护 FIFO 队列，恢复响应与请求的一一对应关系。
    # 不能简单找「响应之前最后一条同 URL 请求」：同一操作并发发两次相同请求时，
    # 那会让两个响应都错误地关联到第二条请求。
    request_of = {
        id(response): request
        for request, response in pair_network_events(net)
        if request is not None
    }

    def req_of(res):
        return request_of.get(id(res))

    def req_body_of(res):
        r = req_of(res)
        if not r or not r.get("body"):
            return None
        try:
            return redact_sensitive_values(json.loads(r["body"]))
        except (ValueError, TypeError):
            return None

    # 在丢掉登录步骤前记住初始页面。业务步骤可能全部发生在 popup；若等
    # prepare_steps 后再猜，会把 popup 错绑到 recorded_page（opener）。
    recorded_root_page_id = next(
        (step.get("pageId") for step in steps
         if step.get("pageId") and not step.get("openerPageId")),
        None,
    )

    # 修正 change/keydown 的事件顺序、折叠双击，并丢掉开头登录段。
    dropped, steps = prepare_steps(steps)
    gated_ids = {
        step_id
        for step in steps
        for step_id in ((step.get("via") or {}).get("gatedSteps") or [])
    }

    head = [HEADER.format(name=name).rstrip("\n")]
    if dropped:
        head.append("#")
        head.append(f"# 已自动去掉开头 {len(dropped)} 步登录（改用登录态复用）。原步骤：")
        for s in dropped:
            tail = "  <密码，未记录>" if s.get("secret") else ""
            head.append(f"#   {s['type']} {s['sel']}{tail}")

    root_page_id = recorded_root_page_id
    lines = [*head, "",
             f"def test_{_ident(name)}(recorded_page: Page):",
             "    page = recorded_page",
             f"    recorded_pages = {{{_lit(root_page_id)}: page}}"
             if root_page_id else "    recorded_pages = {}",
             f"    page.goto({_lit(strip(start_url) or '/')})",
             "    dismiss_overlays(page)"]

    resp_seq = 0
    sw_seq = 0
    el_seq = 0
    text_seq = 0
    popup_seq = 0

    def _emit_network_assert(na, writes, names, req_names, inner="    "):
        """把一条 scope=network 断言落成等待块里的比较行。

        请求对象按录制时的 reqMethod/reqUrl 与等待块里的 writes 配对：多条请求
        时按「断言绑的请求是哪条」选对象，不能默认拿最后一个。匹配不上属于
        生成配置错误 —— 断出来的不是绑定那条就等于没断，进 problems 拒发。
        request_count=0 走 request_count_zero（有界观察窗 + 稳定窗）。

        inner 是等待块的**内层**缩进：request_count 的观察者创建行（pre）落
        在动作行之前、expect_request 的 with 块**内部** —— 动作自己的请求
        也必须落在窗内。缩进不跟 with 走会生成语法不成立的代码（真实闭环
        实测：同动作共存自动写断言 + count=0 时 watcher 行缩进少一层）。
        """
        a, exp = na.get("assertion"), na.get("expected")
        url_part = na["reqUrl"]
        method = na["reqMethod"]
        # 与等待器配对：strip 后路径含 url_part 且 method 相同的第一条。
        # 落盘的请求 URL 是绝对的（含 origin），reqUrl 来自录制菜单的请求清单
        # —— 可能是完整绝对 URL，也可能是用户改过的相对片段。两边都过 strip
        # 再比；仍是绝对 URL 的（跨 origin 请求）按原样参与包含判断。
        idx = None
        for wi, w in enumerate(writes):
            stripped = strip(w["url"])
            if w["method"] == method and (url_part in stripped
                                          or url_part in w["url"]):
                idx = wi
                break
        if a == "request_count":
            # 负断言不需要请求对象。观察窗由动作本身界定，分两半落：
            #   前半（观察者创建）在动作行**之前** —— 窗起点=动作发生前，
            #     不然动作自己发的请求会漏掉；
            #   后半（稳定窗轮询）在动作行**之后** —— 动作先发生，轮询才
            #     开始数：请求在途时不会立即判 0，迟到的请求也会被数到
            #     （观察者 count() 驱动事件分发）。
            # 返回 (pre_action_lines, post_action_lines)。
            # 稳定窗下限 300ms（§6 负断言）：老录制（菜单缺陷修复前）把
            # request_count 记成 stableMs=0 —— 一进窗读到 0 就放行，迟到的
            # 请求永远数不到（真实录制闭环实测）。落生成参数时抬到下限。
            na = dict(na)
            wait = dict(na.get("wait") or {})
            if (wait.get("stableMs") or 0) < 300:
                wait["stableMs"] = 300
            na["wait"] = wait
            w_args = _wait_kwargs(na, default_stable_ms=300)
            arg = f", {w_args}" if w_args else ""
            # 观察者名必须合法：录制 id 可能含连字符等字符（rc-1）
            watcher = f"_net_watcher{_ident(na.get('id') or 'x')}"
            # 观察口径必须带 method：回放链按 method+url 过滤，生成链不带
            # method 就对 POST 噪音误报、两链不同判（真实录制闭环实测）。
            method_arg = f", method={_lit(method)}" if method else ""
            marker = f"    # rec-assert: {na['id']}" if na.get("id") else None
            pad = inner
            pre = [f"{pad}{watcher} = NetRequestWatcher(page{method_arg})"]
            post = [f"    request_count_zero({watcher}, {_lit(url_part)}{arg})"]
            if marker:
                post.append(marker)
            return pre, post
        if idx is None:
            problems.append(
                f"step id={na.get('id')}: 网络断言绑定的请求 "
                f"{method} {url_part} 不在本动作触发的请求里，拒发而不是断错对象")
            return [], []
        req_var = req_names[idx]
        resp_var = names[idx]
        out = []
        if a == "status":
            # 失败要能指回这条断言：给出实际值和期望值（§8 失败报告）。
            out.append(f"    if {resp_var} is None or {resp_var}.status != {exp}:")
            out.append(
                f"        net_status_fail({resp_var}.status if {resp_var} "
                f"is not None else None, {exp}, {_lit(na.get('id'))})")
        elif a == "request_body":
            out.append(
                f"    assert_subset({req_var}.post_data_json, {_to_matcher(exp)})")
        elif a == "response_body":
            out.append(
                f"    assert_subset({resp_var}.json(), {_to_matcher(exp)})")
        if na.get("id"):
            # P0（§8）：一致性校验按行首 rec-assert 标记提取断言 id，必须单独成行
            out.append(f"    # rec-assert: {na['id']}")
        return [], out

    def emit_action(action_expr, calls, warn, network_asserts=None):
        """一次操作；若触发了写请求，包成「等响应 + 断言」。

        network_asserts 是用户在录制菜单选「最近请求」范围落下的 scope=network
        断言步骤（afterStepId 指向本动作）。它们与自动生成的写请求断言共用同一条
        pre-armed expect_request 等待链 —— 因果动作发出请求、等待块抓到 request、
        用户契约断在同一个 request/response 对象上，观察起点/终点就是动作本身。
        """
        nonlocal resp_seq
        network_asserts = network_asserts or []
        # 按请求**发出**次序生成等待，而不是响应完成次序 —— 并发请求可能后发先回
        writes = sorted(
            (c for c in calls if c["method"] != "GET" and c["status"] < 400),
            key=lambda c: (req_of(c) or c)["t"],
        )
        if not writes and not network_asserts:
            out = [f"    {action_expr}{warn}"]
            for c in calls:
                out.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
                if c["status"] >= 400 and c.get("body"):
                    body = re.sub(r"\s+", " ", c["body"][:160])
                    out.append(f"    #     ⚠ 失败响应: {body}")
            return out

        # 变量名必须唯一：Python 没有块作用域，整个函数体是一个命名空间
        resp_seq += 1
        base = resp_seq
        names = [f"resp{base}" if len(writes) == 1 else f"resp{base}_{i + 1}"
                 for i in range(len(writes))]
        req_names = [n.replace("resp", "req", 1) for n in names]

        # 等的是 request 而不是 response：请求对象上既能拿到 post_data_json，
        # 也能 .response() 拿回响应，配对关系不会串。
        # 多个写请求 → 嵌套 with，等价于 JS 的 Promise.all([...waitForRequest])
        occurrences: dict[str, int] = {}
        out = []
        inner = "    " + "    " * len(writes)
        # 用户手选的网络契约断言（scope=network）先收集，按观察窗语义分插：
        # request_count=0 的观察者创建在**动作行之前**（窗起点=动作发生前，
        # 不然动作自己发的请求会漏掉），稳定窗轮询在动作行之后（请求在途
        # 不算 0）；status/body 断言在动作行后、断在已抓到的对象上。
        pre: list[str] = []
        post: list[str] = []
        for na in network_asserts:
            pre_lines, post_lines = _emit_network_assert(
                na, writes, names, req_names, inner=inner)
            pre.extend(pre_lines)
            post.extend(post_lines)
        for i, w in enumerate(writes):
            p = strip(w["url"]).split("?")[0]
            key = f"{w['method']}\n{p}"
            nth = occurrences.get(key, 0) + 1
            occurrences[key] = nth
            pad = "    " + "    " * i
            out.append(
                f"{pad}with page.context.expect_event(\"request\", "
                f"nth_request({_lit(p)}, {_lit(w['method'])}, {nth})"
                f") as {req_names[i]}_info:"
            )
        # 观察者创建行在 with 块内部、动作行之前（窗起点=动作前）—— 缩进
        # 必须跟 with 的内层走，动作行的缩进就是 inner。
        out.extend(pre)
        out.append(f"{inner}{action_expr}{warn}")
        for i, w in enumerate(writes):
            out.append(f"    {req_names[i]} = {req_names[i]}_info.value")
            out.append(f"    {names[i]} = {req_names[i]}.response()")
            out.append(f"    assert {names[i]} is not None and "
                       f"{names[i]}.status == {w['status']}")
            body = req_body_of(w)
            if isinstance(body, dict):
                out.append(f"    assert_subset({req_names[i]}.post_data_json, "
                           f"{_to_matcher(body)})")
        for c in calls:
            if c["method"] == "GET":
                out.append(f"    #   ↳ GET {strip(c['url'])} -> {c['status']}")
        out.extend(post)
        return out

    # 每个顶层 Page 独立推进自己的地址。把 popup 和 opener 共用一个 URL 基准
    # 会把「新标签页里的第一步」误写成「当前 page 地址已变」。
    #
    # 录制器记的 url 是 `location.pathname + hash`，在 iframe 里就是**iframe 自己的**
    # 地址。拿它跟主文档地址比，会得出「地址变成 /login_frame.html 了」这种结论，
    # 进而建议人 page.goto("/login_frame.html") —— 那会把主页面整个换掉。
    # 而且 iframe 地址若留作下一步的比较基准，紧接着还会再误报一次。
    # 所以 in-frame 的步骤既不报变化，也不更新基准。
    last_top_urls: dict[str, str] = {}
    current_page_id = root_page_id

    def step_page_url(step):
        route = step.get("url")
        origin = step.get("pageOrigin")
        return urljoin(origin.rstrip("/") + "/", route) if origin and route else None

    # 无法如实转译的步骤：收集全部问题，最后统一拒发（SpecGenerationError）。
    # 注释照写 —— 错误文本里已有诊断，代码文本只是第二份证据。
    problems: list[str] = []
    # scope=network 断言按 afterStepId 预分组：动作步骤先于断言被遍历，等待块
    # 生成时就能拿到自己名下的网络断言（不能靠 pop 时序）。
    _network_by_action: dict[str, list] = {}
    for _s in steps:
        if (_s.get("type") == "assert" and _s.get("scope") == "network"
                and _s.get("afterStepId") and not _s.get("expectedConfirmed") is False):
            a = _s.get("assertion")
            exp = _s.get("expected")
            pre_ok = (
                a in ("status", "request_body", "response_body", "request_count")
                and isinstance(_s.get("reqUrl"), str) and _s["reqUrl"]
                and isinstance(_s.get("reqMethod"), str) and _s["reqMethod"]
                and (a != "status" or (isinstance(exp, int)
                                       and not isinstance(exp, bool)
                                       and 100 <= exp <= 599))
                and (a != "request_count" or (isinstance(exp, int)
                                              and not isinstance(exp, bool)
                                              and exp == 0))
            )
            if pre_ok:
                _network_by_action.setdefault(_s["afterStepId"], []).append(_s)
    for i, s in enumerate(steps):
        hi = steps[i + 1]["t"] if i + 1 < len(steps) else float("inf")
        calls = between(s["t"], hi)

        in_frame = bool(s.get("inFrame"))
        page_key = s.get("pageId") or "__legacy__"
        expected_page_url = step_page_url(s)

        if not in_frame and s.get("pageId") and s.get("pageId") != current_page_id:
            if expected_page_url:
                lines.append(
                    "    page = recorded_page_for("
                    f"recorded_page.context, recorded_pages, {_lit(s['pageId'])}, "
                    f"expected_url={_lit(expected_page_url)}, "
                    f"opener_id={_lit(s.get('openerPageId'))})"
                )
            current_page_id = s["pageId"]

        # 同一个 Page 的地址变化必须等导航完成；不同 pageId 已在上面切换页面。
        # 基准记完整 URL（含 origin）：步骤 url 只有路径，跨 origin 同路径
        # 导航（同主机不同端口等）在路径上不可见 —— 只比路径会把「跳到
        # 另一个 origin 的同一页面」当成没动，后续步骤不等导航就执行，
        # 全部落在旧 origin 的页面上，随后被跳转抹掉（双链分歧：trace 链
        # 按 pageOrigin 校验，会等到导航完成）。
        prev_url = last_top_urls.get(page_key)
        if not in_frame and s.get("url"):
            last_top_urls[page_key] = expected_page_url or s["url"]
        if (not in_frame and prev_url and s.get("url")
                and (expected_page_url or s["url"]) != prev_url):
            if expected_page_url:
                lines.append(
                    f"    wait_for_recorded_url(page, {_lit(expected_page_url)})"
                )
            lines.append(f"    #   ⇢ 地址已变为 {s['url']}")
            lines.append(f"    #     若前面几步只是为了导航到这里，可换成 "
                         f"page.goto({_lit(s['url'])})，")
            lines.append("    #     避开侧边栏菜单在小 viewport 下需要滚动才可见的问题。")

        # 存在性断言撞车不是缺陷：「至少有一个可见」本来就是确定的命题，
        # 这时候用「回放时可能点错」吓人反而误导。
        if s.get("_useFocusedElement"):
            warn = "   # 前一步已聚焦正确输入框；fill 不再用歧义 selector 二次定位"
        elif not s.get("ambiguous"):
            warn = ""
        elif s.get("_wasTextTautology"):
            warn = f"   # {s.get('matches')} 个元素匹配 → 断言的是「至少有一个可见」"
        else:
            warn = f"   # ⚠ AMBIGUOUS: {s.get('matches')} 个元素匹配，回放时可能点错"

        # 嵌套 iframe 按录制时的完整链逐层进入。旧录制仍用 framePath 兼容。
        root = "page"
        for frame in s.get("frameChain") or []:
            attr = next((key for key in ("id", "name") if frame.get(key)), None)
            if attr:
                value = str(frame[attr]).replace("\\", "\\\\").replace('"', '\\"')
                frame_sel = f'iframe[{attr}="{value}"], frame[{attr}="{value}"]'
                root += f".frame_locator({_lit(frame_sel)})"
            elif isinstance(frame.get("index"), int):
                root += f'.frame_locator("iframe, frame").nth({frame["index"]})'
            else:
                path = urlsplit(frame.get("src") or frame.get("url") or "").path
                value = path.replace("\\", "\\\\").replace('"', '\\"')
                frame_sel = f'iframe[src*="{value}"], frame[src*="{value}"]'
                root += f".frame_locator({_lit(frame_sel)})"
        if root == "page" and s.get("inFrame") and s.get("framePath"):
            tail = s["framePath"].split("/")[-1]
            root = f'page.frame_locator({_lit(f"iframe[src*=\x22{tail}\x22]")})'

        # ── 页面级断言（scope="page"，P1）：URL / 标题，无 DOM 选择器 ──
        # 绑定当前 pageId（前面已 page = recorded_page_for(...)），比较的是页面
        # 自身 url / title，与元素断言同为 matches_assert + 统一轮询（§8 两链同判）。
        if s["type"] == "assert" and s.get("scope") == "page":
            a, exp = s.get("assertion"), s.get("expected")
            op0 = s.get("op")
            if (a in ("url", "title") and op0 not in ("is_blank", "is_not_blank")
                    and s.get("expectedConfirmed") is False):
                lines.append("    # ⚠ 页面断言需要 expected 但 expectedConfirmed=false，已拒发")
                problems.append(
                    f"step#{i} (id={s.get('id')}): 页面断言 expected 未确认，拒发而不是少断言")
                continue
            if a == "page_count":
                # 页面数量：统计当前 browser context 的 page 数（count 类算子）
                op = op0 or "equals"
                if not (isinstance(op, str) and op in COUNT_OPS):
                    lines.append(f"    # ⚠ {a} 不支持算子 {op!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): {a} 不支持算子 {op!r}")
                elif not (isinstance(exp, int) and not isinstance(exp, bool) and exp >= 0):
                    lines.append(f"    # ⚠ {a} 的期望须为非负整数 {exp!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): {a} 期望须为非负整数 {exp!r}")
                else:
                    w_args = _wait_kwargs(s)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    expect_page_count(page, {_lit(exp)}, op={_lit(op)}{arg}){warn}")
            elif a in ("url", "title"):
                op = op0 or "equals"
                if not isinstance(op, str) or op not in STRING_OPS:
                    lines.append(f"    # ⚠ {a} 不支持算子 {op!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): {a} 不支持算子 {op!r}")
                else:
                    helper = "expect_page_url" if a == "url" else "expect_page_title"
                    w_args = _wait_kwargs(s)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    {helper}(page, {_lit(exp)}, op={_lit(op)}{arg}){warn}")
            else:
                lines.append(f"    # ⚠ 未知页面断言类型 {_lit(a)}，已拒发")
                problems.append(
                    f"step#{i} (id={s.get('id')}): 未知页面断言类型 {_lit(a)}")
            if s.get("id"):
                lines.append(f"    # rec-assert: {s['id']}")
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
            continue

        # ── 网络范围断言（scope="network"，P2 §9.2）：挂到因果动作的等待块 ──
        # 断言本身不生成独立代码 —— 比较落在 afterStepId 指向的动作的
        # expect_request 等待块里（观察起点=动作、终点=块退出）。先校验+暂存，
        # 动作步骤处理时取出。无法归属的动作、坏算子、未确认 expected 都拒发。
        if s["type"] == "assert" and s.get("scope") == "network":
            a, exp = s.get("assertion"), s.get("expected")
            sid = s.get("id")
            ok = True
            if a not in ("status", "request_body", "response_body", "request_count"):
                lines.append(f"    # ⚠ 未知网络断言类型 {a!r}，已拒发")
                problems.append(
                    f"step#{i} (id={sid}): 未知网络断言类型 {a!r} —— "
                    "拒发而不是少断言")
                ok = False
            elif a != "request_count":
                # 静态期望值必须已确认（P0 红线在网络范围同样成立）
                if s.get("expectedConfirmed") is False:
                    lines.append("    # ⚠ 网络断言需要 expected 但未确认，已拒发")
                    problems.append(
                        f"step#{i} (id={sid}): 网络断言 expected 未确认，拒发而不是少断言")
                    ok = False
                if a == "status" and not (isinstance(exp, int)
                                          and not isinstance(exp, bool)
                                          and 100 <= exp <= 599):
                    lines.append(f"    # ⚠ status 期望须为三位整数 {exp!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={sid}): status 期望须为 100–599 的整数 {exp!r}")
                    ok = False
            if ok and not s.get("afterStepId"):
                # 没有因果前置动作 → 无法确定观察起点（请求是哪个动作发出来的），
                # 按动作窗口猜测会引入「最后一条同 URL」式错配 —— 拒发。
                lines.append("    # ⚠ 网络断言缺少因果前置步骤，无法确定观察起点，已拒发")
                problems.append(
                    f"step#{i} (id={sid}): 网络断言 afterStepId 缺失 —— "
                    "无法确定请求归属的动作，拒发而不是猜")
                ok = False
            if ok and not (isinstance(s.get("reqUrl"), str) and s["reqUrl"]
                           and isinstance(s.get("reqMethod"), str) and s["reqMethod"]):
                lines.append("    # ⚠ 网络断言缺少请求匹配条件（reqMethod/reqUrl），已拒发")
                problems.append(
                    f"step#{i} (id={sid}): 网络断言缺 reqMethod/reqUrl，拒发而不是猜")
                ok = False
            if ok and a == "request_count" and not (
                    isinstance(exp, int) and not isinstance(exp, bool) and exp == 0):
                # request_count 只支持「应为 0」的负断言；非 0 计数无法给出
                # 可靠观察窗（窗内没有匹配请求也可能是请求还没发）—— 拒发。
                lines.append(f"    # ⚠ request_count 仅支持期望 0，收到 {exp!r}，已拒发")
                problems.append(
                    f"step#{i} (id={sid}): request_count 仅支持期望 0，收到 {exp!r}")
                ok = False
            if ok:
                pass            # 已在预扫描阶段进 _network_by_action，等动作 emit
            # 不在这里放 rec-assert 标记：断言的比较行在因果动作的等待块里
            # （_emit_network_assert 已带标记），这里再放会重复计数。
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
            continue

        # ── 页面导航事件（reload / 前进后退）：无 DOM 目标，直接落在 page 上 ──
        # 这类步骤没有选择器；丢掉它，回放的状态就和录制时对不上（历史导航会
        # 清掉未提交的输入、重载会把页面打回初始态）。未知 navType 不猜 ——
        # 拒发整份 spec，与未知断言/动作同一红线。
        #
        # 断言菜单的 afterStepId 绑「最近一步」，navigate 步骤就是合法的因果
        # 动作（reload 后不发请求的负断言真实可达）—— 必须走 emit_action 领走
        # 自己名下的网络断言并生成观察窗，不然 leftover 检查拒发整份 spec，
        # 而 trace 链的 Navigate 节点照常接受 —— 同一录制两链不同判。
        if s["type"] == "navigate":
            nav = s.get("navType")
            if nav == "reload":
                nav_expr = "page.reload()"
            elif nav == "back_forward":
                nav_expr = "page.go_back()"
            else:
                lines.append(f"    # ⚠ 未知导航类型 {nav!r}，已拒发")
                problems.append(
                    f"step#{i} (id={s.get('id')}): 未知导航类型 {nav!r} —— "
                    "回放会少做一次导航，拒发而不是静默跳过")
                if s.get("id"):
                    lines.append(f"    # rec-step: {s['id']}")
                for c in calls:
                    lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
                continue
            lines.extend(emit_action(
                nav_expr, calls, "",
                network_asserts=_network_by_action.pop(s.get("id"), None)))
            if s.get("id"):
                lines.append(f"    # rec-step: {s['id']}")
            continue

        # ── 页面关闭（Ctrl+W / 关标签页 / window.close）：无 DOM 目标 ──
        # 丢掉它，回放终态比录制多一个标签页 —— page_count 类断言（双链共用
        # expect_page_count）会判成与录制相反的结论。关闭对象按 pageId 从
        # recorded_pages 取：当前变量 page 可能正指在被关的 popup 上，直接
        # page.close() 碰巧也对，但换一步骤顺序就错；绑定录制身份才是事实。
        if s["type"] == "page_close":
            target_id = s.get("pageId")
            if not target_id:
                lines.append("    # ⚠ page_close 缺少 pageId，已拒发")
                problems.append(
                    f"step#{i} (id={s.get('id')}): page_close 缺少 pageId —— "
                    "不知道关哪个页面，拒发而不是猜")
            else:
                lines.append(f"    recorded_pages[{_lit(target_id)}].close()")
            if s.get("id"):
                lines.append(f"    # rec-step: {s['id']}")
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
            continue

        try:
            sel = to_python(s["sel"])
        except SelectorError as e:
            lines.append(f"    # ⚠ 无法转译选择器 {s['sel']!r}：{e}")
            problems.append(
                f"step#{i} (id={s.get('id')}): 无法转译选择器 {s['sel']!r}：{e}")
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
            continue

        # ── 断言步骤 ──
        # expected 一律从录制数据里取，不在生成时重新推导。
        if s["type"] == "assert":
            dyn = s.get("expectedFrom") or {}
            # P0（common-web-assertion-design §5/§6）：需要静态 expected 的断言，
            # expected 必须已由用户确认。老录制缺 expectedConfirmed 字段视为已确认
            # （兼容）；只有显式 expectedConfirmed=false 才拒发整份 spec —— 系统
            # 不得把「当前看到什么」直接当作用户意图。
            a0, op0, exp0 = s.get("assertion"), s.get("op"), s.get("expected")
            needs_expected = (
                a0 in ("text", "value", "attribute", "count", "visible", "checked",
                       "enabled", "editable", "selected")
                and op0 not in ("is_blank", "is_not_blank") and not dyn
            )
            if needs_expected and s.get("expectedConfirmed") is False:
                lines.append("    # ⚠ 需要 expected 但 expectedConfirmed=false，已拒发")
                problems.append(
                    f"step#{i} (id={s.get('id')}): 断言需要 expected 但 "
                    "expectedConfirmed=false —— expected 未获用户确认，拒发而不是少断言")
                continue
            loc = f"{root}.{sel}"
            a, exp = s.get("assertion"), s.get("expected")
            if dyn.get("kind") == LOCALTIME_KIND:
                # 期望值由回放那一刻的本机时钟决定，不用录制时的字面量。
                # 录制时看到的值留在注释里作证据 —— 它说明当时那个位置显示的是时间。
                fmt = dyn.get("format") or "%H:%M"
                match = dyn.get("match") or "contains"
                lines.append(f"    # 录制时这里显示 {_lit(exp)}；断言改为「回放此刻的本机时间」，")
                lines.append("    # 否则这个字段每刷新一次，钉死的字面量就红一次")
                if s.get("cellAnchor"):
                    lines.append(f"    # 选择器锚在同行的 {_lit(s['cellAnchor'])} 上按列取值 ——")
                    lines.append("    # 不能锚在这段时间文本自己身上，它一刷新元素就没了")
                elif exp and str(exp) in str(s.get("sel") or ""):
                    # 锚换不掉时必须喊出来：这个断言回放一定失败，而且失败原因
                    # 会显示成「找不到元素」，指不到真正的问题上。
                    lines.append("    # ⚠ 这个选择器把录到的时间文本包在里面了，"
                                 "回放时该元素已不存在 ——")
                    lines.append("    #   断言会以「找不到元素」失败，而不是「时间不对」。"
                                 "请改成锚在稳定字段上，")
                    lines.append("    #   例如 page.locator(\"tr\", has_text=\"<稳定文本>\")"
                                 ".locator(\"td\").nth(<列号>)")
                read = "value" if a == "value" else "text"
                extra = f", read={_lit(read)}" if read == "value" else ""
                lines.append(f"    expect_local_time({loc}, {_lit(fmt)}, "
                             f"match={_lit(match)}{extra}){warn}")
            elif dyn.get("kind") == TIMEWINDOW_KIND:
                fmt = dyn.get("format") or "%H:%M"
                fluctuation = dyn.get("fluctuationMinutes") or 0
                read = "value" if a == "value" else "text"
                extra = f", read={_lit(read)}" if read == "value" else ""
                lines.append(
                    f"    expect_time_window({loc}, {_lit(exp)}, {_lit(fmt)}, "
                    f"fluctuation_minutes={_lit(fluctuation)}{extra}){warn}"
                )
            elif dyn.get("kind") == TIMETRANGE_KIND:
                # 期望值是一段「时间区间」：[起点, 起点+时长) 半开区间，
                # 比的是元素文本里时间记号的**值**（记号可在文本任意位置）。
                read = "value" if a == "value" else "text"
                extra = f", read={_lit(read)}" if read == "value" else ""
                duration = dyn.get("durationSeconds")
                if (duration is not None
                        and (isinstance(duration, bool)
                             or not isinstance(duration, int)
                             or not 1 <= duration <= 86400)):
                    # 非法时长（0 / 非整数 / 越界）不能静默当缺省 —— 拒发。
                    lines.append(f"    # ⚠ 时间区间时长非法 {_lit(duration)}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): 时间区间时长非法 "
                        f"{duration!r}（需 1–86400 的整数秒，或省略）")
                else:
                    # 缺省时长不写参数（helper 自己按起点粒度取 60/1 秒缺省）；
                    # 空位置参数再接关键字参数是 SyntaxError。
                    dur_arg = f", {duration}" if duration is not None else ""
                    lines.append(
                        f"    expect_time_range({loc}, {_lit(exp)}{dur_arg}{extra}){warn}"
                    )
            elif a == "count":
                # op 缺省 equals 只对「字段不存在」成立（老录制没有该字段；
                # 录制器也从不写空串/null —— 只在显式选了非 equals 时落字段）。
                # 显式给坏的（空串/None/非字符串/未知）与 text 分支同构：
                # 进 problems 拒发整份 spec，不能静默回退成 equals。
                op = s["op"] if "op" in s else "equals"
                if not (isinstance(op, str) and op in COUNT_OPS):
                    lines.append(f"    # ⚠ count 不支持算子 {op!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): count 不支持算子 {op!r}")
                else:
                    w_args = _wait_kwargs(s, default_stable_ms=300 if exp == 0 else 0)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    expect_count({loc}, {_lit(exp)}, op={_lit(op)}{arg}){warn}"
                    )
            elif a == "text":
                # 语义对齐说明（2026-10-21 验收修正）：
                #   * to_have_text(字符串) = 全串 + 空白归一化 —— equals/not_equals 直接用；
                #   * to_contain_text = 归一化子串 —— contains/not_contains 直接用；
                #   * 但 to_have_text(正则) 比的是**原始**文本（不归一化），裸
                #     re.compile("abc") 还会按搜索语义放过 "prefix abc suffix"。
                # 所以 starts_with / ends_with / matches / is_blank / is_not_blank
                # 不走 Playwright 正则，而是读 inner_text 后走**与回放同一个**
                # 纯比较 matches_assert（回放链 _assert_locator 的同款调用）——
                # 两边共享一份 fullmatch / 前后缀 / 空白定义，判定必然一致。
                op = s.get("op") or "equals"
                if op == "equals":
                    lines.append(f"    expect({loc}).to_have_text({_lit(exp)}, "
                                 f"use_inner_text=True){warn}")
                elif op == "contains":
                    lines.append(f"    expect({loc}).to_contain_text({_lit(exp)}, "
                                 f"use_inner_text=True){warn}")
                elif op in ("not_equals", "not_contains"):
                    # F2（审查 2026-10-29）：负文本断言（not_equals/not_contains）
                    # 不能走 Playwright 立即负断言（not_to_have_text /
                    # not_to_contain_text）—— 那表达不了 §6 的稳定窗口，会与回放链
                    # 分叉、且「先缺席后出现」的中间态会假通过。改走共享的
                    # expect_text（matches_assert + 统一 deadline/stable 轮询），
                    # 与回放链同一比较核心；负断言默认带 stable（_wait_kwargs 兜底
                    # 300ms，或录制端显式 wait）。
                    w_args = _wait_kwargs(s, default_stable_ms=300)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    expect_text({loc}, {_lit(exp)}, op={_lit(op)}{arg}){warn}"
                    )
                elif op in ("starts_with", "ends_with", "matches",
                            "is_blank", "is_not_blank"):
                    blank_no_expected = op in ("is_blank", "is_not_blank")
                    expected_lit = "None" if blank_no_expected else _lit(exp)
                    # F9（评审 2026-10-23）：元素读取失败（缺失 / 轮询中途 detach，
                    # Playwright 一律以 TimeoutError 呈现）不是观测值。旧模板把读取
                    # 内联进 lambda，异常从 poll_until 直接冒泡 —— 生成链失败形态是
                    # error，而回放链（replay_trace._assert_locator）捕获读取异常
                    # 轮询到超时，是干净的 AssertionError，§7.1 的 failure_category
                    # 归因因此分叉。修复：闭包**局部**捕获读取异常返回 False 哨兵，
                    # 让 poll_until 继续轮询、超时后走既有 AssertionError 路径。
                    # 约束：try 只包读取本身，比较（matches_assert）在 try 外 ——
                    # 编程错误照常冒泡；哨兵在比较**之前**短路返回，is_not_blank
                    # 不能拿哨兵当「非空」假通过。
                    text_seq += 1
                    closure = f"_text_assert_{text_seq}"
                    lines.append(f"    def {closure}():")
                    lines.append("        try:")
                    lines.append(f"            text = {loc}.inner_text("
                                 f"timeout={ASSERT_READ_TIMEOUT_MS})")
                    lines.append("        except PlaywrightTimeoutError:")
                    lines.append("            return False")
                    lines.append(
                        f"        return matches_assert(text, {expected_lit}, {_lit(op)})"
                    )
                    diagnosis = (
                        f"文本断言失败: op={op}"
                        + ("" if blank_no_expected else f" expected={exp!r}")
                        + f" locator={loc}: "
                    )
                    lines.append("    try:")
                    lines.append(f"        poll_until({closure}, True){warn}")
                    lines.append("    except AssertionError as e:")
                    lines.append("        raise AssertionError(")
                    lines.append(f"            {_lit(diagnosis)} + str(e)) from e")
                    lines.append(
                        "    # ↑ 与回放链共用 matches_assert 的比较定义（见 rec_assert）；"
                        "读取失败按「本轮未满足」继续轮询"
                    )
                    lines.append(
                        "    #   （F9：失败形态与回放链对齐为 AssertionError）"
                    )
                else:
                    lines.append(f"    # ⚠ 文本不支持算子 {_lit(op)}，已跳过")
                    problems.append(f"step#{i}: text 不支持算子 {op!r}")
            elif a == "value":
                # 与 count 分支同构：坏算子（空串/None/非字符串/未知）进
                # problems 拒发，不静默回退；合法面即 rec_assert.VALUE_OPS。
                op = s["op"] if "op" in s else "equals"
                if not (isinstance(op, str) and op in VALUE_OPS):
                    lines.append(f"    # ⚠ value 不支持算子 {op!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): value 不支持算子 {op!r}")
                elif op == "equals":
                    lines.append(f"    expect({loc}).to_have_value({_lit(exp)}){warn}")
                else:
                    w_args = _wait_kwargs(s)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(f"    expect_value({loc}, {_lit(exp)}, op={_lit(op)}{arg}){warn}")
            elif a == "visible":
                if s.get("_wasTextTautology"):
                    lines.append(
                        f"    # 录制时选的是「文本等于 {s['_wasTextTautology']}」，"
                        f"而元素本身就是按这段文本找到的 ——"
                    )
                    lines.append(
                        "    # 断言它的文本等于自己，只有元素消失才会失败。"
                        "如实生成存在性断言"
                    )
                if exp:
                    lines.append(f"    expect({loc}).to_be_visible(){warn}")
                else:
                    # 负断言（不可见）：稳定窗口防「先缺席后出现」的中间态抢跑（§6）
                    w_args = _wait_kwargs(s, default_stable_ms=300)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    expect_state({loc}, False, \"visible\"{arg}){warn}"
                    )
            elif a == "checked":
                if exp:
                    lines.append(f"    expect({loc}).to_be_checked(){warn}")
                else:
                    # 负断言（未勾选）同样需要稳定窗口
                    w_args = _wait_kwargs(s, default_stable_ms=300)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    expect_state({loc}, False, \"checked\"{arg}){warn}"
                    )
            elif a in ("enabled", "editable", "selected"):
                # P1：布尔状态断言统一定位到 expect_state（与回放链同核心）
                w_args = _wait_kwargs(s, default_stable_ms=0)
                arg = f", {w_args}" if w_args else ""
                lines.append(
                    f"    expect_state({loc}, {_lit(exp)}, {_lit(a)}{arg}){warn}"
                )
            elif a == "attribute":
                # 与 count/value 分支同构：坏算子进 problems 拒发。
                op = s["op"] if "op" in s else "equals"
                if not (isinstance(op, str) and op in VALUE_OPS):
                    lines.append(f"    # ⚠ attribute 不支持算子 {op!r}，已拒发")
                    problems.append(
                        f"step#{i} (id={s.get('id')}): attribute 不支持算子 {op!r}")
                elif op == "equals":
                    lines.append(f"    expect({loc}).to_have_attribute("
                                 f"{_lit(s.get('attribute'))}, {_lit(exp)}){warn}")
                else:
                    w_args = _wait_kwargs(s)
                    arg = f", {w_args}" if w_args else ""
                    lines.append(
                        f"    expect_attribute({loc}, {_lit(s.get('attribute'))}, "
                        f"{_lit(exp)}, op={_lit(op)}{arg}){warn}"
                    )
            else:
                lines.append(f"    # ⚠ 未知断言类型 {_lit(a)}，已跳过")
                problems.append(
                    f"step#{i} (id={s.get('id')}): 未知断言类型 {_lit(a)} —— "
                    "这条断言在生成的用例里不存在，拒发而不是少断言")
            # P0（§8）：成功生成的 spec 里每条断言都带上它的录制 id，供三产物
            # （recording / pytest / trace）断言 id 与数量的一致性校验读取。
            if s.get("id"):
                lines.append(f"    # rec-assert: {s['id']}")
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
            continue

        # 只有录制时观察到的条件步骤才「有就点」。CSS 只是定位手段。
        if (
            s["type"] == "click"
            and (s.get("dismissesOverlay") or s.get("id") in gated_ids)
        ):
            el_seq += 1
            el = f"_el{el_seq}"
            lines.append("    # 条件步骤：录制时证实它用于关浮层或完成门控交互")
            lines.append(f"    {el} = {root}.{sel}")
            lines.append(f"    if is_present({el}):")
            # 等确认元素存在后再建立响应等待，避免可选弹窗未出现时空等到超时
            for line in emit_action(f"{el}.click()", calls, warn):
                lines.append("    " + line)
            continue

        # ── 开关：拨到指定状态，而不是盲目点一下 ──
        if s["type"] == "switch":
            sw_seq += 1
            n = sw_seq
            v = "True" if s.get("to") else "False"
            via = s.get("via") or {}
            lines.append(f"    sw{n} = {root}.{sel}")
            # 可点的是外层（有名字、点得动），状态常写在内层。点归点，读归读。
            st = f"sw{n}"
            if via.get("within"):
                lines.append(f"    state{n} = sw{n}.locator({_lit(via['within'])}).first")
                st = f"state{n}"
            if via.get("type") == "class":
                t = json.dumps(via.get("token"), ensure_ascii=False)
                expr = (f'!e.classList.contains({t})' if via.get("polarity") == "off"
                        else f'e.classList.contains({t})')
                lines.append(f"    is_on{n} = lambda: {st}.evaluate({_lit(f'(e) => {expr}')})")
                lines.append(f"    if is_on{n}() != {v}:")
                lines.append(f"        sw{n}.click()")
                lines.append(f"    poll_until(is_on{n}, {v})")
            elif via.get("type") == "checked":
                lines.append(f"    if {st}.is_checked() != {v}:")
                lines.append(f"        sw{n}.click()")
                lines.append(f"    expect({st})."
                             f"{'to_be_checked()' if s.get('to') else 'not_to_be_checked()'}")
            else:
                lines.append(f"    if {st}.get_attribute(\"aria-checked\") != {_lit(v.lower())}:")
                lines.append(f"        sw{n}.click()")
                lines.append(f"    expect({st}).to_have_attribute("
                             f"\"aria-checked\", {_lit(v.lower())})")
            if warn:
                lines.append(f"   {warn.strip()}")
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
                if c["status"] >= 400 and c.get("body"):
                    body = re.sub(r"\s+", " ", c["body"][:160])
                    lines.append(f"    #     ⚠ 失败响应: {body}")
            continue

        loc = f"{root}.{sel}"
        # 录制器的歧义计数有意包含隐藏副本（许多 Playwright 定位器也会包含）。
        # 但真正执行动作时，先排除隐藏副本通常就只剩一个业务控件；这样比对一个
        # 超宽、近纯色的输入框做视觉匹配可靠。多个可见候选仍会 strict-mode 失败，
        # click 随后才交给 visual_click 消歧，不会静默挑 first。
        action_loc = f"{loc}.filter(visible=True)" if s.get("ambiguous") else loc
        input_loc = (
            f'{root}.locator(":focus")' if s.get("_useFocusedElement") else action_loc
        )
        t = s["type"]
        visual_ui = {
            key: s.get("ui", {}).get(key)
            for key in ("pageRect", "click", "templates")
            if s.get("ui", {}).get(key) is not None
        }
        if t in ("click", "dblclick") and visual_ui.get("templates"):
            action = (
                f"visual_click(page, {action_loc}, template_root=Path(__file__).parent, "
                f"ui={visual_ui!r}"
                f"{', click_count=2' if t == 'dblclick' else ''})"
            )
        elif t == "click":
            action = f"{action_loc}.click()"
        elif t == "dblclick":
            action = f"{action_loc}.dblclick()"
        elif t == "fill" and s.get("secret"):
            action = f'{input_loc}.fill(os.environ["REC_PASSWORD"])'
        elif t == "fill" and (s.get("valueFrom") or {}).get("kind") == LOCALTIME_KIND:
            # 填的是日期：按「相对录制当天几天」回放，而不是钉死那一天。
            # 死值不会报错，只会悄悄查错区间 —— 这类错最难发现。
            vf = s["valueFrom"]
            off = int(vf.get("offsetDays") or 0)
            when = "当天" if off == 0 else f"{abs(off)} 天{'前' if off < 0 else '后'}"
            lines.append(f"    # 录制时填的是 {_lit(s.get('value'))}（{when}）；"
                         f"改为按回放当天算，")
            lines.append("    # 否则这个筛选区间会随时间漂移，用例照样绿但查的不是同一段")
            args = f"{_lit(vf.get('format') or '%Y-%m-%d')}"
            if off:
                args += f", offset_days={off}"
            action = f"{input_loc}.fill(local_time_value({args}))"
        elif t == "fill":
            action = f"{input_loc}.fill({_lit(s.get('value') or '')})"
        elif t == "check":
            action = f"{action_loc}.check()"
        elif t == "uncheck":
            action = f"{action_loc}.uncheck()"
        elif t == "press":
            action = f'{input_loc}.press("Enter")'
        else:
            # 与未知断言同一红线：动作被静默丢掉 = 用例少做一件事还跑绿。
            problems.append(
                f"step#{i} (id={s.get('id')}): 未知动作类型 {_lit(t)} —— "
                "回放会少做这一步，拒发而不是静默跳过")
            continue

        next_step = steps[i + 1] if i + 1 < len(steps) else None
        opens_popup = bool(
            t in ("click", "dblclick")
            and s.get("pageId")
            and next_step
            and next_step.get("pageId") != s.get("pageId")
            and next_step.get("openerPageId") == s.get("pageId")
        )
        emitted = emit_action(
            action, calls, warn,
            network_asserts=_network_by_action.pop(s.get("id"), None))
        if opens_popup:
            popup_seq += 1
            info = f"popup{popup_seq}_info"
            lines.append(f"    with page.expect_popup() as {info}:")
            lines.extend("    " + line for line in emitted)
            lines.append(
                f"    recorded_pages[{_lit(next_step['pageId'])}] = {info}.value"
            )
        else:
            lines.extend(emitted)

    # P3a：业务与清理**结构隔离**（平台规范 §6.6）。
    # 业务段进 try 先行定案；清理在独立函数、finally 里独立运行，
    # 自身失败只告警 —— 清理挂掉不能翻掉业务 PASS/FAIL，
    # 业务挂了清理也要跑。旧模板只有注释建议用户自己塞 try/finally，
    # 清理失败可以直接盖掉业务失败。
    try_idx = next(i for i, ln in enumerate(lines)
                   if ln.startswith("    page.goto("))
    lines[try_idx:try_idx] = ["    try:"]
    lines[try_idx + 1:] = [("    " + ln if ln else ln)
                           for ln in lines[try_idx + 1:]]
    lines += [
        "    finally:",
        "        _run_cleanup(page)",
        "",
        "",
        "def _run_cleanup(page):",
        '    """清理动作（录制后手写，仅当本用例会写数据）。',
        "",
        "    finally 里独立运行：业务失败它仍要跑；它自己的失败只",
        "    记一条告警，绝不推翻业务结论（平台规范 §6.6）。",
        '    """',
        "    try:",
        "        pass  # TODO cleanup: 清理动作写在这里",
        "    except Exception as e:",
        '        print(f"⚠ cleanup failed（不影响业务结论）: '
        '{type(e).__name__}: {e}")',
    ]

    lines.append("")
    # 网络断言挂靠的动作步骤不存在（afterStepId 指向被丢弃/不存在的步骤）→
    # 观察起点无从确定，拒发整份 spec —— 不能静默丢断言。
    for after_id, nas in _network_by_action.items():
        for na in nas:
            problems.append(
                f"网络断言 (id={na.get('id')}) 的因果动作步骤 {after_id!r} "
                "不在生成后的步骤序列里 —— 无法确定观察起点，拒发而不是猜")
    if problems:
        raise SpecGenerationError(problems)
    return "\n".join(lines)
