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
from urllib.parse import urlsplit

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

from playwright.sync_api import Page, expect

from rec_assert import (
    ANY_NUM, ANY_STR, assert_subset, expect_local_time, expect_time_window,
    expect_count, expect_value, expect_attribute, expect_time_range,
    local_time_value, poll_until,
)
from rec_helpers import dismiss_overlays, is_present, nth_request
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

    lines = [*head, "",
             f"def test_{_ident(name)}(recorded_page: Page):",
             "    page = recorded_page",
             f"    page.goto({_lit(strip(start_url) or '/')})",
             "    dismiss_overlays(page)"]

    resp_seq = 0
    sw_seq = 0
    el_seq = 0

    def emit_action(action_expr, calls, warn):
        """一次操作；若触发了写请求，包成「等响应 + 断言」。"""
        nonlocal resp_seq
        # 按请求**发出**次序生成等待，而不是响应完成次序 —— 并发请求可能后发先回
        writes = sorted(
            (c for c in calls if c["method"] != "GET" and c["status"] < 400),
            key=lambda c: (req_of(c) or c)["t"],
        )
        if not writes:
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
        for i, w in enumerate(writes):
            p = strip(w["url"]).split("?")[0]
            key = f"{w['method']}\n{p}"
            nth = occurrences.get(key, 0) + 1
            occurrences[key] = nth
            pad = "    " + "    " * i
            out.append(
                f"{pad}with page.expect_request("
                f"nth_request({_lit(p)}, {_lit(w['method'])}, {nth})"
                f") as {req_names[i]}_info:"
            )
        inner = "    " + "    " * len(writes)
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
        return out

    # 主文档地址，只由主文档里的步骤推进。
    #
    # 录制器记的 url 是 `location.pathname + hash`，在 iframe 里就是**iframe 自己的**
    # 地址。拿它跟主文档地址比，会得出「地址变成 /login_frame.html 了」这种结论，
    # 进而建议人 page.goto("/login_frame.html") —— 那会把主页面整个换掉。
    # 而且 iframe 地址若留作下一步的比较基准，紧接着还会再误报一次。
    # 所以 in-frame 的步骤既不报变化，也不更新基准。
    last_top_url = None

    # 无法如实转译的步骤：收集全部问题，最后统一拒发（SpecGenerationError）。
    # 注释照写 —— 错误文本里已有诊断，代码文本只是第二份证据。
    problems: list[str] = []
    for i, s in enumerate(steps):
        hi = steps[i + 1]["t"] if i + 1 < len(steps) else float("inf")
        calls = between(s["t"], hi)

        in_frame = bool(s.get("inFrame"))

        # 地址已变 —— 提示可以直接 goto，避开侧边栏菜单在小 viewport 下要滚动的问题
        prev_url = last_top_url
        if not in_frame and s.get("url"):
            last_top_url = s["url"]
        if not in_frame and prev_url and s.get("url") and s["url"] != prev_url:
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
            loc = f"{root}.{sel}"
            a, exp = s.get("assertion"), s.get("expected")
            dyn = s.get("expectedFrom") or {}
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
                duration = dyn.get("durationSeconds")
                read = "value" if a == "value" else "text"
                extra = f", read={_lit(read)}" if read == "value" else ""
                dur_lit = _lit(duration) if duration else ""
                lines.append(
                    f"    expect_time_range({loc}, {_lit(exp)}, {dur_lit}{extra}){warn}"
                )
            elif a == "count":
                op = s.get("op") or "equals"
                lines.append(
                    f"    expect_count({loc}, {_lit(exp)}, op={_lit(op)}){warn}"
                )
            elif a == "text":
                op = s.get("op") or "equals"
                if op == "equals":
                    lines.append(f"    expect({loc}).to_have_text({_lit(exp)}){warn}")
                elif op == "not_equals":
                    lines.append(f"    expect({loc}).not_to_have_text({_lit(exp)}){warn}")
                elif op == "contains":
                    lines.append(f"    expect({loc}).to_contain({_lit(exp)}){warn}")
                elif op == "not_contains":
                    lines.append(f"    expect({loc}).not_to_contain({_lit(exp)}){warn}")
                elif op == "starts_with":
                    lines.append(f"    expect({loc}).to_have_text("
                                 f"re.compile({_lit(r'^' + re.escape(str(exp)))})){warn}")
                elif op == "ends_with":
                    lines.append(f"    expect({loc}).to_have_text("
                                 f"re.compile({_lit(re.escape(str(exp)) + r'$')})){warn}")
                elif op == "matches":
                    lines.append(f"    expect({loc}).to_have_text(re.compile({_lit(exp)})){warn}")
                elif op == "is_blank":
                    lines.append(f"    expect({loc}).to_have_text(re.compile(r'^\\s*$')){warn}")
                elif op == "is_not_blank":
                    lines.append(f"    expect({loc}).not_to_have_text(re.compile(r'^\\s*$')){warn}")
                else:
                    lines.append(f"    # ⚠ 文本不支持算子 {_lit(op)}，已跳过")
                    problems.append(f"step#{i}: text 不支持算子 {op!r}")
            elif a == "value":
                op = s.get("op") or "equals"
                if op == "equals":
                    lines.append(f"    expect({loc}).to_have_value({_lit(exp)}){warn}")
                else:
                    lines.append(f"    expect_value({loc}, {_lit(exp)}, op={_lit(op)}){warn}")
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
                lines.append(f"    expect({loc}).to_be_visible(){warn}" if exp
                             else f"    expect({loc}).to_be_hidden(){warn}")
            elif a == "checked":
                lines.append(f"    expect({loc}).to_be_checked(){warn}" if exp
                             else f"    expect({loc}).not_to_be_checked(){warn}")
            elif a == "attribute":
                op = s.get("op") or "equals"
                if op == "equals":
                    lines.append(f"    expect({loc}).to_have_attribute("
                                 f"{_lit(s.get('attribute'))}, {_lit(exp)}){warn}")
                else:
                    lines.append(f"    expect_attribute({loc}, {_lit(s.get('attribute'))}, "
                                 f"{_lit(exp)}, op={_lit(op)}){warn}")
            else:
                lines.append(f"    # ⚠ 未知断言类型 {_lit(a)}，已跳过")
                problems.append(
                    f"step#{i} (id={s.get('id')}): 未知断言类型 {_lit(a)} —— "
                    "这条断言在生成的用例里不存在，拒发而不是少断言")
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

        lines.extend(emit_action(action, calls, warn))

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
    if problems:
        raise SpecGenerationError(problems)
    return "\n".join(lines)
