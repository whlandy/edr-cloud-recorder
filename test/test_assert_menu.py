"""断言菜单的实机回归：右键和 Alt/Option+左键入口可用且落对步骤。

菜单在独立 iframe 里（事件树与业务页分离），测试侧用 frame_locator 操作。
"""

import pytest
from playwright.sync_api import sync_playwright

from recorder_loader import recorder_source

HTML = """<!doctype html><meta charset="utf-8"><body>
  <div id="msg" style="padding:20px">订单保存成功，单号 abc-42</div>
  <div id="time" style="padding:20px">操作时间 2026-10-21 12:00:30</div>
  <input id="qty" value="7" style="width:120px">
</body>"""

MENU = '#__rec_assert_menu__'


@pytest.fixture(scope="module")
def rec_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.set_content(HTML)
        page.evaluate(recorder_source())
        yield page
        browser.close()


def _open_menu(page, sel):
    box = page.locator(sel).bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2,
                      button="right")
    menu = page.frame_locator(MENU)
    menu.locator("#t").wait_for(state="visible")
    return menu


def _last_assert_step(page):
    steps = page.evaluate("() => window.__rec.steps")
    return [s for s in steps if s["type"] == "assert"][-1]


def test_alt_click_opens_menu_without_clicking_page_or_recording_action(rec_page):
    rec_page.evaluate("""() => {
      window.__businessEvents = [];
      for (const kind of ['pointerdown', 'mousedown', 'click']) {
        document.querySelector('#msg').addEventListener(
          kind, () => { window.__businessEvents.push(kind); });
      }
    }""")
    before = rec_page.evaluate(
        "() => window.__rec.steps.filter((s) => s.type === 'click').length")

    rec_page.locator("#msg").click(modifiers=["Alt"])
    menu = rec_page.frame_locator(MENU)
    menu.locator("#t").wait_for(state="visible")

    assert rec_page.evaluate("window.__businessEvents") == []
    after = rec_page.evaluate(
        "() => window.__rec.steps.filter((s) => s.type === 'click').length")
    assert after == before
    menu.get_by_role("button", name="取消").click()


def test_text_menu_shows_operator_select(rec_page):
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("text")
    ops = menu.locator("#op option")
    labels = [ops.nth(i).inner_text() for i in range(ops.count())]
    assert "包含" in labels and "正则匹配(全串)" in labels and "为空" in labels
    # 老类型默认「等于」：不显式选别的，落盘步骤不带 op 字段（旧回放兼容）
    menu.locator("#op").select_option("contains")
    menu.locator("#es").fill("abc-42")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "text" and step["op"] == "contains"


def test_value_menu_has_numeric_ops(rec_page):
    menu = _open_menu(rec_page, "#qty")
    menu.locator("#t").select_option("value")
    labels = [menu.locator("#op option").nth(i).inner_text()
              for i in range(menu.locator("#op option").count())]
    assert "大于(数值)" in labels and "小于(数值)" in labels
    menu.locator("#op").select_option("greater_than")
    menu.locator("#es").fill("5")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "value" and step["expected"] == "5"
    assert step["op"] == "greater_than"


def test_count_menu_records_integer(rec_page):
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("count")
    # 期望值输入框在数量类型下变成数字框
    assert menu.locator("#es").get_attribute("type") == "number"
    labels = [menu.locator("#op option").nth(i).inner_text()
              for i in range(menu.locator("#op option").count())]
    assert labels == ["等于", "至少", "至多"]
    # 非整数 / 空要拦住
    menu.locator("#es").fill("1.5")
    assert menu.get_by_role("button", name="添加断言").is_disabled()
    menu.locator("#es").fill("2")
    menu.locator("#op").select_option("at_least")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "count" and step["expected"] == 2
    assert step["op"] == "at_least"


