#!/usr/bin/env python3
"""web-record —— 把网页操作录成 pytest 脚本，并关联触发的接口（record.mjs 的 Python 版）。

启动一个 Playwright 控制的浏览器，你在里面正常操作，脚本负责：
  - 记录每一次点击 / 输入 / 勾选 / 回车，为元素算出最稳的选择器
  - 从驱动侧抓所有 XHR/fetch（含请求体、状态码、失败响应体）
  - 按时间把接口调用挂到触发它的那一步下面
  - 结束时输出原始 JSON、完整成功 trace 和可直接跑的 pytest 用例草稿

与 `playwright codegen` 的区别：codegen 只产选择器，不记录接口。
当你的目标是「搞清楚这个操作到底打了哪些接口、请求体长什么样」时，
codegen 给不了答案。

用法：
  python record.py --url https://app.example.com
  python record.py --url ... --name login-flow
  python record.py --url ... --api '/api/'        # 只记录路径含 /api/ 的请求
  python record.py --url ... --out ./recordings

环境变量：
  REC_CHROME_BIN   指定浏览器可执行文件（默认自动探测）
  REC_STATE_DIR    登录态目录（默认 ./.auth）
"""

import argparse
import base64
import hashlib
import inspect
import json
import re
import struct
import sys
import time
import weakref
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "assets"))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit(
        "找不到 playwright。录制器需要它：\n"
        "  python -m pip install playwright\n"
        "  python -m playwright install chromium\n"
        "\n"
        "（本机 ms-playwright 缓存里已有构建的话，最后一步可以跳过 ——\n"
        "  chrome_path.py 会自动复用。）"
    )

from chrome_path import resolve_chrome                      # noqa: E402
from generate_spec import _ident, generate_spec             # noqa: E402
from generate_trace import POSITIONAL_STEP_TYPES, generate_trace  # noqa: E402
from rec_config import ConfigError, load_config, with_defaults  # noqa: E402
from rec_session import restore_context_session, write_session_snapshot  # noqa: E402
from rec_secrets import redact_text                            # noqa: E402
from recorder_loader import recorder_source                 # noqa: E402

DRAIN = "() => (window.__rec ? window.__rec.drain() : [])"
PUMP_MS = 200
VISUAL_STEP_TYPES = POSITIONAL_STEP_TYPES
PRE_ACTION_ONLY_TYPES = {"check", "uncheck", "switch"}
PRE_FRAME_MAX_AGE_MS = 1_500
CONTEXT_PADDING_CSS = 12
# 登录态只能趁页面还活着时拍。不能在轮询里调用 context.storage_state()：
# 当上下文访问过当前页面未覆盖的第三方 origin 时，Playwright 会创建可见临时页、
# 逐个导航后再关闭，表现就是浏览器窗口不断闪烁。这里直接读取 cookies 和现有
# frame 的 localStorage，不创建任何页面。


def _request_body_fields(request) -> dict:
    """读取请求体；Playwright 的 post_data 会在二进制内容上强制解码 UTF-8。"""
    try:
        return {"body": redact_text(request.post_data)}
    except UnicodeDecodeError:
        raw = request.post_data_buffer
        if raw is None:
            return {"body": None}
        return {
            "body": None,
            "bodyBase64": base64.b64encode(raw).decode("ascii"),
            "bodyEncoding": "base64",
        }


def _capture_storage(context, page, origins: dict) -> dict:
    """生成 Playwright storage_state 结构，但只检查已经存在的 frame。"""
    cookies = context.cookies()
    for frame in page.frames:
        try:
            parts = urlsplit(frame.url)
            if not parts.scheme or not parts.netloc:
                continue
            origin = f"{parts.scheme}://{parts.netloc}"
            local_storage = frame.evaluate(
                "() => Object.entries(localStorage)"
                ".map(([name, value]) => ({name, value}))"
            )
            if local_storage:
                origins[origin] = {"origin": origin, "localStorage": local_storage}
            else:
                origins.pop(origin, None)
        except Exception:
            continue
    return {"cookies": cookies, "origins": list(origins.values())}


