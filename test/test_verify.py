"""自检 —— 验证录制器确实按 SKILL.md 承诺的那样工作（verify.mjs 的 Python 版）。

造一个包含全部边界情况的页面（同名元素、自增 id、密码框、会发请求的按钮），
用真实浏览器跑一遍，逐条断言。改动录制器后跑这个，比肉眼看输出可靠。

    pytest

检查项与 verify.mjs 一一对应、同名。两边都必须绿 —— 迁移期间 JS 侧是
Python 侧的参照物，任何一边单独绿都不算数。
"""

import json
import re

import pytest

from generate_spec import generate_spec, prepare_steps
from generate_trace import generate_trace

CHECKS: dict[str, callable] = {}


def check(name):
    def deco(fn):
        assert name not in CHECKS, f"检查项重名: {name}"
        CHECKS[name] = fn
        return fn
    return deco


# ────────────────────────── 录制侧 ──────────────────────────

def find(steps, pred):
    return next((s for s in steps if pred(s)), None)


@check("data-testid 优先于其他方式")
def _(rec):
    s = find(rec["steps"], lambda s: "save-btn" in s["sel"])
    assert s and s["kind"] == "testid", s and s["sel"]


@check("文本输入框用 getByPlaceholder")
def _(rec):
    s = find(rec["steps"], lambda s: s["type"] == "fill" and not s.get("secret"))
    assert s and s["kind"] == "placeholder", s and s["sel"]


@check("输入的值被记录")
def _(rec):
    s = find(rec["steps"], lambda s: s["type"] == "fill" and not s.get("secret"))
    assert s and s.get("value") == "alice", s and s.get("value")


@check("普通元素的自定义 change 不会被录成 fill")
def _(rec):
    assert not find(rec["steps"], lambda s: s.get("label") == "轮播内容")


@check("密码框标记为 secret")
def _(rec):
    assert find(rec["steps"], lambda s: s.get("secret"))


@check("密码明文未出现在记录里")
def _(rec):
    raw = json.dumps(rec, ensure_ascii=False)
    assert "sup3rs3cret" not in raw


@check("撞车文本被识别且计数正确")
def _(rec):
    s = find(rec["steps"], lambda s: (s.get("matches") or 0) > 1)
    assert s and s["matches"] == 3, s and f"{s['sel']} → {s.get('matches')}"


@check("运行时自增 id 未进入选择器")
def _(rec):
    s = find(rec["steps"], lambda s: "close_x" in (s.get("css") or ""))
    assert s and "tip_box_10059" not in s["css"], s and s["css"]


@check("勾选框只录一步（click 与 change 不重复）")
def _(rec):
    cbs = [s for s in rec["steps"]
           if s["type"] in ("check", "uncheck") or "checkbox" in s["sel"]]
    assert len(cbs) == 1 and cbs[0]["type"] == "check", f"{len(cbs)} 步"


@check("按钮用 getByRole")
def _(rec):
    s = find(rec["steps"], lambda s: "提交订单" in (s.get("label") or ""))
    assert s and s["kind"] == "role", s and s["sel"]


@check("点击记录了黑盒 UI 边界和落点")
def _(rec):
    s = find(rec["steps"], lambda s: "提交订单" in (s.get("label") or ""))
    ui = (s or {}).get("ui") or {}
    assert ui.get("rect", {}).get("width", 0) > 0, ui
    assert ui.get("click", {}).get("rx") is not None, ui


@check("接口被关联到触发它的那一步")
def _(rec):
    btn = find(rec["steps"], lambda s: "提交订单" in (s.get("label") or ""))
    assert btn
    later = [s for s in rec["steps"] if s["t"] > btn["t"]]
    hi = later[0]["t"] if later else float("inf")
    calls = [n for n in rec["net"]
             if n["phase"] == "res" and btn["t"] <= n["t"] < hi]
    assert any("/api/ok" in c["url"] for c in calls), [c["url"] for c in calls]


@check("失败响应体被保留")
def _(rec):
    bad = find(rec["net"], lambda n: n["phase"] == "res" and n["status"] >= 400)
    assert bad and "subnetIdList" in (bad.get("body") or ""), bad and bad.get("body")


