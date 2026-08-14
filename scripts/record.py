#!/usr/bin/env python3
"""web-record —— 把网页操作录成 pytest 脚本，并关联触发的接口（record.mjs 的 Python 版）。

启动一个 Playwright 控制的浏览器，你在里面正常操作，脚本负责：
  - 记录每一次点击 / 输入 / 勾选 / 回车，为元素算出最稳的选择器
  - 从驱动侧抓所有 XHR/fetch（含请求体、状态码、失败响应体）
  - 按时间把接口调用挂到触发它的那一步下面
  - 结束时输出原始 JSON + 一份可直接跑的 pytest 用例草稿

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
import json
import re
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
from rec_config import ConfigError, load_config, with_defaults  # noqa: E402
from recorder_loader import recorder_source                 # noqa: E402

DRAIN = "() => (window.__rec ? window.__rec.drain() : [])"
PUMP_MS = 800
# 登录态快照。JS 版是在浏览器关闭之后才存，而用法写的是「操作完成后直接
# 关闭浏览器窗口」—— 那时 context 已经没了，storage_state() 必然抛
# "Target page, context or browser has been closed"，登录态其实从来没存下来
# （JS 代码自己的 catch 注释也承认了「页面已关就取不到了」）。
#
# 只能趁页面还活着的时候拍。每个轮询周期都拍一次，这样「用户关窗口」最多
# 丢掉最后 800ms 内的变化 —— 而登录态在最后 800ms 内变化的可能性可以忽略。
# 成本是每 800ms 一次本地 CDP 往返，可以忽略。


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
    """
    name = name or "session-" + datetime.now().isoformat(
        timespec="seconds").replace(":", "-")
    out_dir = Path(out_dir).resolve()
    state_dir = Path(state_dir).resolve()
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(start_url))

    steps, seen, net = [], set(), []

    def accept(step):
        if not step or not step.get("id") or step["id"] in seen:
            return                                  # 双通道上报，按 id 去重
        seen.add(step["id"])
        steps.append(step)
        if step.get("secret"):
            val = " = <密码，未记录>"
        elif step.get("value") is not None:
            val = f" = {json.dumps(step['value'], ensure_ascii=False)}"
        else:
            val = ""
        print(f"  [录制] {step['type']:<6} {step['sel']}{val}")

    chrome_bin = chrome_bin or resolve_chrome()
    if chrome_bin:
        print(f"浏览器: {chrome_bin.replace(str(Path.home()), '~')}")

    state_file = state_dir / "state.json"
    ss_file = state_dir / "session-storage.json"

    snapshot = {"state": None, "session": None}

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
        context.expose_binding("__recPush", lambda source, step: accept(step))

        context.add_init_script(script=recorder_source())

        # storage_state 不含 sessionStorage。有些站点把登录态放在 sessionStorage 里，
        # 那就必须在页面脚本执行**之前**注回去，否则 SPA 启动时读不到会立刻跳登录页。
        #
        # 注意 Python 的 add_init_script **没有 arg 参数**（JS 的 addInitScript(fn, arg)
        # 有），数据只能内联进脚本字符串，用 json.dumps 转义。
        if ss_file.exists():
            raw = ss_file.read_text(encoding="utf-8")
            context.add_init_script(script="""(() => {
              try {
                for (const [k, v] of Object.entries(JSON.parse(%s))) {
                  try { sessionStorage.setItem(k, v); } catch { /* 只读键或超配额 */ }
                }
              } catch { /* 文件损坏就算了 */ }
            })()""" % json.dumps(raw))
            print("已载入 sessionStorage")

        page = context.new_page()

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
                net.append({"id": seq["n"], "t": _now(), "phase": "req",
                            "method": r.method, "url": r.url, "body": r.post_data})

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
                    e["body"] = r.text()[:2000]
                except Exception:
                    e["body"] = None
            net.append(e)

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"\n打开 {start_url}")
        if on_ready is None:
            print("需要登录就在这个窗口里登录（密码不会被记录）。")
            print("操作完成后直接关闭浏览器窗口，脚本自动生成。\n")

        try:
            page.goto(start_url, wait_until="domcontentloaded")
        except Exception as e:
            print(f"打开失败: {str(e).splitlines()[0]}")

        closed = {"v": False}
        page.on("close", lambda _: closed.update(v=True))
        browser.on("disconnected", lambda _: closed.update(v=True))

        def take_snapshot():
            """趁页面还活着，把登录态抓进内存。"""
            try:
                snapshot["state"] = context.storage_state()
            except Exception:
                return
            try:
                ss = page.evaluate("() => JSON.stringify(sessionStorage)")
                if ss and ss != "{}":
                    snapshot["session"] = ss
            except Exception:
                pass

        # 兜底轮询：捞走 binding 未能送达的步骤（去重由 accept 负责）。
        # JS 版用 setInterval 与 waitForEvent 并发；Python sync API 是单线程的，
        # 只能把两件事合进一个循环 —— wait_for_timeout 本身会驱动事件分发，
        # 所以 binding 回调照常触发。
        if on_ready is not None:
            # 测试路径：回调驱动页面，返回即视为「操作完毕」，由这里收尾 ——
            # 拍最后一张快照再关页面。人工路径下这一步由用户关窗口触发，
            # 收尾快照则由下面循环里的每周期快照承担。
            try:
                on_ready(page)
            except Exception as e:
                print(f"on_ready 抛出: {e}")
            take_snapshot()
            if not page.is_closed():
                page.close()

        while not closed["v"]:
            try:
                for s in page.evaluate(DRAIN):
                    accept(s)
            except Exception:
                pass                                # 导航中，下次再取

            take_snapshot()

            try:
                page.wait_for_timeout(PUMP_MS)
            except Exception:
                break                               # 浏览器已关

        try:
            for s in page.evaluate(DRAIN):
                accept(s)
        except Exception:
            pass                                    # 页面已关

        take_snapshot()                             # 还开着就抓最新的一份
        try:
            browser.close()
        except Exception:
            pass

    # 步骤可能来自两个通道，顺序不保证，按时间排好再输出
    steps.sort(key=lambda s: s["t"])

    # 落盘登录态，下次录制免登录
    if snapshot["state"]:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(snapshot["state"], ensure_ascii=False, indent=1), encoding="utf-8")
        if snapshot["session"]:
            ss_file.write_text(snapshot["session"], encoding="utf-8")

    # ---------- 输出 ----------
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_file = out_dir / f"{name}.json"
    raw_file.write_text(json.dumps(
        {"startUrl": start_url, "recordedAt": datetime.now().isoformat(),
         "steps": steps, "net": net},
        ensure_ascii=False, indent=1), encoding="utf-8")

    spec_text = generate_spec(steps, net, start_url=start_url, name=name)
    # pytest 只收集 test_*.py，文件名必须带前缀，而且得是合法模块名
    spec_file = out_dir / f"test_{_ident(name)}.py"
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
    print(f"  脚本草稿  {spec_file}")
    if snapshot["state"]:
        print(f"  登录态    {state_file}")
    if failed:
        print(f"\n  ⚠ {len(failed)} 个请求失败：")
        for f in failed[:5]:
            print(f"      {f['method']} {strip(f['url']).split('?')[0]} -> {f['status']}")
    if amb:
        print(f"  ⚠ {len(amb)} 个选择器有歧义，草稿里已标出")
    if css:
        print(f"  ⚠ {len(css)} 个只能用 CSS 兜底，已包成「存在则点」")

    return {"steps": steps, "net": net, "raw_file": raw_file, "spec_file": spec_file,
            "state_file": state_file if snapshot["state"] else None}


def _now() -> int:
    return int(time.time() * 1000)


if __name__ == "__main__":
    sys.exit(main())
