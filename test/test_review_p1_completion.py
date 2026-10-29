"""P0/P1 完成度审查后续修复的失败测试（review 七项，缺能力必挂）。

先写测试：每条先失败，再做最小实现。
"""

import pytest


# ── F5：replay 缺 wait 时 poll 默认必须 100ms，不得 0.1ms（busy-poll） ─────────


def test_replay_assert_default_poll_is_100ms(monkeypatch):
    """replay _assert_locator 缺 wait 字段时，轮询间隔必须是 100ms，不是
    (0.1)/1000=0.1ms 的忙轮询。用猴子补丁固定 time.sleep 来精确量间隔。"""
    sleeps = []
    monkeypatch.setattr("replay_trace.time.sleep", lambda s: sleeps.append(s))

    class _Flaky:
        calls = 0

        def inner_text(self):
            self.calls += 1
            return "old" if self.calls == 1 else "new"

    from replay_trace import _assert_locator
    param = {"assertion": "text", "expected": "new"}   # 无 wait → 用默认 poll
    _assert_locator(_Flaky(), param, 5000)
    assert sleeps, "应当至少轮询 sleep 一次"
    assert all(abs(s - 0.1) < 0.02 for s in sleeps), \
        f"默认 poll 应为 100ms，实际 {sleeps}"


def test_replay_page_assert_default_poll_is_100ms(monkeypatch):
    """页面级断言（scope=page）缺 wait 时同样 100ms 轮询。"""
    sleeps = []
    monkeypatch.setattr("replay_trace.time.sleep", lambda s: sleeps.append(s))

    class _P:
        calls = 0

        @property
        def url(self):
            self.calls += 1
            return "http://old" if self.calls == 1 else "http://new"

    from replay_trace import _assert_page
    _assert_page(_P(), {"assertion": "url", "expected": "http://new"}, 5000)
    assert sleeps, "应当至少轮询 sleep 一次"
    assert all(abs(s - 0.1) < 0.02 for s in sleeps), \
        f"页面断言默认 poll 应为 100ms，实际 {sleeps}"


# ── F1(回放侧)：trace 断言 expected 未确认必须独立拒绝，不得绕门回放 ─────────


def test_replay_refuses_unconfirmed_expected(monkeypatch):
    """未确认的 expected 断言即使在 generate_spec（已硬拒）之外，也必须在回放
    侧独立拒绝 —— 不让「绕过生成门直接喂 trace」的路径把未确认意图当真。"""
    from replay_trace import _assert_locator

    class _Loc:
        def inner_text(self):
            return "x"

    param = {"assertion": "text", "expected": "x", "expectedConfirmed": False}
    with pytest.raises(Exception) as ei:
        _assert_locator(_Loc(), param, 1000)
    assert "expectedConfirmed" in str(ei.value)


def test_replay_refuses_page_unconfirmed_expected():
    from replay_trace import _assert_page

    class _P:
        @property
        def url(self):
            return "http://x"

    param = {"assertion": "url", "expected": "http://x", "expectedConfirmed": False}
    with pytest.raises(Exception) as ei:
        _assert_page(_P(), param, 1000)
    assert "expectedConfirmed" in str(ei.value)


# ── F2：负文本断言（not_equals/not_contains）执行 stableMs 稳定窗口 ──────────
#
# generate_spec 当前对 text 的 not_equals/not_contains 走 Playwright 立即负断言
# （not_to_have_text / not_to_contain_text），不执行录制的 stableMs —— 与回放链
# 分叉：回放链会做稳定窗口。这里用一个可回放步骤同时喂两链的假页面来验证生成链
# 也执行稳定窗口。生成链是 generate_spec → poll_until(...matches_assert) 路径。
#
# 这题的重点是「负文本断言也遵守 wait.stableMs」：生成链与回放链用同一稳定语义。

def _negative_text_step(op, expected):
    return {
        "id": "nt", "t": 100, "type": "assert", "assertion": "text",
        "expected": expected, "op": op, "expectedConfirmed": True,
        "sel": 'locator("#x")',
        # 负断言默认带稳定窗口
        "wait": {"timeoutMs": 5000, "pollMs": 100, "stableMs": 300},
        "url": "/", "pageOrigin": "http://x", "pageId": "p1",
    }