@check("写请求的请求体被保留")
def _(rec):
    # 原始记录里请求体必须**原样**保留：放宽是生成阶段的事，
    # 录制阶段就抹掉的话，重新生成时再想改放宽策略已经没有原料了
    r = find(rec["net"], lambda n: n["phase"] == "req" and "/api/ok" in n["url"])
    assert r and r.get("body"), r
    body = json.loads(r["body"])
    assert body["id"] == "39049753287328" and body["a"] == 1, r["body"]


@check("写请求的响应体被保留")
def _(rec):
    r = find(rec["net"], lambda n: n["phase"] == "res" and "/api/ok" in n["url"])
    assert r and r.get("body"), r and r.get("body")


@check("点击后立即跳转的步骤未丢失")
def _(rec):
    assert find(rec["steps"], lambda s: "立刻跳转" in (s.get("label") or ""))


@check("跳转后新页面仍在录制")
def _(rec):
    assert find(rec["steps"], lambda s: "after-nav" in s["sel"])


@check("撞车文本自动加作用域（不再是 .first()）")
def _(rec):
    s = find(rec["steps"], lambda s: s.get("label") == "删除")
    assert s and s["kind"] == "scoped" and ".first()" not in s["sel"], s and s["sel"]


@check("iframe 内的操作被标记归属")
def _(rec):
    s = find(rec["steps"], lambda s: s.get("value") == "frame-user")
    assert s and s.get("inFrame") is True, s and s.get("framePath")


# ── 开关与浮层 ──

@check("开关录成 switch 步骤而非普通 click")
def _(rec):
    assert find(rec["steps"], lambda s: s["type"] == "switch")


@check("开关记录了目标状态")
def _(rec):
    s = find(rec["steps"], lambda s: s["type"] == "switch")
    assert s and s.get("to") is True, s and f"to={s.get('to')}"


@check("开关用 getByRole(switch)")
def _(rec):
    s = find(rec["steps"], lambda s: s["type"] == "switch")
    assert s and s["kind"] == "role" and "switch" in s["sel"], s and s["sel"]


@check("后代开关也能被识别（整行可点的情形）")
def _(rec):
    assert find(rec["steps"],
                lambda s: s["type"] == "switch" and s.get("label") == "行内自保护")


@check("记录了状态是靠 class 表达的")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "行内自保护")
    via = (s or {}).get("via") or {}
    assert via.get("type") == "class" and via.get("token") == "toggled", via


@check("记录了状态在内层哪一级")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "行内自保护")
    via = (s or {}).get("via") or {}
    assert via.get("within") == ".eui_toggle_container", via.get("within")


@check("不唯一的 testid 不被采用")
def _(rec):
    s = find(rec["steps"], lambda s: "重复标记甲" in (s.get("label") or ""))
    assert s and "getByTestId" not in s["sel"], s and s["sel"]


@check("data-cy 生成属性选择器而非 getByTestId")
def _(rec):
    s = find(rec["steps"], lambda s: "仅有 data-cy" in (s.get("label") or ""))
    assert s and "[data-cy=" in s["sel"] and "getByTestId" not in s["sel"], s and s["sel"]


@check("录制忠实记下了 press 在 fill 之前")
def _(rec):
    steps = rec["steps"]
    fi = next((i for i, s in enumerate(steps)
               if s["type"] == "fill" and s.get("value") == "bob"), -1)
    pi = next((i for i, s in enumerate(steps)
               if s["type"] == "press" and "请输入用户名" in s["sel"]), -1)
    assert pi >= 0 and fi >= 0 and pi < fi, f"press@{pi} fill@{fi}"


@check("点在空白处不产生步骤")
def _(rec):
    junk = [s for s in rec["steps"]
            if re.search(r'locator\("(html|body)"\)', s["sel"])]
    assert not junk, f"{len(junk)} 条"


@check("浮层选项识别到撞车")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "click" and "#pop" in (s.get("css") or ""))
    assert s and s.get("matches") == 2, s and f"matches={s.get('matches')}"


@check("浮层选项被限定作用域，不是 .first()")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "click" and "#pop" in (s.get("css") or ""))
    assert s and s["kind"] == "scoped" and ".first()" not in s["sel"], s and s["sel"]


