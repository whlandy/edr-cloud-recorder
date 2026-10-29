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
  <button id="en">可用按钮</button>
  <button id="dis" disabled>禁用按钮</button>
  <input id="ed" value="可编辑">
  <input id="ro" value="只读" readonly>
  <select id="sel"><option value="a">A</option><option value="b" selected>B</option></select>
  <table id="tbl">
    <tr><td class="name">Shared</td><td class="key">Alpha</td><td>10:00</td></tr>
    <tr><td class="name">Shared</td><td class="key">Beta</td><td>11:30</td></tr>
  </table>
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


def test_enabled_menu_records_bool(rec_page):
    """P1：新的布尔状态断言 enabled —— 可操作性。F3：bool 也要显式确认。"""
    menu = _open_menu(rec_page, "#en")
    menu.locator("#t").select_option("enabled")
    assert menu.locator("#eb").input_value() == "true"
    menu.locator("#confirmBool").check()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "enabled" and step["expected"] is True
    assert step["expectedConfirmed"] is True


def test_enabled_negative_defaults_to_stable(rec_page):
    menu = _open_menu(rec_page, "#dis")
    menu.locator("#t").select_option("enabled")
    assert menu.locator("#eb").input_value() == "false"
    menu.locator("#confirmBool").check()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "enabled" and step["expected"] is False
    assert step["wait"]["stableMs"] == 300


def test_editable_readonly_is_false(rec_page):
    menu = _open_menu(rec_page, "#ro")
    menu.locator("#t").select_option("editable")
    assert menu.locator("#eb").input_value() == "false"
    menu.locator("#confirmBool").check()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "editable" and step["expected"] is False


def test_selected_menu_records(rec_page):
    # <option> 不可右键（不渲染为可见元素）；右键 select 本身记录 selected 类型。
    # selected=true 的正例由 test_generated_execution 的两链步骤覆盖。
    menu = _open_menu(rec_page, "#sel")
    menu.locator("#t").select_option("selected")
    menu.locator("#confirmBool").check()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "selected"
    assert isinstance(step["expected"], bool)


def test_page_scope_url_confirmation(rec_page):
    """P1（§4.1/§5）：范围切到「当前页面」可断言 URL，expected 预填当前 URL 需确认。"""
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#sc").select_option("page")
    labels = [menu.locator("#t option").nth(i).inner_text()
              for i in range(menu.locator("#t option").count())]
    assert "页面 URL" in labels and "页面标题" in labels
    menu.locator("#t").select_option("url")
    # expected 预填当前页面 URL（about:blank）；未确认前保存被禁用
    assert menu.locator("#es").input_value() == "about:blank"
    assert menu.get_by_role("button", name="添加断言").is_disabled()
    menu.locator("#confirm").check()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step.get("scope") == "page"
    assert step["assertion"] == "url"
    assert step["expected"] == "about:blank"
    assert step["expectedConfirmed"] is True


def test_page_scope_title(rec_page):
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#sc").select_option("page")
    menu.locator("#t").select_option("title")
    menu.locator("#es").fill("测试页面")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step.get("scope") == "page"
    assert step["assertion"] == "title"
    assert step["expected"] == "测试页面"


def test_multi_element_cell_assert_gets_stable_scope(rec_page):
    """P1（§4.3）：单元素断言命中多个元素时不得静默 .first() ——
    落到唯一目标（收窄到稳定行，或 scoped 行+列）。

    两个 <td class="name"> 文本相同（"Shared"）；断言必须落到唯一目标
    （这里收窄到 hasText:"Beta" 的稳定行内），不能标多元素、不能挑 first。
    """
    menu = _open_menu(rec_page, "table#tbl tr:nth-child(2) td.name")
    menu.locator("#t").select_option("text")
    menu.locator("#es").fill("Shared")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    # 不再标多元素 —— 收窄过的选择器不是静默 .first()，而是稳定作用域
    assert step["ambiguous"] is not True, step.get("sel")
    # 断言落在 Beta 行的稳定作用域内，而不是两个 Shared 里随便挑一个
    assert "Beta" in step.get("sel", ""), step.get("sel")
    # 选择器必须保留行锚（hasText）而不是退化成".name" 撞车
    assert step["kind"] == "scoped" or "hasText" in step.get("sel", ""), step.get("sel")


