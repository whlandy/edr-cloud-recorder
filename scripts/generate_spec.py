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

from selector_py import to_python

# 易变值：UUID 和 10 位以上纯数字（雪花 ID、毫秒时间戳）
VOLATILE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{10,})$",
    re.I,
)

HEADER = '''import os

from playwright.sync_api import Page, expect

from rec_assert import ANY_NUM, ANY_STR, assert_subset, poll_until
from rec_helpers import dismiss_overlays, is_present, nth_request

# 由 web-record 生成：{name}
# 写请求已自动生成断言（状态码 + 请求体形态）；GET 保留为注释。
# 请求体里的 UUID / 长数字 ID / 时间戳已放宽，避免每次运行都失效。
#
# 用 authed_page 而不是裸 page：登录态由 conftest.py 存一次、这里复用。
# 首启弹窗由 dismiss_overlays 统一关掉 —— 它们的遮罩会静默吞掉后续点击，
# 而失败会报在后面某个 expect_response 上，看着像「接口没发」。
#
# 仍需人工处理：
#   1. 收紧仍标着 AMBIGUOUS 的选择器（多数已自动加了作用域）
#   2. 删掉与意图无关的误操作步骤
#   3. 会产生数据的用例补上清理逻辑（建议放 try/finally，中途失败也能还原）
'''

# 开头那段登录：几乎必然录进来（录制从登录页开始），也几乎必然回放不了 ——
# 登录表单常在 iframe 里、常有同 placeholder 的诱饵输入框、密码又不该写进脚本。
# 而它对用例的意图毫无贡献，只是让每条用例都多一个失败点。
LOGIN_FRAME = re.compile(r"login|signin|sso", re.I)


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
    if isinstance(v, str) and VOLATILE.match(v):
        return "ANY_STR"
    # 数字型的时间戳和雪花 ID 也要放宽。只处理字符串的话，像「最近 30 天」这种
    # 默认查询条件会把录制那一刻的毫秒时间戳原样钉进断言 —— 下一次运行必然对不上，
    # 而这恰恰是上面那段注释声称要防住的失效方式。
    # 门槛取 1e9：10 位是秒级时间戳，13 位是毫秒级，业务上的页码、数量都远小于它。
    if isinstance(v, int) and abs(v) >= 1_000_000_000:
        return "ANY_NUM"
    return json.dumps(v, ensure_ascii=False)