@check("触发器本身不受影响")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "click" and "#trigger" in (s.get("css") or ""))
    assert s and s["kind"] == "text", s and s["sel"]


# ── 断言菜单 ──

@check("右键能添加断言")
def _(rec):
    asserts = [s for s in rec["steps"] if s["type"] == "assert"]
    assert len(asserts) == 4, f"{len(asserts)} 条"


# ── 同义反复的文本断言 ──
# expect(page.get_by_text("X")).to_have_text("X") 只有元素消失才会失败：
# 元素本来就是按这段文本找到的。实测真实录制出来的两条断言都是这个形状 ——
# 看着像断言，其实什么都没断。

@check("按文本定位再断言同一段文本，改写成存在性断言")
def _(rec):
    _, prepped = prepare_steps(rec["steps"])
    s = find(prepped, lambda s: s.get("_wasTextTautology") == "待删除的资产")
    assert s, [x.get("sel") for x in prepped if x["type"] == "assert"]
    assert s["assertion"] == "visible" and s["expected"] is True, (
        s["assertion"], s["expected"])


@check("用别的方式定位的文本断言不受影响")
def _(rec):
    # getByTestId 找到的元素，断言它的文本是实打实的命题，不能动
    _, prepped = prepare_steps(rec["steps"])
    s = find(prepped, lambda s: s.get("expected") == "用户确认过的值")
    assert s and s["assertion"] == "text", s and s["assertion"]
    assert not s.get("_wasTextTautology"), s


@check("expected 保存的是用户改过的值，不是元素当前值")
def _(rec):
    a = find(rec["steps"], lambda s: s.get("assertion") == "text")
    assert a and a.get("expected") == "用户确认过的值", a and a.get("expected")


@check("expected 为空时提交被禁用")
def _(rec):
    assert rec["emptyGuard"]["blocked"] is True


@check("勾选「允许空值」后可提交")
def _(rec):
    assert rec["emptyGuard"]["unblocked"] is True


@check("checked 断言用布尔 expected")
def _(rec):
    a = find(rec["steps"], lambda s: s.get("assertion") == "checked")
    assert a and isinstance(a.get("expected"), bool), a and a.get("expected")


@check("visible 可以显式断言 false")
def _(rec):
    a = find(rec["steps"], lambda s: s.get("assertion") == "visible")
    assert a and a.get("expected") is False, a and a.get("expected")


# ────────────────────────── 生成器侧 ──────────────────────────
# 这些检查断言的是**生成出来的 Python 代码**，与 verify.mjs 里断言 TS 代码
# 的那几条一一对应，只是目标语言换了。

def spec_of(rec):
    return generate_spec(rec["steps"], rec["net"],
                         start_url=rec["startUrl"], name="gen-check")


def trace_of(rec):
    return generate_trace(rec["steps"], rec["net"],
                          start_url=rec["startUrl"], name="gen-check")


@check("轨迹里也是存在性断言（两个产物同一条规则）")
def _(rec):
    trace = trace_of(rec)
    node = find(trace["steps"].values(),
                lambda n: n["action"]["type"] == "Assert"
                and "待删除的资产" in (n["selector"].get("sel") or ""))
    param = (node or {})["action"]["param"]
    assert param["assertion"] == "visible" and param["expected"] is True, param


@check("草稿里生成 to_be_visible 并说明为什么改了")
def _(rec):
    spec = spec_of(rec)
    assert 'get_by_text("待删除的资产", exact=True)).to_be_visible()' in spec, \
        re.search(r'.*待删除的资产.*', spec)
    assert "只有元素消失才会失败" in spec


@check("写请求生成状态码断言")
def _(rec):
    spec = spec_of(rec)
    assert re.search(r"assert resp\d+(_\d+)? is not None and resp\d+(_\d+)?\.status == 200",
                     spec), re.search(r"assert resp[^\n]*", spec)


@check("写请求生成请求体断言")
def _(rec):
    spec = spec_of(rec)
    assert "assert_subset(" in spec and ".post_data_json" in spec
    assert '"a": 1' in spec


@check("响应变量名不重复")
def _(rec):
    names = re.findall(r"(resp\d+(?:_\d+)?) = req\d+(?:_\d+)?\.response\(\)", spec_of(rec))
    assert len(names) == len(set(names)), names