def test_timerange_menu_records_prefix_and_duration(rec_page):
    menu = _open_menu(rec_page, "#time")
    menu.locator("#t").select_option("timerange")
    # 起点从显示文本里抠出纯时间；到秒的起点默认时长 1 秒
    assert menu.locator("#es").input_value() == "12:00:30"
    assert menu.locator("#dur").input_value() == "1"
    # 预览要写出区间边界，人不用心算
    preview = menu.locator("#fmtNow").inner_text()
    assert "12:00:31" in preview
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "text"
    assert step["expected"] == "12:00:30"
    assert step["expectedFrom"] == {"kind": "timerange", "durationSeconds": 1}


def test_timerange_minute_prefix_defaults_to_whole_minute(rec_page):
    menu = _open_menu(rec_page, "#time")
    menu.locator("#t").select_option("timerange")
    menu.locator("#es").fill("12:00")
    assert menu.locator("#dur").input_value() == "60"
    preview = menu.locator("#fmtNow").inner_text()
    assert "12:01:00" in preview


def test_blank_op_requires_no_expected(rec_page):
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("text")
    menu.locator("#op").select_option("is_blank")
    # 清空 expected 后不应再报「为空要勾允许空值」
    menu.locator("#es").fill("")
    assert menu.get_by_role("button", name="添加断言").is_enabled()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["op"] == "is_blank"
    assert step["expected"] == ""


def test_string_expected_prefilled_requires_confirmation(rec_page):
    """P0：文本/输入值断言的 expected 预填自当前值，未确认前不能保存。

    菜单不得把「当前看到什么」直接当作用户意图 —— 必须修改或勾选
    「确认使用当前值」后保存才可用（references/common-web-assertion-design.md §4.2/§6）。
    """
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("text")
    # expected 预填自元素当前文本
    assert menu.locator("#es").input_value() == "订单保存成功，单号 abc-42"
    # 未修改也未勾选确认 → 保存被禁用
    assert menu.get_by_role("button", name="添加断言").is_disabled()
    # 勾选确认后可用
    menu.locator("#confirm").check()
    assert menu.get_by_role("button", name="添加断言").is_enabled()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step.get("expectedConfirmed") is True


def test_string_expected_edited_counts_as_confirmation(rec_page):
    """P0：用户把预填值改成别的内容，即实打实的命题，无需再勾选确认。"""
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("text")
    menu.locator("#es").fill("abc-42")          # 修改预填值
    assert menu.get_by_role("button", name="添加断言").is_enabled()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["expected"] == "abc-42"
    assert step.get("expectedConfirmed") is True


def test_assert_records_after_step_id_and_wait(rec_page):
    """P0：断言记录直接因果前置步骤 id（afterStepId）与等待策略（wait）。

    先录一个普通点击作为前置动作，随后的断言必须带上该步骤 id ——
    生成器据此保证不把断言移到触发动作之前（§7 因果顺序）。
    """
    rec_page.click("#msg")
    before = [s for s in rec_page.evaluate("() => window.__rec.steps")
              if s["type"] == "click"]
    assert before, "普通点击没被录成步骤"
    trigger_id = before[-1]["id"]

    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("text")
    menu.locator("#es").fill("保存成功")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["afterStepId"] == trigger_id
    assert step["expectedConfirmed"] is True
    wait = step["wait"]
    assert wait["timeoutMs"] > 0 and wait["pollMs"] > 0
    # 文本「包含/等于」是非负断言 → 默认无稳定窗口；负断言才要求稳定
    assert wait["stableMs"] == 0


def test_negative_assert_defaults_to_stable_window(rec_page):
    """P0：负断言（不包含 / 不可见 / count=0）默认带稳定窗口，防动画中间态抢跑。"""
    # count=0（断言「列表空」）
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("count")
    menu.locator("#es").fill("0")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "count" and step["expected"] == 0
    assert step["wait"]["stableMs"] == 300


def test_menu_shows_selector_and_match_count(rec_page):
    """P0（§4.2 目标预览）：菜单显示目标选择器与匹配数量，后者用于消歧提示。"""
    menu = _open_menu(rec_page, "#msg")
    preview = menu.locator("#targetPreview").inner_text()
    assert "匹配" in preview
    # 唯一选择器（id 定位）不应标「多元素」
    assert "多元素" not in preview