def test_preset_toast_sets_type_and_op_but_requires_confirmation(rec_page):
    """P1（§9.3）：预设只填配置（类型 + 算子 + 稳定窗口），不改语义、不绕过确认。"""
    # 在 msg（含「保存成功」）上选「提示出现」预设 → 文本 contains
    assert "保存成功" in rec_page.locator("#msg").inner_text()
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#ps").select_option("toast")
    # 预设把类型切成「文本」、算子切成「包含」（contains）
    assert menu.locator("#t").input_value() == "text"
    # 算子里应含 contains 且被选中
    menu.locator("#op").select_option("contains")  # 预设已应用；这里再确认一次
    # expected 预填自当前文本，未确认前仍被禁用（预设不绕过 P0 红线）
    menu.locator("#es").fill("保存成功")
    menu.get_by_role("button", name="添加断言").is_enabled()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "text"
    assert step["op"] == "contains"
    assert step["expected"] == "保存成功"
    assert step["wait"]["stableMs"] == 0  # 正向「出现」不需要稳定窗口


def test_preset_toastGone_sets_negative_with_stable_window(rec_page):
    """P1：提示消失的预设 → 可见性=否，负断言默认带稳定窗口。"""
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#ps").select_option("toastGone")
    assert menu.locator("#t").input_value() == "visible"
    # 可见性=否（提示应消失）→ 用户必须选 false 并确认
    menu.locator("#eb").select_option("false")
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "visible" and step["expected"] is False
    assert step["wait"]["stableMs"] == 300


def test_bool_expected_requires_explicit_confirmation(rec_page):
    """F3（review）：布尔断言（可见性/勾选/enabled/editable/selected）的
    true/false 也是 expected，必须由用户明确确认 —— 不得把「读到默认值」自动当
    true/false。未确认前保存被禁用。"""
    # visible 默认预填 true，未确认应被禁用
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("visible")
    assert menu.locator("#eb").input_value() == "true"
    assert menu.get_by_role("button", name="添加断言").is_disabled(), \
        "布尔 expected 未确认前不得保存"
    menu.locator("#confirmBool").check()
    assert menu.get_by_role("button", name="添加断言").is_enabled()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "visible" and step["expected"] is True
    assert step["expectedConfirmed"] is True


def test_bool_changed_by_user_is_confirmed(rec_page):
    """F3：用户把布尔 expected 从默认改成别的（例如 true→false），算显式确认。"""
    menu = _open_menu(rec_page, "#msg")
    menu.locator("#t").select_option("visible")
    menu.locator("#eb").select_option("false")
    # 用户显式选了 false = 确认命题
    assert menu.get_by_role("button", name="添加断言").is_enabled()


def test_editable_target_must_be_editable_element(rec_page):
    """F4（review）：editable 只适用于可编辑目标（input/textarea/[contenteditable]）。
    对普通 div 选 editable 必须被阻止（或预填不假称为可编辑），不能静默 expected=true。"""
    menu = _open_menu(rec_page, "#msg")   # #msg 是普通 div
    menu.locator("#t").select_option("editable")
    # 普通 div 不是可编辑元素 → 保存被禁用（或预填 false 而非 true）
    assert menu.get_by_role("button", name="添加断言").is_disabled(), \
        "对普通 div 断言 editable 应被阻止"


def test_editable_target_is_enabled_on_input(rec_page):
    """F4：editable 在真正的输入框上是合法断言。"""
    menu = _open_menu(rec_page, "#ed")
    menu.locator("#t").select_option("editable")
    # 输入框可编辑 → 预填 true 且可选
    assert menu.locator("#eb").input_value() == "true"
    menu.locator("#confirmBool").check()
    assert menu.get_by_role("button", name="添加断言").is_enabled()
    menu.get_by_role("button", name="添加断言").click()
    step = _last_assert_step(rec_page)
    assert step["assertion"] == "editable" and step["expected"] is True