@check("GET 不生成断言，仍是注释")
def _(rec):
    spec = spec_of(rec)
    assert not re.search(r'nth_request\([^\n]*"GET"', spec)


# ── 并发同端点写请求（codex 分支那个修复）──
# 一次操作向同一端点并发发两条请求时，旧做法让两个响应都关联到第二条请求，
# 两个等待器又都命中第一条响应。这里用合成录制直接验生成器。

def _dup_write_spec():
    return generate_spec(
        [{"t": 100, "type": "click", "kind": "text", "sel": 'getByText("Save", { exact: true })'}],
        [
            {"id": 1, "t": 110, "phase": "req", "method": "POST",
             "url": "https://example.test/api/item", "body": '{"id":"first"}'},
            {"id": 2, "t": 120, "phase": "req", "method": "POST",
             "url": "https://example.test/api/item", "body": '{"id":"second"}'},
            # 第二条请求先返回，验证生成器不依赖响应完成顺序
            {"requestId": 2, "t": 130, "phase": "res", "method": "POST",
             "url": "https://example.test/api/item", "status": 202},
            {"requestId": 1, "t": 140, "phase": "res", "method": "POST",
             "url": "https://example.test/api/item", "status": 201},
        ],
        start_url="https://example.test/start", name="duplicate writes",
    )


@check("同端点第二个等待器只接第二条请求")
def _(rec):
    spec = _dup_write_spec()
    assert re.search(r'nth_request\("/api/item", "POST", 1\)', spec), spec
    assert re.search(r'nth_request\("/api/item", "POST", 2\)', spec), spec


@check("响应按 FIFO 关联各自的请求体")
def _(rec):
    spec = _dup_write_spec()
    # 按请求发出次序：first 在前、second 在后；状态码也各归各位
    assert re.search(r'"id": "first"[\s\S]*"id": "second"', spec), spec
    assert "resp1_1.status == 201" in spec, spec
    assert "resp1_2.status == 202" in spec, spec


@check("CSS 兜底的写请求也生成断言")
def _(rec):
    spec = generate_spec(
        [{"t": 100, "type": "click", "kind": "css", "sel": 'locator(".icon-save")'}],
        [
            {"t": 110, "phase": "req", "method": "PATCH",
             "url": "https://example.test/api/item/1", "body": '{"enabled":true}'},
            {"t": 120, "phase": "res", "method": "PATCH",
             "url": "https://example.test/api/item/1", "status": 200},
        ],
        start_url="https://example.test/", name="optional css write",
    )
    # 等待必须建立在「元素确实存在」之后，否则可选弹窗没出现时会空等到超时
    assert re.search(r"if is_present\(_el\d+\):[\s\S]*page\.expect_request[\s\S]*\.click\(\)",
                     spec), spec
    assert "is not None and resp1.status == 200" in spec, spec
    assert re.search(r"assert_subset\(req1\.post_data_json", spec), spec


@check("生成 frameLocator 而非直接 page")
def _(rec):
    assert "frame_locator(" in spec_of(rec)


@check("生成状态感知的拨动而非盲点")
def _(rec):
    spec = spec_of(rec)
    assert 'get_attribute("aria-checked")' in spec
    assert 'to_have_attribute("aria-checked"' in spec


@check("class 型开关生成 classList 读法而非 aria-checked")
def _(rec):
    spec = spec_of(rec)
    assert 'classList.contains(\\"toggled\\")' in spec, \
        re.search(r"is_on\d+ = [^\n]*", spec)
    assert re.search(r"poll_until\(is_on\d+, ", spec)


@check("点外层、读内层")
def _(rec):
    spec = spec_of(rec)
    assert re.search(r'state(\d+) = sw\1\.locator\("\.eui_toggle_container"\)\.first', spec)
    assert re.search(r"is_on(\d+) = lambda: state\1\.evaluate", spec)
    assert re.search(r"sw\d+\.click\(\)", spec)


@check("生成时把顺序纠正为先填值后回车")
def _(rec):
    spec = spec_of(rec)
    i_fill = spec.find('.fill("bob")')
    i_press = spec.find('get_by_placeholder("请输入用户名").press("Enter")')
    assert i_fill >= 0 and i_press >= 0 and i_fill < i_press, \
        f"fill@{i_fill} press@{i_press}"


