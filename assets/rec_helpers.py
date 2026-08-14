"""回放用例的通用助手（fixtures.ts 里那三个 export 的 Python 版）。"""

import json
import re
from dataclasses import dataclass
from typing import Any, Pattern

from playwright.sync_api import Locator, Page

from rec_assert import poll_until

DEFAULT_CONFIRM = re.compile(r"确认|确定|OK")


def click_if_present(target: Locator) -> bool:
    """点击可选元素 —— 存在才点，不存在直接跳过。

    首次引导、公告、提示条这类元素出现与否取决于账号状态和历史操作。
    录制时它出现了，回放时可能不出现；当成必经步骤会在等待时超时。

    注意 try/except：元素不存在时 is_visible() 会抛错，
    不接住的话容错逻辑本身会变成失败点。
    """
    try:
        if target.is_visible():
            try:
                target.click()
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


@dataclass
class Captured:
    status: int
    request_body: Any
    response_body: str


def confirm_and_capture(
    page: Page,
    *,
    trigger: Locator,
    url_pattern: str | Pattern[str],
    confirm_name: str | Pattern[str] | None = None,
    method: str = "POST",
) -> Captured:
    """执行一个带二次确认的操作，并返回实际发出的请求。

    漏掉确认弹窗是「静默通过」假测试的头号来源：脚本点了「删除」，断言也过了，
    但因为没点「确认」，其实什么都没发生。把两步绑在一起，调用方就没机会忘。

    返回请求体和状态码，这样用例可以断言接口契约而不只是界面文字 ——
    界面文案会改，接口契约不会轻易改。

    与 JS 版的差异：JS 用 waitForRequest/waitForResponse 先拿到两个 promise
    再点击；Python sync API 的等价物是 expect_request/expect_response 两个
    上下文管理器。语义一致 —— 监听在动作**之前**就建立好了，不会漏掉
    点击后立刻发出的请求。
    """
    confirm = DEFAULT_CONFIRM if confirm_name is None else confirm_name

    def match(url: str) -> bool:
        return url_pattern in url if isinstance(url_pattern, str) \
            else bool(url_pattern.search(url))

    with page.expect_request(lambda r: r.method == method and match(r.url)) as req_info, \
         page.expect_response(
             lambda r: r.request.method == method and match(r.url)) as res_info:
        trigger.click()
        page.get_by_role("button", name=confirm).click()

    req = req_info.value
    res = res_info.value

    body = None
    if req.post_data:
        try:
            body = json.loads(req.post_data)
        except ValueError:
            body = req.post_data

    try:
        text = res.text()
    except Exception:
        # 响应体只在页面还没导航走的时候取得到，取不到不该让整个用例挂
        text = ""

    return Captured(status=res.status, request_body=body, response_body=text)


def is_present(target: Locator) -> bool:
    """元素在不在（不存在不抛错）。

    生成的草稿里用它守住 CSS 兜底那类「有就点、没有就跳过」的动作。
    JS 侧写成 `isVisible().catch(() => false)`，Python 的 if 里塞不进 try，
    所以单独成一个函数。
    """
    try:
        return target.is_visible()
    except Exception:
        return False


def nth_request(url_part: str, method: str, n: int = 1):
    """返回一个谓词：只在第 n 次匹配上时为真。

    同一步可能向同一端点并发发多次请求。相同谓词的 expect_request 都会命中
    第一条，于是两个等待器抢同一条请求、另一条没人接。给每个等待器一个独立
    计数器，第 N 个等待器就只接第 N 条。
    """
    seen = [0]

    def pred(request) -> bool:
        if url_part in request.url and request.method == method:
            seen[0] += 1
            return seen[0] == n
        return False

    return pred


DEFAULT_CLOSERS = [
    "span.eui_Dialog_closeIcon",
    ".eui_tipBox_close",
    '[class*="closeIcon"]',
]
DEFAULT_MASKS = ('.eui-dialog-masking, .eui_Dialog_Over, '
                 '[class*="masking"], [class*="Dialog_Over"]')


def dismiss_overlays(page: Page, selectors: list[str] | None = None,
                     masks: str = DEFAULT_MASKS,
                     timeout: float = 15_000, probe: float = 2_000) -> None:
    """关掉首启弹窗，并等到没有遮罩挡路为止。

    这一步是回放稳定性的分水岭，原因不直观：

    1. **弹窗常常不止一个。** 实测某控制台首启会叠两个（校验码 + 校验码历史），
       各带一层遮罩。只关一个，剩下那层照样吞掉后面所有点击。
    2. **遮罩关掉后还会残留一会儿**，而且它拦截点击时，Playwright 认为
       click «成功了» —— 失败会报在后面某个 expect_response 上，
       看着像「接口没发」，实际是「点了没进去」。
    3. **遮罩常驻 DOM，只靠 CSS 隐藏**，所以判「可见数为 0」，
       判 to_have_count(0) 会永远等不到。

    关闭按钮的 class 因组件库而异，用 selectors 参数覆盖。

    probe / timeout：先用 probe 毫秒探一下，没等到就检查 DOM 里有没有这类元素。
    一个都没有就立刻返回（站点不走这套，不该每条用例白等十几秒）；有但还没显示，
    才继续等到 timeout。如果你的弹窗**渲染得比 probe 还晚、而且此前完全不在 DOM 里**，
    把 probe 调大。
    """
    closers = page.locator(", ".join(selectors or DEFAULT_CLOSERS))

    # 等第一个弹窗出现再动手：紧跟 goto 就问「在不在」，那一刻页面还是空的，
    # 「存在才点」会静默返回 False —— 弹窗没关掉，而失败要到很后面才暴露。
    #
    # 但不能无脑等满 timeout：目标站点根本不用这几个 class 时，那是每条用例
    # 都要白付的十几秒。所以分两段 —— 先短探针，没等到就看 DOM 里究竟有没有
    # 这类元素（哪怕还隐藏着）。一个都没有，说明这个站点不走这套，直接返回。
    try:
        closers.first.wait_for(state="visible", timeout=probe)
    except Exception:
        try:
            present = closers.count() or page.locator(masks).count()
        except Exception:
            present = 0
        if not present:
            return                       # 这个站点没有这类遮罩，不用再等
        # DOM 里有，只是还没显示出来 —— 那就值得等满
        try:
            closers.first.wait_for(state="visible", timeout=max(0, timeout - probe))
        except Exception:
            pass

    for _ in range(5):
        btn = closers.filter(visible=True).first
        try:
            if not btn.is_visible():
                break
        except Exception:
            break
        try:
            btn.click()
        except Exception:
            pass
        page.wait_for_timeout(400)

    # JS 侧用 expect.poll，Python 没有 —— 换成 poll_until。
    # 没有遮罩的站点直接过，不该因此失败。
    try:
        poll_until(lambda: page.locator(masks).filter(visible=True).count(), 0,
                   timeout=15.0, interval=0.3)
    except AssertionError:
        print("⚠ 遮罩层一直没消失，后续点击可能被它拦住")


def snapshot(page: Page, url: str) -> str:
    """读取某个资源的原始响应文本 —— 用于基线快照。

    返回文本而不是解析后的对象，因为比对必须逐字节做：字段顺序、数字精度、
    空值表示（null vs 缺失）的差异，只有字节比较才抓得住。
    """
    return page.evaluate(
        "async (u) => { const r = await fetch(u); return r.text(); }", url
    )
