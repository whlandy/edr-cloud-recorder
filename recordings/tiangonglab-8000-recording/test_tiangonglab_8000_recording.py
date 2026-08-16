import os
from pathlib import Path

from playwright.sync_api import Page, expect

from rec_assert import ANY_NUM, ANY_STR, assert_subset, poll_until
from rec_helpers import dismiss_overlays, is_present, nth_request
from rec_visual import visual_click

# 由 web-record 生成：tiangonglab-8000-recording
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
#
# 已自动去掉开头 6 步登录（改用登录态复用）。原步骤：
#   click getByPlaceholder("用户名")
#   press getByPlaceholder("用户名")
#   fill getByPlaceholder("用户名")
#   press getByPlaceholder("用户名")
#   fill getByPlaceholder("密码")  <密码，未记录>
#   click getByRole("button", { name: "登录", exact: true })

def test_tiangonglab_8000_recording(authed_page: Page):
    page = authed_page
    page.goto("/")
    dismiss_overlays(page)
    visual_click(page, page.get_by_role("button", name="7天", exact=True), template_root=Path(__file__).parent, ui={'pageRect': {'x': 273.8515625, 'y': 64, 'width': 43.8359375, 'height': 27.5}, 'click': {'x': 307, 'y': 84, 'rx': 0.7561931919443949, 'ry': 0.7272727272727273}, 'templates': {'element': {'path': 'assets/step-0007.element.png', 'sha256': 'b9ce030ccf29bdad38149cf0095e142a21788f29b9c4b418a5c154a37abb7bcf', 'width': 87, 'height': 55}, 'context': {'path': 'assets/step-0007.context.png', 'sha256': '48a66875ed6ce46f3109772786627a7ecd92d40b04fd4a3626c30bec5014844b', 'width': 135, 'height': 103, 'elementOffset': {'x': 24, 'y': 24}}}})
    #   ↳ GET /api/stats/summary?days=7 -> 200
    #   ↳ GET /api/stats/timeline?days=7 -> 200
    #   ↳ GET /api/stats/models?days=7 -> 200
    visual_click(page, page.get_by_role("button", name="30天", exact=True), template_root=Path(__file__).parent, ui={'pageRect': {'x': 324.6875, 'y': 64, 'width': 52.0859375, 'height': 27.5}, 'click': {'x': 370, 'y': 72, 'rx': 0.8699565021748913, 'ry': 0.2909090909090909}, 'templates': {'element': {'path': 'assets/step-0008.element.png', 'sha256': 'f6002a300f2b674e50adaec669c291ce56074e14c32c1c30e1a18637c419be1f', 'width': 105, 'height': 55}, 'context': {'path': 'assets/step-0008.context.png', 'sha256': '24ee52d573e2cd4dee3cc06eef397ab672f415826133728db15988abc5319b20', 'width': 153, 'height': 103, 'elementOffset': {'x': 24, 'y': 24}}}})
    #   ↳ GET /api/stats/models?days=30 -> 200
    #   ↳ GET /api/stats/summary?days=30 -> 200
    #   ↳ GET /api/stats/timeline?days=30 -> 200
    expect(page.locator("tr", has_text="4.4K").get_by_text("MiniMax-M3", exact=True)).to_have_text("MiniMax-M3")