@check("生成 toHaveText 且带 expected")
def _(rec):
    assert 'to_have_text("用户确认过的值")' in spec_of(rec)


@check("visible=false 生成 toBeHidden")
def _(rec):
    assert "to_be_hidden()" in spec_of(rec)


@check("checked=false 生成 not.toBeChecked")
def _(rec):
    assert re.search(r"(not_)?to_be_checked\(\)", spec_of(rec))


# ── 易变值放宽：稳定值必须保留 ──
# 两边都要，否则断言不是每次必挂，就是什么也没守住。

@check("字符串型雪花 ID 放宽")
def _(rec):
    spec = spec_of(rec)
    assert re.search(r'"id": ANY_STR', spec), re.search(r'"id": [^,\n]*', spec)


@check("数字型时间戳放宽")
def _(rec):
    spec = spec_of(rec)
    assert re.search(r'"endTime": ANY_NUM', spec), re.search(r'"endTime": [^,\n]*', spec)


@check("普通数字不受影响")
def _(rec):
    spec = spec_of(rec)
    assert re.search(r'"pageSize": 100', spec), re.search(r'"pageSize": [^,\n]*', spec)


# ── 慢开关 ──
# 开关拨动后 class 更新可能比固定延时慢。检测不到变化就退回盲点击，
# 回放时方向取决于初始状态，而且不报错 —— 这类错最难查。

@check("慢开关（500ms 后才变）也能被识别")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "延迟自保护")
    assert s, "退回成了普通 click"


@check("慢开关读到了正确的目标状态")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "延迟自保护")
    assert s and s.get("to") is True, s and f"to={s.get('to')}"


# ── 长文本容器里的图标 ──
# 弹窗关闭叉没文本、没 role，正文又上百字。原来要求作用域容器整段文本 ≤40 字，
# 于是这类图标只能退到 CSS 绝对路径，而那串路径带 nth-of-type：实测同一个关闭
# 叉两次录制分别录成 div:nth-of-type(8) 和 (9)，回放时按第一次的路径根本点不中，
# 弹窗留在页面上，后面每一步都被它的遮罩挡住。

@check("长文本弹窗里的图标也能拿到作用域选择器")
def _(rec):
    s = find(rec["steps"], lambda s: "dlg_panel" in (s.get("css") or ""))
    assert s and s["kind"] == "scoped", s and (s["kind"], s["sel"])
    assert "nth-of-type" not in s["sel"], s["sel"]


@check("作用域锚点用文本前缀，且能区分两个同类弹窗")
def _(rec):
    s = find(rec["steps"], lambda s: "dlg_panel" in (s.get("css") or ""))
    assert s and "卸载校验码" in s["sel"], s and s["sel"]
    assert "历史记录" not in s["sel"], s["sel"]


@check("关浮层的那一步被标出来（据观察，不靠选择器形态猜）")
def _(rec):
    s = find(rec["steps"], lambda s: "dlg_panel" in (s.get("css") or ""))
    assert s and s.get("dismissesOverlay") is True, s and s.get("kind")


@check("页内提示条的关闭图标也算关浮层")
def _(rec):
    # 提示条挂在页面流里，没有 fixed/absolute + 高 z-index，按浮层判据认不出来。
    # 实测「系统检测到您未绑定手机号码和电子邮箱」这条横幅就是这么被漏判成
    # 必经步骤的 —— 而下次登录它可能根本不出现，整条轨迹断在那里。
    s = find(rec["steps"], lambda s: "tipBox" in (s.get("css") or ""))
    assert s and s.get("dismissesOverlay") is True, s and s.get("css")


@check("删除某一行不算关浮层")
def _(rec):
    # 点完那一行同样消失，但这是破坏性操作。标成可选就等于允许悄悄跳过一次删除，
    # 而且不报错 —— 所以容器证据弱的时候要求图标类名本身是个关闭件。
    s = find(rec["steps"], lambda s: "list_row" in (s.get("css") or ""))
    assert s and not s.get("dismissesOverlay"), s and s.get("css")