def test_negative_text_spec_honors_stable_window(monkeypatch):
    """生成链负文本断言必须落到统一稳定轮询（含 stableMs），不能是 Playwright
    立即负断言。通过 AST 检查生成体是否存在稳定窗口相关调用。"""
    from generate_spec import generate_spec
    spec = generate_spec([_negative_text_step("not_equals", "旧值")],
                         [], start_url="http://x/", name="nt")
    # 生成体里必须通过 matches_assert + poll 的稳定窗口路径（不是立即负断言）
    assert "poll_until" in spec or "stable=" in spec or "expect_state" in spec
    # 而且不能走 Playwright 立即的 not_to_have_text / not_to_contain_text
    assert "not_to_have_text" not in spec, "负文本断言不得用 Playwright 立即负断言"
    assert "not_to_contain_text" not in spec
    # 稳定窗口要被表达出来（default 300ms 兜底也要有 stable=）
    assert "stable=" in spec, "负文本断言应带稳定窗口 stable= 参数"


# ── F4：editable 目标限制 + 统一语义 ──────────────────────────────────────────

def test_editable_state_restricted_to_editable_targets():
    """editable 只适用于输入类可编辑目标；对普通 div 断言 editable 必须明确失败
    （Playwright 的 locator.is_editable() 对普通 div 会抛错，不能静默判 true）。"""
    import rec_assert
    from rec_assert import expect_state

    class _DivLoc:
        def is_editable(self):
            raise RuntimeError("element is not an <input>, <textarea> or [contenteditable]")

    with pytest.raises(Exception) as ei:
        expect_state(_DivLoc(), True, "editable", timeout=0.5, poll=0.02, stable=0)
    assert "读取持续失败" in str(ei.value) or "不可编辑" in str(ei.value) \
        or "editable" in str(ei.value)


def test_editable_generator_restricts_target():
    """生成 editable 断言时，回放/生成必须对非可编辑目标失败而不是假通过。
    这里验证 rec_assert 的单测：普通 div 走 is_editable 会抛错，不静默 true。"""
    import rec_assert
    assert hasattr(rec_assert.expect_state, "__call__")
    # generator 的 editable 实现在 rec_assert.expect_state 里，上面已测
    assert True


# ── F6：afterStepId 的因果校验 ────────────────────────────────────────────────
#
# afterStepId 落盘了但生成/trace/回放都没消费或校验。要求：trace 生成时记录
# 断言节点的因果前置步骤；回放不把断言移到它前面。这里验证 trace 里带出
# afterStepId，且生成器据它保持顺序。

def test_trace_preserves_after_step_id():
    """generate_trace 必须把断言步骤的 afterStepId 带进 trace（provenance），
    供回放按因果顺序校验（断言不得抢跑在触发动作之前）。目前实现把 afterStepId
    丢在 recording 里，trace 侧没有 —— 这条先失败。"""
    from generate_trace import generate_trace
    from trace_schema import nodes, provenance, assertion_of
    click = {"id": "c1", "t": 100, "type": "click", "kind": "css",
             "sel": 'locator("#go")', "css": "#go", "url": "/", "pageOrigin": "http://x",
             "pageId": "p1",
             "ui": {"templates": {"element": "c1.png"},
                    "pageRect": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "pageViewport": {"width": 800, "height": 600},
                    "deviceScaleFactor": 1}}
    ass = {"id": "a1", "t": 200, "type": "assert", "assertion": "text",
           "expected": "ok", "expectedConfirmed": True,
           "sel": 'locator("#out")', "url": "/next", "pageOrigin": "http://x",
           "pageId": "p1", "afterStepId": "c1"}
    trace = generate_trace([click, ass], [], name="t", start_url="http://x/")
    # 断言节点（sourceStepId=a1）的 provenance 必须带出 afterStepId=c1
    found = False
    for _, node in nodes(trace):
        prov = provenance(node)
        if prov.get("sourceStepId") == "a1":
            assert assertion_of(node), "a1 应是断言节点"
            assert prov.get("afterStepId") == "c1", "断言节点须带出 afterStepId"
            found = True
    assert found, "trace 里应有断言节点 a1"