def _png_size(data: bytes) -> tuple[int, int] | None:
    """不引入 Pillow，直接读取 PNG 的 IHDR 宽高。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def _visible_clip(ui: dict) -> dict | None:
    """把元素矩形裁进当前 viewport，避免 Playwright 拒绝越界 clip。"""
    rect = ui.get("rect") or {}
    viewport = ui.get("viewport") or {}
    try:
        left = max(0.0, float(rect["x"]))
        top = max(0.0, float(rect["y"]))
        right = min(float(viewport["width"]), float(rect["x"]) + float(rect["width"]))
        bottom = min(float(viewport["height"]), float(rect["y"]) + float(rect["height"]))
    except (KeyError, TypeError, ValueError):
        return None
    if right - left < 1 or bottom - top < 1:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _template_meta(path: Path, data: bytes, asset_dir: Path) -> dict:
    meta = {
        "path": f"{asset_dir.name}/{path.name}",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    size = _png_size(data)
    if size:
        meta.update(width=size[0], height=size[1])
    return meta


def _crop_pre_frame(pre_frame: dict, step: dict, asset_dir: Path,
                    index: int) -> bool:
    """从点击前 viewport 帧裁出元素和上下文模板。"""
    action_t = step.get("actionT", step.get("t", 0))
    age = action_t - pre_frame.get("t", 0) if pre_frame else -1
    if not pre_frame or not 0 <= age <= PRE_FRAME_MAX_AGE_MS:
        return False
    ui = step["ui"]
    rect = ui.get("pageRect")
    viewport = ui.get("pageViewport")
    if not rect or not viewport:
        return False
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(pre_frame["data"], np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return False
        sx = image.shape[1] / float(viewport["width"])
        sy = image.shape[0] / float(viewport["height"])
        x1 = max(0, round(float(rect["x"]) * sx))
        y1 = max(0, round(float(rect["y"]) * sy))
        x2 = min(image.shape[1], round((float(rect["x"]) + float(rect["width"])) * sx))
        y2 = min(image.shape[0], round((float(rect["y"]) + float(rect["height"])) * sy))
    except (ImportError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    if x2 <= x1 or y2 <= y1:
        return False

    pad_x, pad_y = round(CONTEXT_PADDING_CSS * sx), round(CONTEXT_PADDING_CSS * sy)
    cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    cx2, cy2 = min(image.shape[1], x2 + pad_x), min(image.shape[0], y2 + pad_y)
    element = image[y1:y2, x1:x2]
    context = image[cy1:cy2, cx1:cx2]
    element_path = asset_dir / f"step-{index:04d}.element.png"
    context_path = asset_dir / f"step-{index:04d}.context.png"
    ok_element, element_data = cv2.imencode(".png", element)
    ok_context, context_data = cv2.imencode(".png", context)
    if not ok_element or not ok_context:
        return False

    asset_dir.mkdir(parents=True, exist_ok=True)
    element_bytes, context_bytes = element_data.tobytes(), context_data.tobytes()
    element_path.write_bytes(element_bytes)
    context_path.write_bytes(context_bytes)
    ui["templates"] = {
        "element": _template_meta(element_path, element_bytes, asset_dir),
        "context": {
            **_template_meta(context_path, context_bytes, asset_dir),
            "elementOffset": {"x": x1 - cx1, "y": y1 - cy1},
        },
    }
    return True


def _select_pre_frame(history: list[dict], action_t: int) -> dict | None:
    candidates = [
        frame for frame in history
        if 0 <= action_t - frame["t"] <= PRE_FRAME_MAX_AGE_MS
    ]
    return dict(max(candidates, key=lambda frame: frame["t"])) if candidates else None


def _select_source_pre_frame(histories: dict, source: dict | None,
                             action_t: int) -> dict | None:
    """Only use a viewport captured from the page that produced the action."""
    page = (source or {}).get("page")
    if page is None:
        return None
    return _select_pre_frame(histories.get(page, []), action_t)


def _describe_frame_chain(frame) -> list[dict]:
    """Describe nested iframe elements from the top page down to ``frame``."""
    chain = []
    current = frame
    while current is not None and current.parent_frame is not None:
        element = current.frame_element()
        item = {"url": current.url}
        for attr in ("src", "id", "name"):
            value = element.get_attribute(attr)
            if value:
                item[attr] = value
        index = element.evaluate(
            "el => Array.from(el.ownerDocument.querySelectorAll('iframe, frame')).indexOf(el)"
        )
        if isinstance(index, int) and index >= 0:
            item["index"] = index
        chain.append(item)
        current = current.parent_frame
    chain.reverse()
    return chain


def _capture_ui_template(source: dict, step: dict, asset_dir: Path,
                         index: int, pre_frame: dict | None = None) -> None:
    """保存定位型动作的渲染模板；任何失败都降级为仅保留 ui 元数据。"""
    if step.get("type") not in VISUAL_STEP_TYPES or not step.get("ui"):
        return
    if _crop_pre_frame(pre_frame, step, asset_dir, index):
        return
    if step.get("type") in PRE_ACTION_ONLY_TYPES:
        return

    frame = source.get("frame") if source else None
    page = source.get("page") if source else None
    if frame is None:
        return

    asset_dir.mkdir(parents=True, exist_ok=True)
    path = asset_dir / f"step-{index:04d}.element.png"
    try:
        data = frame.locator(step["css"]).first.screenshot(path=str(path))
    except Exception:
        clip = _visible_clip(step["ui"])
        if step.get("inFrame") or page is None or clip is None:
            path.unlink(missing_ok=True)
            return
        try:
            data = page.screenshot(path=str(path), clip=clip)
        except Exception:
            path.unlink(missing_ok=True)
            return

    step["ui"]["templates"] = {"element": _template_meta(path, data, asset_dir)}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="record.py",
        description="把网页操作录成 pytest 脚本，并关联触发的接口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="环境变量:\n"
               "  REC_CHROME_BIN     指定浏览器可执行文件（默认自动探测）\n"
               "  REC_STATE_DIR      登录态目录，默认 ./.auth\n"
               "  REC_URL            起始页地址（--url 未给时使用）",
    )
    p.add_argument("--url", help="起始页地址（也可用 REC_URL 环境变量或配置文件）")
    p.add_argument("--name", help="输出文件名，默认按时间戳生成")
    p.add_argument("--api", help="只记录 URL 含该片段的请求，默认记录全部 XHR/fetch")
    p.add_argument("--out", help="输出目录，默认 ./recordings")
    p.add_argument("--config", help="配置文件路径，默认读当前目录的 config.json")
    p.add_argument("--headless", action="store_true",
                   help="无头模式。人工录制别用 —— 给 CI 冒烟和自检用的")
    return p.parse_args(argv)


def _artifact_paths(out_dir: str | Path, name: str) -> dict[str, Path]:
    """Return the self-contained directory layout for one recorded case."""
    if (
        not name
        or name in {".", ".."}
        or any(separator in name for separator in ("/", "\\"))
        or Path(name).name != name
    ):
        raise ValueError("用例名必须是单个目录名，不能包含路径分隔符")
    case_dir = Path(out_dir).resolve() / name
    return {
        "case_dir": case_dir,
        "asset_dir": case_dir / "assets",
        "auth_dir": case_dir / ".auth",
        "raw_file": case_dir / "recording.json",
        "trace_file": case_dir / "trace.json",
        "spec_file": case_dir / f"test_{_ident(name)}.py",
    }


def main(argv=None) -> int:
    import os

    args = parse_args(argv)

    # 凭据不在这里读 —— 录制器本身不需要，登录由使用者在浏览器里手动完成
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        sys.exit(str(e))
    opts = with_defaults(cfg, url=args.url, api=args.api, out=args.out)

    if not opts["url"]:
        sys.exit("缺少起始页地址。用 --url 指定，或设置 REC_URL，或在 config.json 里配 baseUrl。")

    record_session(
        start_url=opts["url"],
        name=args.name,
        api_filter=opts["api_filter"],
        out_dir=opts["out_dir"],
        state_dir=os.environ.get("REC_STATE_DIR", ".auth"),
        chrome_bin=opts["chrome_bin"],
        headless=args.headless,
    )
    return 0


def record_session(*, start_url, name=None, api_filter=None, out_dir="recordings",
                   state_dir=".auth", chrome_bin=None, headless=False,
                   on_ready=None) -> dict:
    """录一次，返回小结。

    on_ready 是**测试接缝**：传进来就用它驱动页面（回调自己负责把 page 关掉），
    不传就走正常路径 —— 等用户在浏览器窗口里操作完、自己关窗口。
    录制器本体的行为两条路完全一样，差别只在「谁来操作」。

    回调可以只收 page，也可以收 (page, pump)。收了 pump 就该在每次操作前调，
    它做的正是人工路径那条轮询循环做的事（搬运步骤、抓模板、刷新 viewport
    前帧）。不调的话，延迟上报的动作（开关、勾选）会因为没有动作前的帧而
    缺模板 —— 那是接缝本身的失真，不是页面的问题。
    """
    name = name or "session-" + datetime.now().isoformat(
        timespec="seconds").replace(":", "-")
    out_dir = Path(out_dir).resolve()
    artifacts = _artifact_paths(out_dir, name)
    case_dir = artifacts["case_dir"]
    asset_dir = artifacts["asset_dir"]
    state_dir = Path(state_dir).resolve()
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(start_url))

    steps, seen, net, pending_visual, pending_context = [], set(), [], [], []
    frame_histories: dict[object, list[dict]] = {}

    def accept(step, source=None):
        if not step or not step.get("id"):
            return
        # 升级记录：开关的状态变化可能被二次确认挡在后面，录制器会先记一条普通
        # 点击、之后再用同一个 id 把它改写成「拨到指定状态」。按 id 覆盖，
        # 不能当成重复上报丢掉 —— 丢了就退回盲点，回放时可能朝反方向拨。
        # 升级也走双通道，两份内容一样，应用一次就够（第二次只是噪音）
        if step.get("_upgrade") and f"{step['id']}:upgrade" in seen:
            return
        if step.get("_upgrade"):
            for old in steps:
                if old["id"] != step["id"]:
                    continue
                was = old["type"]
                # 只接受语义字段。视觉字段必须保留点击那一刻抓的：
                #   - ui.templates 由 capture_pending 事后写进**这个字典对象**，
                #     换成新字典就成了孤儿，模板全丢（轨迹会变 incomplete）；
                #     所以原地改，不替换。
                #   - 模板要的是「拨之前」的样子。升级发生在状态已经变了之后，
                #     那时再截图描述的是目标状态，回放时反而对不上。
                # 升级记录可以用 _only 指明自己只想改哪几个字段。默认那份白名单
                # 适合开关（要换成状态层的选择器）；只加一个标记的升级不能走它，
                # 否则会用事后重算的选择器覆盖点击时算出的好选择器。
                allowed = step.get("_only") or (
                    "type", "to", "via", "sel", "kind",
                    "ambiguous", "matches", "label", "css", "dismissesOverlay",
                )
                old.update({
                    key: value for key, value in step.items() if key in allowed
                })
                seen.add(f"{step['id']}:upgrade")
                print(f"  [升级] {was} → {old['type']}  {old['sel']}"
                      f"  to={old.get('to')}")
                return
            return
        if step["id"] in seen:
            return                                  # 双通道上报，按 id 去重
        seen.add(step["id"])
        steps.append(step)
        if source and step.get("inFrame"):
            # Binding callbacks cannot call sync Playwright APIs. Resolve the frame
            # element chain later from pump(), while keeping the same step object.
            pending_context.append((source, step))
        if source and step.get("type") in VISUAL_STEP_TYPES:
            action_t = step.get("actionT", step.get("t", 0))
            pre_frame = _select_source_pre_frame(frame_histories, source, action_t)
            pending_visual.append((source, step, len(steps), pre_frame))
        if step.get("secret"):
            val = " = <密码，未记录>"
        elif step.get("value") is not None:
            val = f" = {json.dumps(step['value'], ensure_ascii=False)}"
        else:
            val = ""
        print(f"  [录制] {step['type']:<6} {step['sel']}{val}")

    def capture_pending():
        # binding 回调内不能调用同步 Playwright API，否则会重入死锁。
        while pending_context:
            source, step = pending_context.pop(0)
            try:
                chain = _describe_frame_chain(source.get("frame"))
                if chain:
                    step["frameChain"] = chain
            except Exception:
                pass
        while pending_visual:
            source, step, index, pre_frame = pending_visual.pop(0)
            _capture_ui_template(source, step, asset_dir, index, pre_frame)

    chrome_bin = chrome_bin or resolve_chrome()
    if chrome_bin:
        print(f"浏览器: {chrome_bin.replace(str(Path.home()), '~')}")

    state_file = state_dir / "state.json"
    ss_file = state_dir / "session-storage.json"

    snapshot = {"state": None, "session": None, "session_origin": None}
    saved_origins = {}
    if state_file.exists():
        try:
            old_state = json.loads(state_file.read_text(encoding="utf-8"))
            saved_origins = {item["origin"]: item for item in old_state.get("origins", [])}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            executable_path=chrome_bin,
            args=["--ignore-certificate-errors", "--start-maximized"],
        )

        # no_viewport 让页面跟着真实窗口尺寸走。
        # 录制是 headed 的，锁死 viewport 会把页面渲染在一个固定尺寸里，与窗口不一致：
        # 底部的操作按钮（应用/保存/提交）可能被挤到可视区外，看起来像「按钮不见了」。
        # 回放时该由 conftest.py 决定 viewport，录制阶段不该替它做主。
        ctx_opts = {"ignore_https_errors": True, "no_viewport": True}
        if state_file.exists():
            ctx_opts["storage_state"] = str(state_file)
            print("已载入 cookies / localStorage")

        context = browser.new_context(**ctx_opts)

        # 步骤上报通道。必须在 add_init_script 之前建立，这样页面里 __recPush 一定存在。
        # 页面产生一步就立刻推过来，不等轮询 —— 否则「点完就跳转」的步骤
        # （登录按钮是最典型的）会随页面卸载一起消失。
        context.expose_binding("__recPush", lambda source, step: accept(step, source))

        context.add_init_script(script=recorder_source())

        # storage_state 不含 sessionStorage。有些站点把登录态放在 sessionStorage 里，
        # 那就必须在页面脚本执行**之前**注回去，否则 SPA 启动时读不到会立刻跳登录页。
        #
        # 注意 Python 的 add_init_script **没有 arg 参数**（JS 的 addInitScript(fn, arg)
        # 有），数据只能内联进脚本字符串，用 json.dumps 转义。
        if ss_file.exists():
            restore_context_session(context, state_dir)
            print("已载入 sessionStorage")

        def wanted(req) -> bool:
            if req.resource_type not in ("xhr", "fetch"):
                return False
            return api_filter in req.url if api_filter else True

        # 给每条请求编号，响应带上它 —— 生成器靠这个把响应和请求一一对应。
        # 不编号就只能按「响应之前最后一条同 URL 请求」猜，同一操作并发发两次
        # 相同请求时会让两个响应都关联到第二条。
        # WeakKeyDictionary 对应 JS 的 WeakMap：请求对象被回收后条目自动消失。
        request_ids: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        seq = {"n": 0}

        def on_request(r):
            if wanted(r):
                seq["n"] += 1
                request_ids[r] = seq["n"]
                event = {"id": seq["n"], "t": _now(), "phase": "req",
                         "method": r.method, "url": r.url}
                event.update(_request_body_fields(r))
                net.append(event)

        def on_response(r):
            if not wanted(r.request):
                return
            e = {"requestId": request_ids.get(r.request), "t": _now(),
                 "phase": "res", "method": r.request.method,
                 "url": r.url, "status": r.status}
            # 失败响应和写操作的响应体一定要留 —— 排查 4xx/5xx 时这是唯一有用的信息。
            # 成功的 GET 响应体可能有几十上百 KB，全存没有价值。
            # 必须当场取：攒到最后再取，Chromium 早把 body 从网络缓存里淘汰了。
            if r.status >= 400 or r.request.method != "GET":
                try:
                    e["body"] = redact_text(r.text())[:2000]
                except Exception:
                    e["body"] = None
            net.append(e)

        closed = {"v": False}
        listened_pages = set()

        def on_page_closed(_):
            try:
                closed["v"] = not any(not item.is_closed() for item in context.pages)
            except Exception:
                pass

        def listen_page(target):
            if target in listened_pages:
                return
            listened_pages.add(target)
            frame_histories.setdefault(target, [])
            target.on("request", on_request)
            target.on("response", on_response)
            target.on("close", on_page_closed)

        # Popups and target=_blank pages are part of the same recording session.
        # Their traffic and screenshots must stay attached to that page.
        context.on("page", listen_page)
        page = context.new_page()
        listen_page(page)
        browser.on("disconnected", lambda _: closed.update(v=True))

        print(f"\n打开 {start_url}")
        if on_ready is None:
            print("需要登录就在这个窗口里登录（密码不会被记录）。")
            print("操作完成后直接关闭浏览器窗口，脚本自动生成。\n")

        try:
            page.goto(start_url, wait_until="domcontentloaded")
        except Exception as e:
            print(f"打开失败: {str(e).splitlines()[0]}")

        def take_snapshot():
            """趁页面还活着，把登录态抓进内存。"""
            alive = [item for item in context.pages if not item.is_closed()]
            if not alive:
                return
            active = alive[-1]
            try:
                snapshot["state"] = _capture_storage(context, active, saved_origins)
            except Exception:
                return
            try:
                ss = active.evaluate("() => JSON.stringify(sessionStorage)")
                snapshot["session"] = ss if ss and ss != "{}" else None
                parsed_active = urlsplit(active.url)
                snapshot["session_origin"] = (
                    f"{parsed_active.scheme}://{parsed_active.netloc}"
                    if parsed_active.scheme and parsed_active.netloc else None
                )
            except Exception:
                pass

        def alive_pages():
            return [item for item in context.pages if not item.is_closed()]

        def drain_all_pages():
            for target in alive_pages():
                for frame in target.frames:
                    try:
                        for item in frame.evaluate(DRAIN):
                            accept(item, {"page": target, "frame": frame})
                    except Exception:
                        pass

        def refresh_pre_frames():
            """按 page 保留短时 viewport 历史，popup 不能借用 opener 的帧。"""
            for target in alive_pages():
                try:
                    frame = {"data": target.screenshot(), "t": _now()}
                    history = frame_histories.setdefault(target, [])
                    history.append(frame)
                    cutoff = frame["t"] - PRE_FRAME_MAX_AGE_MS
                    history[:] = [item for item in history if item["t"] >= cutoff]
                except Exception:
                    pass

        refresh_pre_frames()

        # 兜底轮询：捞走 binding 未能送达的步骤（去重由 accept 负责）。
        # JS 版用 setInterval 与 waitForEvent 并发；Python sync API 是单线程的，
        # 只能把两件事合进一个循环 —— wait_for_timeout 本身会驱动事件分发，
        # 所以 binding 回调照常触发。
        def pump():
            """把下面那条轮询循环干的活暴露给驱动方。

            人工路径靠循环持续刷新 viewport 前帧；脚本驱动路径下循环还没开始，
            整个操作期间一帧都不会刷新。而 switch / check 这类延迟上报的动作
            **只能**用动作前的帧做模板（用动作后的帧会描述成目标状态），
            于是脚本录出来的轨迹里每个开关都缺模板、整条轨迹 incomplete。
            驱动方在每次操作前调一下，两条路径的行为就一致了。
            """
            drain_all_pages()
            capture_pending()
            refresh_pre_frames()

        if on_ready is not None:
            # 测试路径：回调驱动页面，返回即视为「操作完毕」，由这里收尾 ——
            # 拍最后一张快照再关页面。人工路径下这一步由用户关窗口触发，
            # 收尾快照则由下面循环里的每周期快照承担。
            try:
                if len(inspect.signature(on_ready).parameters) >= 2:
                    on_ready(page, pump)
                else:
                    on_ready(page)
            except Exception as e:
                print(f"on_ready 抛出: {e}")
            capture_pending()
            take_snapshot()
            for target in alive_pages():
                target.close()

        while not closed["v"]:
            drain_all_pages()
            capture_pending()
            take_snapshot()
            refresh_pre_frames()

            try:
                current = alive_pages()
                if not current:
                    break
                current[0].wait_for_timeout(PUMP_MS)
            except Exception:
                break                               # 浏览器已关

        drain_all_pages()

        capture_pending()
        take_snapshot()                             # 还开着就抓最新的一份
        try:
            browser.close()
        except Exception:
            pass

    # 步骤可能来自两个通道，顺序不保证，按时间排好再输出
    steps.sort(key=lambda s: s["t"])

    # 落盘登录态，下次录制免登录
    if snapshot["state"]:
        write_session_snapshot(
            state_dir, snapshot["state"], snapshot["session"],
            session_origin=snapshot["session_origin"],
        )
        write_session_snapshot(
            artifacts["auth_dir"], snapshot["state"], snapshot["session"],
            session_origin=snapshot["session_origin"],
        )

    # ---------- 输出 ----------
    case_dir.mkdir(parents=True, exist_ok=True)
    raw_file = artifacts["raw_file"]
    raw_file.write_text(json.dumps(
        {"startUrl": start_url, "recordedAt": datetime.now().isoformat(),
         "steps": steps, "net": net},
        ensure_ascii=False, indent=1), encoding="utf-8")

    trace_file = artifacts["trace_file"]
    trace_file.write_text(json.dumps(
        generate_trace(steps, net, name=name, start_url=start_url),
        ensure_ascii=False, indent=1), encoding="utf-8")

    spec_text = generate_spec(steps, net, start_url=start_url, name=name)
    # pytest 只收集 test_*.py，文件名必须带前缀，而且得是合法模块名
    spec_file = artifacts["spec_file"]
    spec_file.write_text(spec_text, encoding="utf-8")

    # ---------- 小结 ----------
    def strip(u):
        return u.replace(origin, "")

    responses = [n for n in net if n["phase"] == "res"]
    writes = [n for n in net if n["phase"] == "req" and n["method"] != "GET"]
    failed = [n for n in responses if n["status"] >= 400]
    amb = [s for s in steps if s.get("ambiguous")]
    css = [s for s in steps if s.get("kind") == "css"]

    print("\n录制完成")
    print(f"  操作 {len(steps)} 步 · 接口 {len(responses)} 次 · 写请求 {len(writes)} 次")
    print(f"  原始记录  {raw_file}")
    print(f"  成功轨迹  {trace_file}")
    print(f"  脚本草稿  {spec_file}")
    if snapshot["state"]:
        print(f"  登录态    {state_file}")
        print(f"  回放会话  {artifacts['auth_dir']}")
    if failed:
        print(f"\n  ⚠ {len(failed)} 个请求失败：")
        for f in failed[:5]:
            print(f"      {f['method']} {strip(f['url']).split('?')[0]} -> {f['status']}")
    if amb:
        print(f"  ⚠ {len(amb)} 个选择器有歧义，草稿里已标出")
    if css:
        print(f"  ⚠ {len(css)} 个只能用 CSS 兜底，已包成「存在则点」")

    return {"steps": steps, "net": net, "case_dir": case_dir,
            "raw_file": raw_file,
            "trace_file": trace_file, "spec_file": spec_file,
            "state_file": state_file if snapshot["state"] else None}


def _now() -> int:
    return int(time.time() * 1000)


if __name__ == "__main__":
    sys.exit(main())