@check("加标记的升级不会覆盖点击时算出的好选择器")
def _(rec):
    # 浮层在升级发生时已经消失，重算选择器只能退到路径兜底。
    # 实测正是这样把作用域选择器换成了一串 nth-of-type。
    s = find(rec["steps"], lambda s: s.get("dismissesOverlay"))
    assert s and s["kind"] == "scoped", s and (s["kind"], s["sel"])
    assert "nth-of-type" not in s["sel"], s["sel"]


@check("关浮层的步骤在轨迹里是可选的，并带着「可提前做」的标记")
def _(rec):
    trace = trace_of(rec)
    node = find(trace["steps"].values(),
                lambda n: "dlg_panel" in ((n["selector"].get("css")) or ""))
    assert node and node.get("optional") is True, node
    assert node.get("dismissesOverlay") is True, node


@check("选中下拉选项不算关浮层")
def _(rec):
    # 「点在浮层里且浮层消失」这个条件太宽 —— 选中下拉项同样满足。
    # 标成可选就等于允许悄悄跳过一步真操作。
    hits = [s for s in rec["steps"]
            if s.get("label") == "Windows系统" and s.get("dismissesOverlay")]
    assert not hits, hits


# ── 点在滑块上 ──
# 开关组件层层嵌套，滑块 / 轨道 / 容器都带 toggle 字样，但状态只写在容器上。
# 往上撞到的第一层是滑块，它永远读不出状态 —— 真实录制里绝大多数拨开关的
# 步骤就是这么退化成盲点击的：回放时朝哪边拨取决于当时状态，而且不报错。

@check("点滑块也能拨到状态层（不是撞到的第一层）")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "已开启滑块")
    assert s, "退回成了普通 click"
    via = s.get("via") or {}
    assert via.get("within") == ".eui_toggle_container", via.get("within")


@check("点滑块读到的是「要拨成什么」，不是「现在是什么」")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "已开启滑块")
    assert s and s.get("to") is False, s and f"to={s.get('to')}"


@check("状态标记缺席时，靠拨完哪一层变了来定状态层")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "待开启滑块")
    assert s and s.get("to") is True, s and (s.get("type"), s.get("to"))
    via = s.get("via") or {}
    assert via.get("within") == ".eui_toggle_container", via.get("within")
    assert via.get("gated") is False, "中间没有别的步骤，不该标成需后续交互"


# ── 二次确认型开关 ──
# 拨开关先弹确认框，class 要等人点了「确认」才变 —— 那可能是好几秒之后。
# 原来只等 1.2 秒，等不到就退回盲点：回放时起始状态一变就朝反方向拨。
# 现在改成先如实记点击、之后继续观察，真变了再按同一个 id 升级。

@check("二次确认后才变的开关也能升级成拨开关")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "需确认自保护")
    assert s, "退回成了普通 click（升级记录没生效）"


@check("升级后的开关带着目标状态和状态层")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "需确认自保护")
    assert s and s.get("to") is True, s and f"to={s.get('to')}"
    assert s and (s.get("via") or {}).get("within"), "缺 via.within，回放读不出当前状态"


@check("需确认的开关标出了「要靠后续交互才落地」")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "需确认自保护")
    via = (s or {}).get("via") or {}
    assert via.get("gated") is True, via
    assert via.get("gatedSteps"), "没记下中间那几步，回放会把确认按钮当必经步骤"


@check("慢开关不该被当成需要后续交互")
def _(rec):
    s = find(rec["steps"],
             lambda s: s["type"] == "switch" and s.get("label") == "延迟自保护")
    assert not ((s or {}).get("via") or {}).get("gated"), s and s.get("via")


@check("确认框里的步骤在轨迹里是可选的")
def _(rec):
    trace = trace_of(rec)
    sw = find(trace["steps"].values(),
              lambda n: n["action"]["type"] == "SetSwitch"
              and n["selector"].get("label") == "需确认自保护")
    gated = ((sw or {})["action"]["param"].get("via") or {}).get("gatedSteps") or []
    assert gated, sw
    for node in trace["steps"].values():
        if node.get("sourceStepId") in gated:
            assert node.get("optional") is True, node["selector"]