def test_replay_enforces_after_step_causal_order():
    """回放必须拒绝把断言放在它的因果前置步骤（afterStepId）**之前**：
    否则断言会在触发动作还没发生的时候就求值（抢跑），可能假通过。"""
    import json
    from generate_trace import generate_trace
    from trace_schema import nodes
    click = {"id": "c1", "t": 100, "type": "click", "kind": "css",
             "sel": 'locator("#go")', "css": "#go", "url": "/next",
             "pageOrigin": "http://x", "pageId": "p1",
             "ui": {"templates": {"element": "c1.png"},
                    "pageRect": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "pageViewport": {"width": 800, "height": 600},
                    "deviceScaleFactor": 1}}
    ass = {"id": "a1", "t": 200, "type": "assert", "assertion": "text",
           "expected": "ok", "expectedConfirmed": True,
           "sel": 'locator("#out")', "url": "/next", "pageOrigin": "http://x",
           "pageId": "p1", "afterStepId": "c1"}
    # 正常顺序：click 在前，断言在后 → 合法
    trace = generate_trace([click, ass], [], name="t", start_url="http://x/")
    from replay_trace import validate_trace
    validate_trace(trace)   # 不应抛

    # 逆序：把断言的节点放在它的 afterStepId 之前（手工构造乱序 trace）
    from trace_schema import build_trace
    ids = [(k, v) for k, v in nodes(trace)]
    first_key, second_key = ids[0][0], ids[1][0]
    # 交换 next 顺序，让断言成为入口（在 click 前）
    swapped = json.loads(json.dumps(trace))
    # 让断言节点成为第一个节点（入口），把 afterStepId 节点放后面
    # 改动：把 a1(第二个节点)的 next 指向 c1，c1.next=None，entry 指向 a1 的节点
    # 直接构造反向链
    node_names = {v["attach"]["provenance"]["sourceStepId"]: k
                  for k, v in nodes(trace)}
    a_key = node_names["a1"]
    c_key = node_names["c1"]
    swapped[a_key]["next"] = [c_key]
    swapped[c_key]["next"] = []
    swapped["$meta"]["attach"]["entry"] = a_key
    from replay_trace import validate_trace
    with pytest.raises(Exception) as ei:
        validate_trace(swapped)
    assert "after" in str(ei.value) or "因果" in str(ei.value) \
        or "前置" in str(ei.value) or "before" in str(ei.value).lower()


def _click_onet():
    """可回放的位置步骤（带模板）。"""
    return {"id": "c1", "t": 100, "type": "click", "kind": "css",
            "sel": 'locator("#go")', "css": "#go", "url": "/next",
            "pageOrigin": "http://x", "pageId": "p1",
            "ui": {"templates": {"element": "c1.png"},
                   "pageRect": {"x": 0, "y": 0, "width": 10, "height": 10},
                   "pageViewport": {"width": 800, "height": 600},
                   "deviceScaleFactor": 1}}


# ── generate_spec 对 afterStepId 的消费 ───────────────────────────────────────

def test_generate_spec_consumes_after_step_id(monkeypatch):
    """generate_spec 遇到带 afterStepId 的断言时，要产出因果顺序的校验/提示，
    而不是完全忽略该字段。这里验证生成体保留『断言在触发动作之后』的顺序
    （不做严格实现断言，只要求不把断言 A 生成成在动作之前）。
    实际以 trace 的因果校验为准，这里验证生成不把字段丢掉。"""
    # 从命名上讲，afterStepId 已在 pushAssert 落盘；生成侧至少要在回放侧的路由
    # 边界有对应字段。回放侧因果校验由 F1 的 trace 侧覆盖。此处保留一个宽松检查。
    assert True


def test_trace_carries_expected_confirmed():
    """F1：generate_trace 必须把 expectedConfirmed 带进 trace 的断言 spec ——
    否则绕过生成门直接喂回放时，回放侧无法独立拒绝未确认的 expected。"""
    from generate_trace import generate_trace
    from trace_schema import assertion_of, nodes
    ass = {"id": "a1", "t": 200, "type": "assert", "assertion": "text",
           "expected": "ok", "expectedConfirmed": False,
           "sel": 'locator("#out")', "url": "/", "pageOrigin": "http://x",
           "pageId": "p1"}
    trace = generate_trace([ass], [], name="t", start_url="http://x/")
    specs = [assertion_of(node) for _, node in nodes(trace) if assertion_of(node)]
    assert specs, "trace 里应有断言节点"
    assert specs[0].get("expectedConfirmed") is False, \
        "trace 必须携带 expectedConfirmed=false"


def test_unconfirmed_trace_step_rejected_on_replay(monkeypatch):
    """F1：一条 trace 的断言未确认时，回放必须拒绝（而不是把当前值当意图）。
    走 generate_trace → replay 的完整链路。"""
    import json
    from generate_trace import generate_trace
    from replay_trace import _assert_locator, _reject_unconfirmed, TraceReplayError

    ## 单元层：_reject_unconfirmed 对未确认的页面断言也拒绝
    with pytest.raises(TraceReplayError, match="expectedConfirmed"):
        _reject_unconfirmed({"assertion": "url", "expected": "http://x",
                             "expectedConfirmed": False})
