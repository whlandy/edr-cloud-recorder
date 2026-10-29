"""P1 常用 Web 场景验收（references/common-web-assertion-design.md 阶段 P1 验收）。

五条真实 Chromium fixture —— 页面跳转、表单提交、下拉选择、提示出现/消失、
表格目标行 —— 在 pytest（生成链）与 golden trace（回放链）两条路径都通过；
逐项注入反例时两条路径都失败，且两条链路同判。

每条场景 = 一轮「动作 + 断言」，用一个 recording 步骤集喂给：
  * generate_spec → exec 出 pytest 函数 → 在真实 Chromium 执行（生成链）；
  * generate_trace → replay_trace → 在真实 Chromium 回放（回放链）。
同一个断言步骤在两条链都必须 success（正例）或 failed（反例）。
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect as pw_expect

from chrome_path import resolve_chrome
from generate_spec import generate_spec
from generate_trace import generate_trace
from replay_trace import replay_trace

# popup 的真实导航无法用 route().fulfill() 在假域名上承接（实测新标签页会变成
# chrome-error:// 错误页），所以这里用真本地 HTTP 服务 —— 与 fixture_drive 同款，
# 保证 target=_blank 的新标签页导航真的能被解析。
ROUTES = {
    "/": """<!doctype html><html><head><meta charset="utf-8"><title>首页</title></head><body>
      <button id="submit" onclick="document.getElementById('out').textContent='已提交'">提交</button>
      <div id="out"></div>
      <select id="city"><option value="bj">北京</option><option value="sh">上海</option></select>
      <div id="toast" style="display:none">保存成功</div>
      <button id="fire" onclick="document.getElementById('toast').style.display='block'">触发提示</button>
      <table id="orders">
        <tr><td class="rowkey">ORD-1001</td><td class="rowval">已发货</td></tr>
        <tr><td class="rowkey">ORD-1002</td><td class="rowval">待支付</td></tr>
      </table>
      <a id="go" href="/next">去下一页</a>
      <a id="pop" href="/popup" target="_blank">新标签页</a>
    </body></html>""",
    "/next": """<!doctype html><html><head><meta charset="utf-8"><title>结果页</title></head>
      <body><h1 id="landed">到达目标页</h1></body></html>""",
    "/popup": """<!doctype html><html><head><meta charset="utf-8"><title>详情页</title></head>
      <body><div id="pop-state">详情内容</div></body></html>""",
}


def _start_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = ROUTES.get(self.path, ROUTES["/"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


_ORIGIN = None


@pytest.fixture(scope="module")
def accept_env():
    """浏览器 + 本地 HTTP 服务，模块级共用（不能嵌套 sync_playwright）。

    返回 (browser, origin)：每条场景/每条链各自开新 context+page，
    保证 popup 数、DOM 状态、导航进度都不在链与链之间串扰。
    """
    global _ORIGIN
    srv, origin = _start_server()
    _ORIGIN = origin
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=resolve_chrome())
            yield browser, origin
            browser.close()
    finally:
        srv.shutdown()


def _steps(*items):
    """拼一个含前置动作 + 页面导航 + 断言步骤的录制，按时间序。"""
    return list(items)


def _click(item_id, sel, url="/", page_id="p1", **kw):
    """点击步骤。带一个假模板让 trace 达到 ready（回放走 DOM 定位，模板仅用于
    视觉兜底，DOM 能定位时不需要真实模板文件）。"""
    return {"id": item_id, "t": len(item_id), "type": "click",
            "sel": f'locator("{sel}")', "url": url,
            "pageOrigin": _ORIGIN, "pageId": page_id,
            # 让 generate_trace 产 TemplateMatch + ready：回放走 DOM，模板只作兜底
            "ui": {"templates": {"element": f"{item_id}.png"},
                   "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
                   "pageViewport": {"width": 1280, "height": 720},
                   "deviceScaleFactor": 1},
            **kw}


def _assert(item_id, assertion, expected, sel, *, op=None, scope="element", **kw):
    step = {"id": item_id, "t": len(item_id) + 100, "type": "assert", "scope": scope,
            "assertion": assertion, "expected": expected,
            "expectedConfirmed": True, "sel": f'locator("{sel}")',
            "url": "/", "pageOrigin": _ORIGIN, "pageId": "p1"}
    if op:
        step["op"] = op
    if assertion in ("visible", "checked", "enabled", "editable", "selected"):
        # 负断言从录制端带默认稳定窗口
        if expected is False:
            step["wait"] = {"timeoutMs": 3000, "pollMs": 100, "stableMs": 300}
    step.update(kw)
    return step


def _page_assert(item_id, assertion, expected, *, op=None):
    """页面级断言：同 tab 导航后对当前页面断言 URL/title（pageId 不变）。"""
    step = {"id": item_id, "t": 500, "type": "assert", "scope": "page",
            "assertion": assertion, "expected": expected,
            "expectedConfirmed": True, "url": "/next",
            "pageOrigin": _ORIGIN, "pageId": "p1"}
    if op:
        step["op"] = op
    return step


def _popup_click(item_id, sel, *, url="/", opener_id="p1", page_id="p2"):
    """点击打开新标签页：目标页 pageId=p2、openerPageId=p1。"""
    return {"id": item_id, "t": 600, "type": "click",
            "sel": f'locator("{sel}")', "url": url,
            "pageOrigin": _ORIGIN, "pageId": opener_id,
            "ui": {"templates": {"element": f"{item_id}.png"},
                   "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
                   "pageViewport": {"width": 1280, "height": 720},
                   "deviceScaleFactor": 1}}


def _popup_assert(item_id, assertion, expected, sel=None, *, op=None,
                  url="/popup"):
    step = {"id": item_id, "t": 700, "type": "assert",
            "assertion": assertion, "expected": expected,
            "expectedConfirmed": True, "url": url,
            "pageOrigin": _ORIGIN, "pageId": "p2", "openerPageId": "p1"}
    if sel:
        step["sel"] = f'locator("{sel}")'
    else:
        step["scope"] = "page"      # URL/标题断言比较的是页面自身，无 DOM 选择器
    if op:
        step["op"] = op
    return step


def _new_page(browser):
    origin = _ORIGIN
    context = browser.new_context(base_url=origin)
    pg = context.new_page()
    pg.set_default_timeout(2000)
    pw_expect.set_options(timeout=1500)
    return context, pg


def _generated(browser, steps):
    ctx, pg = _new_page(browser)
    try:
        spec = generate_spec(steps, [], start_url=_ORIGIN + "/", name="accept")
        compile(spec, "accept.py", "exec")
        ns = {"__file__": str(Path(__file__))}
        exec(spec, ns)
        test = next(v for k, v in ns.items() if k.startswith("test_") and callable(v))
        try:
            test(pg)
            return "success"
        except Exception as error:
            return f"failed: {type(error).__name__}"
    finally:
        ctx.close()


def _replay(browser, steps):
    ctx, pg = _new_page(browser)
    try:
        pg.goto(_ORIGIN + "/")
        trace = generate_trace(steps, [], name="accept", start_url=_ORIGIN + "/")
        execution = replay_trace(pg, trace, navigate=False, timeout_ms=2000)
        if execution["status"] == "success":
            return "success"
        step = execution["steps"][0] if execution["steps"] else {}
        return f"failed: {step.get('error') or step.get('status')}"
    finally:
        ctx.close()


def _build_scenarios():
    """在 _ORIGIN 就绪后构造场景（popup/URL 期望用真实本地 origin）。"""
    o = _ORIGIN
    return [
        ("页面跳转-URL断言",
         [_click("c1", "#go", "/"), _page_assert("a1", "url", o + "/next")], True),
        ("页面跳转-URL反例",
         [_click("c1", "#go", "/"), _page_assert("a1", "url", o + "/wrong")], False),
        ("页面跳转-Title断言",
         [_click("c1", "#go", "/"), _page_assert("a1", "title", "结果页")], True),
        ("表单提交-正确文案反例",
         [_click("c1", "#submit", "/"), _assert("a1", "text", "未提交", "#out")], False),
        ("表单提交-正确文本",
         [_click("c1", "#submit", "/"), _assert("a1", "text", "已提交", "#out")], True),
        ("下拉选择-selected反例(默认未选第二项)",
         [_assert("a1", "selected", True, "#city option:nth-child(2)")], False),
        ("下拉选择-selected正例(默认选第一项)",
         [_assert("a1", "selected", True, "#city option:nth-child(1)")], True),
        ("提示出现-visible正例",
         [_click("c1", "#fire", "/"), _assert("a1", "visible", True, "#toast")], True),
        ("提示出现-visible反例",
         [_assert("a1", "visible", True, "#toast")], False),
        ("表格目标行-行文本正例",
         [_assert("a1", "text", "已发货", "table#orders tr:nth-child(1) td.rowval")], True),
        ("表格目标行-行文本反例",
         [_assert("a1", "text", "待支付", "table#orders tr:nth-child(1) td.rowval")], False),
        ("popup-URL断言",
         [_popup_click("c1", "#pop"), _popup_assert("a1", "url", o + "/popup")], True),
        ("popup-URL反例",
         [_popup_click("c1", "#pop"), _popup_assert("a1", "url", o + "/wrong")], False),
        ("popup-标题断言",
         [_popup_click("c1", "#pop"), _popup_assert("a1", "title", "详情页")], True),
        ("popup-元素断言",
         [_popup_click("c1", "#pop"),
          _popup_assert("a1", "text", "详情内容", sel="#pop-state")], True),
    ]


_SCENARIO_COUNT = 15


@pytest.mark.parametrize("scenario_index", list(range(_SCENARIO_COUNT)),
                         ids=[f"s{i}" for i in range(_SCENARIO_COUNT)])
def test_p1_scenario_two_chains_agree(accept_env, scenario_index):
    """每条场景用**独立的新页面**跑生成链 + 回放链 —— 场景间互不污染。

    场景会打开 popup、改变 DOM/导航状态；若所有场景共用同一条页面，后一个场景
    的 trace 会在残留的 popup 或已变化的状态上定位错目标。两条链各自开新页最稳。
    """
    browser, origin = accept_env
    label, steps, should_pass = _build_scenarios()[scenario_index]
    gen = _generated(browser, steps)
    rep = _replay(browser, steps)
    if should_pass:
        assert gen == "success", f"{label}: 生成链失败 {gen}"
        assert rep == "success", f"{label}: 回放链失败 {rep}"
    else:
        assert gen != "success", f"{label}: 生成链应失败却通过"
        assert rep != "success", f"{label}: 回放链应失败却通过"
    assert (gen == "success") == (rep == "success"), (
        f"{label}: 两链同判被破坏 gen={gen} rep={rep}")