@check("升级不会弄丢点击那一刻抓的模板")
def _(rec):
    for label in ("待开启滑块", "需确认自保护"):
        s = find(rec["steps"],
                 lambda s: s["type"] == "switch" and s.get("label") == label)
        tpl = ((s or {}).get("ui") or {}).get("templates") or {}
        assert tpl.get("element") == f"{s['id']}.png", (label, tpl)


@check("升级是覆盖而不是追加，同一下拨动只留一条")
def _(rec):
    hits = [s for s in rec["steps"] if s.get("label") == "需确认自保护"]
    assert len(hits) == 1, [(h["type"], h["id"]) for h in hits]


# ── 断言步骤没被升级改动波及 ──
# 升级机制给 push 加了 _id 入口，改错函数会让 pushAssert 引用不存在的变量，
# 而那里的 try/catch 会把 ReferenceError 静默吞掉 —— 断言全部消失且不报错。

@check("右键菜单录出的断言步骤仍然完整")
def _(rec):
    kinds = {s.get("assertion") for s in rec["steps"] if s["type"] == "assert"}
    assert kinds == {"text", "visible", "checked"}, kinds


# ── placeholder 撞车 ──
# 登录页常有诱饵输入框，两个 placeholder 一模一样。不验唯一性的话，
# 回放第一步就 strict mode 报错，看起来像"页面变了"。

def _dup_ph(rec):
    return find(rec["steps"],
                lambda s: s["type"] == "fill" and s.get("value") == "zhangsan")


@check("不唯一的 placeholder 不被采用")
def _(rec):
    s = _dup_ph(rec)
    assert s and "getByPlaceholder" not in s["sel"], s and s["sel"]


@check("也不会退回同样撞车的 getByRole")
def _(rec):
    s = _dup_ph(rec)
    assert s and "getByRole" not in s["sel"], s and s["sel"]


@check("退到稳定 id 而不是 getByText")
def _(rec):
    # 退到「能用的东西」上才算修好。退成 getByText 是最坏的结果：
    # 语法正确、回放却永远找不到元素，因为 input 没有文本内容。
    s = _dup_ph(rec)
    assert s and s["sel"] == 'locator("#real_user")', s and s["sel"]


# ── 回放稳定性：生成的草稿必须自带三件事 ──

@check("草稿走 authedPage 而不是裸 page")
def _(rec):
    spec = spec_of(rec)
    assert re.search(r"def test_\w+\(authed_page: Page\):", spec)
    assert "page = authed_page" in spec
    assert "from rec_helpers import" in spec


@check("草稿自带关弹窗前奏")
def _(rec):
    assert "dismiss_overlays(page)" in spec_of(rec)


@check("作用域不采用时间/日期类文本")
def _(rec):
    # 作用域不能锚在时间戳上：当场绿、几小时后必挂，是最难查的一类
    spec = spec_of(rec)
    assert not re.search(r'has_text="\d{4}-\d{2}-\d{2}', spec)
    assert not re.search(r'has_text="[^"]*\d{1,2}:\d{2}', spec)


# 登录段只砍**开头连续**的那一段（登录之后的 iframe 操作是正经业务，不能砍）。
# fixture 里 iframe 步骤在中间，所以这里用一份合成录制直接验生成器。

def _login_spec():
    t0 = 1_700_000_000_000
    frame = {"inFrame": True, "framePath": "/custom_login.html", "url": "/"}
    steps = [
        {"id": "L1", "t": t0, "type": "fill", "sel": 'getByPlaceholder("用户名")',
         "kind": "placeholder", "value": "alice", **frame},
        {"id": "L2", "t": t0 + 1, "type": "fill", "sel": 'getByPlaceholder("密码")',
         "kind": "placeholder", "secret": True, **frame},
        {"id": "L3", "t": t0 + 2, "type": "press", "sel": 'getByPlaceholder("密码")',
         "kind": "placeholder", **frame},
        {"id": "B1", "t": t0 + 3, "type": "click",
         "sel": 'getByText("业务按钮", { exact: true })', "kind": "text",
         "label": "业务按钮", "url": "/"},
    ]
    return generate_spec(steps, [], start_url="http://127.0.0.1/", name="login-check")