def generate_spec(steps, net, start_url, name):
    parts = urlsplit(start_url)
    origin = f"{parts.scheme}://{parts.netloc}"

    def strip(u):
        return u.replace(origin, "")

    def between(a, b):
        return [n for n in net if n["phase"] == "res" and a <= n["t"] < b]

    # Playwright 的 response 能直接拿到它的 request，但落盘后的录制数据没有对象引用。
    # 按 method + URL 为每条请求维护 FIFO 队列，恢复响应与请求的一一对应关系。
    # 不能简单找「响应之前最后一条同 URL 请求」：同一操作并发发两次相同请求时，
    # 那会让两个响应都错误地关联到第二条请求。
    request_of: dict[int, dict] = {}
    by_id = {n["id"]: n for n in net if n["phase"] == "req" and n.get("id") is not None}
    pending: dict[str, list] = {}
    # 同一时刻的 req 排在 res 之前，否则同 ms 的请求响应会配错
    for ev in sorted(net, key=lambda n: (n["t"], 0 if n["phase"] == "req" else 1)):
        key = f"{ev['method']}\n{ev['url']}"
        if ev["phase"] == "req":
            pending.setdefault(key, []).append(ev)
        elif ev["phase"] == "res":
            req = by_id.get(ev.get("requestId"))
            if req is None:
                queue = pending.get(key) or []
                req = queue.pop(0) if queue else None
            if req is not None:
                request_of[id(ev)] = req

    def req_of(res):
        return request_of.get(id(res))

    def req_body_of(res):
        r = req_of(res)
        if not r or not r.get("body"):
            return None
        try:
            return json.loads(r["body"])
        except (ValueError, TypeError):
            return None

    # ── 修正「先回车、后填值」──
    # 值是在 change 里记的（要等失焦或回车之后），按键是按下就记。
    # 同一字段上「先回车、再填值」不可能成立，直接交换，不设时间阈值。
    steps = list(steps)
    for i in range(len(steps) - 1):
        a, b = steps[i], steps[i + 1]
        if (a["type"] == "press" and b["type"] == "fill"
                and a["sel"] == b["sel"] and a.get("inFrame") == b.get("inFrame")):
            # 时间戳跟着换，否则接口挂载的时间窗口会倒过来
            steps[i] = {**b, "t": a["t"]}
            steps[i + 1] = {**a, "t": b["t"]}

    # ── 丢掉开头那段登录 ──
    #
    # 只砍**开头连续**的那一段：登录之后再出现的 iframe 操作是正经业务。
    # 砍掉的步骤原样留在注释里，需要时能捡回来。
    def is_login_step(s) -> bool:
        return bool(s.get("secret") is True or (
            s.get("inFrame") and LOGIN_FRAME.search(s.get("framePath") or "")))

    cut = 0
    while cut < len(steps) and is_login_step(steps[cut]):
        cut += 1
    # 登录段后面常紧跟着「按回车提交」，它和登录是一体的
    if (0 < cut < len(steps) and steps[cut]["type"] == "press"
            and steps[cut].get("inFrame")):
        cut += 1
    dropped, steps = steps[:cut], steps[cut:]

    head = [HEADER.format(name=name).rstrip("\n")]
    if dropped:
        head.append("#")
        head.append(f"# 已自动去掉开头 {len(dropped)} 步登录（改用登录态复用）。原步骤：")
        for s in dropped:
            tail = "  <密码，未记录>" if s.get("secret") else ""
            head.append(f"#   {s['type']} {s['sel']}{tail}")

    lines = [*head, "",
             f"def test_{_ident(name)}(authed_page: Page):",
             "    page = authed_page",
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

        warn = (f"   # ⚠ AMBIGUOUS: {s.get('matches')} 个元素匹配，回放时可能点错"
                if s.get("ambiguous") else "")

        # iframe 里的元素必须先进 frame。用 src 片段定位 iframe：比 nth 稳，
        # 也比整条 src 宽容（src 常带随机 query）。
        if s.get("inFrame") and s.get("framePath"):
            tail = s["framePath"].split("/")[-1]
            root = f'page.frame_locator({_lit(f"iframe[src*=\x22{tail}\x22]")})'
        else:
            root = "page"

        sel = to_python(s["sel"])

        # ── 断言步骤 ──
        # expected 一律从录制数据里取，不在生成时重新推导。
        if s["type"] == "assert":
            loc = f"{root}.{sel}"
            a, exp = s.get("assertion"), s.get("expected")
            if a == "text":
                lines.append(f"    expect({loc}).to_have_text({_lit(exp)}){warn}")
            elif a == "value":
                lines.append(f"    expect({loc}).to_have_value({_lit(exp)}){warn}")
            elif a == "visible":
                lines.append(f"    expect({loc}).to_be_visible(){warn}" if exp
                             else f"    expect({loc}).to_be_hidden(){warn}")
            elif a == "checked":
                lines.append(f"    expect({loc}).to_be_checked(){warn}" if exp
                             else f"    expect({loc}).not_to_be_checked(){warn}")
            elif a == "attribute":
                lines.append(f"    expect({loc}).to_have_attribute("
                             f"{_lit(s.get('attribute'))}, {_lit(exp)}){warn}")
            else:
                lines.append(f"    # ⚠ 未知断言类型 {_lit(a)}，已跳过")
            for c in calls:
                lines.append(f"    #   ↳ {c['method']} {strip(c['url'])} -> {c['status']}")
            continue

        # CSS 兜底基本都是关弹窗/提示条这类「有就点、没有就跳过」的动作。
        # 生成成必经步骤会让脚本在弹窗不出现时直接失败。
        if s.get("kind") == "css" and s["type"] == "click":
            el_seq += 1
            el = f"_el{el_seq}"
            lines.append("    # ⚠ CSS 兜底（元素没有 role/label/稳定文本），建议改用语义定位")
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
        t = s["type"]
        if t == "click":
            action = f"{loc}.click()"
        elif t == "fill" and s.get("secret"):
            action = f'{loc}.fill(os.environ.get("REC_PASSWORD", ""))'
        elif t == "fill":
            action = f"{loc}.fill({_lit(s.get('value') or '')})"
        elif t == "check":
            action = f"{loc}.check()"
        elif t == "uncheck":
            action = f"{loc}.uncheck()"
        elif t == "press":
            action = f'{loc}.press("Enter")'
        else:
            continue

        lines.extend(emit_action(action, calls, warn))

    lines.append("")
    return "\n".join(lines)