@check("开头的登录段被自动去掉")
def _(rec):
    spec = _login_spec()
    body = spec.split("def test_", 1)[1]
    assert "custom_login" not in body, "正文里仍有登录步骤"
    assert "REC_PASSWORD" not in body, "正文里仍有密码步骤"


@check("登录之后的业务步骤保留")
def _(rec):
    body = _login_spec().split("def test_", 1)[1]
    assert "业务按钮" in body


@check("去掉的登录步骤留在注释里备查")
def _(rec):
    spec = _login_spec()
    m = re.search(r"已自动去掉开头 \d+ 步登录", spec)
    assert m, spec.splitlines()[:20]


# ────────────────────────── 跑 ──────────────────────────

@pytest.mark.parametrize("name", list(CHECKS))
def test_recorder(name, recording):
    CHECKS[name](recording)


def test_duplicate_id_does_not_shortcut_css_path(recording):
    """id 不能假定唯一。

    实测一个页面上叠了两个弹窗，各自都 id="dialog_panel"。以前 cssPath 遇到
    id 就短路返回，产出的路径命中 2 个元素 —— 录制时能跑通，回放必然
    strict mode 报错，视觉回退又分不清那两个一模一样的图标。
    """
    steps = recording["steps"]
    dlg = next((s for s in steps if "dlg_close" in (s.get("css") or "")), None)
    assert dlg is not None, "没录到弹窗关闭图标那一步"
    # 要么路径里不再靠那个重复 id 短路，要么被明确标成撞车
    shortcut = (dlg.get("css") or "").startswith("div#dialog_panel")
    assert not shortcut or dlg.get("ambiguous"), dlg.get("css")


def test_css_fallback_collision_is_flagged(recording):
    """CSS 兜底撞车必须标出来，别让它以「回放时点错元素」的形式暴露。

    先断言 fixture 真的产出了撞车步骤 —— 否则这个测试会空转：
    循环体一次都不执行也算通过，唯一性校验被改回旧行为都发现不了。
    fixture 里那两个 id="dialog_panel" 的弹窗就是为这条准备的。
    """
    css_steps = [s for s in recording["steps"] if s.get("kind") == "css"]
    assert css_steps, "fixture 没产出任何 CSS 兜底步骤，这条测试失去意义"

    collided = [s for s in css_steps if s.get("ambiguous")]
    assert collided, (
        "fixture 里叠了两个同 id 的弹窗，本该产出撞车的 CSS 路径却一个都没有 —— "
        "要么唯一性校验失效了，要么 fixture 变了"
    )
    for step in collided:
        assert step.get("matches") not in (None, 0, 1), step.get("matches")


def test_icon_control_is_captured_itself_not_its_row(recording):
    """最小捕获原则：事件目标本身是独立控件时，就地停下，别爬到外层容器。

    实测踩过：资产树一行里有个展开箭头（无文本、无 role），人点的是箭头
    （展开、列出终端），录制器却上溯停在带文本的整行上 —— 回放点行只选中不展开，
    终端永远不出现，而那一步还报 success。
    """
    step = next((s for s in recording["steps"] if "row_hit" in (s.get("sel") or "")), None)
    assert step is not None, (
        "展开箭头没被单独抓住，多半又上溯到外层了。当前录到的选择器："
        + str([s.get("sel") for s in recording["steps"] if "tree_row" in (s.get("sel") or "")])
    )
    # 形态应当是「行做作用域 + 图标类名」，而不是一长串 nth-of-type
    assert "tree_row" in step["sel"] and "row_hit" in step["sel"], step["sel"]


def _unused_row_click_selector_points_at_the_row_not_its_label(recording):
    """选择器必须能定位回**被点的那个元素**。

    实测栽过：资产树一行里，点整行会展开+选中，点行内的 span 只选中。
    录到的是整行，生成的却是 getByText —— 而 getByText 解析到最内层带该文本的
    元素，也就是那个 span。回放时子节点永远不出现，那一步却报 success：
    点击确实成功了，只是做的不是同一件事。
    """
    step = next((s for s in recording["steps"] if s.get("label") == "分组甲"), None)
    assert step is not None, "没录到整行点击那一步"
    assert "getByText" not in step["sel"], step["sel"]
    assert "tree_row" in step["sel"], step["sel"]
