"""驱动一遍带全部边界情况的页面，把录制结果交出来（fixture-drive.mjs 的 Python 版）。

页面 HTML 就在本文件里。迁移期间它是从 fixture-drive.mjs 抠出来的，
两边共用同一份才谈得上「Python 侧和 Node 侧录出来的东西一致」；
JS 侧退役后已内联，这里是唯一副本。
"""

import threading
import weakref
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from recorder_loader import recorder_source

HTML = """<!doctype html><meta charset="utf-8"><body>
  <button data-testid="save-btn">保存</button>
  <button id="submit_9" onclick="sendOrder()">提交订单</button>
  <button onclick="fetch('/api/bad',{method:'POST',body:'{}'})">触发失败</button>
  <div id="synthetic_change">轮播内容</div>
  <input placeholder="请输入用户名">
  <input type="password" placeholder="密码">
  <label><input type="checkbox" id="agree"> 同意条款</label>
  <table>
    <tr><td>张三</td><td><span class="op">删除</span></td></tr>
    <tr><td>李四</td><td><span class="op">删除</span></td></tr>
    <tr><td>王五</td><td><span class="op">删除</span></td></tr>
  </table>
  <div id="tip_box_10059"><span class="close_x">×</span></div>
  <a id="go" href="/next">立刻跳转</a>
  <iframe src="/login_frame.html" width="300" height="120"></iframe>

  <!-- 标准开关：有 role 和 aria-checked -->
  <div id="sw1" role="switch" aria-checked="false" tabindex="0">自保护</div>

  <!-- 自研开关：只有 class，没有 aria -->
  <div id="sw2" class="ui-switch off"><span class="knob"></span></div>

  <!-- 整行可点，开关是点击目标的「后代」而不是祖先；状态写在内层 class 上 -->
  <div id="row_sp" class="labelAndItem" style="padding:24px">
    <span>行内自保护</span>
    <div class="eui_toggle"><div class="eui_toggle_container"><i class="eui_toggle_thumb"></i></div></div>
  </div>

  <!-- 页内提示条：不是浮层（没有 fixed/absolute + 高 z-index），但同样是
       「出现与否取决于账号状态」的条件元素。实测就是这种形态被漏判成必经步骤。 -->
  <!-- 提示条挂在一个绝对定位的头部容器里 —— 往上撞到的第一个「浮层」是头部，
       而头部不会消失。观察错了层，就永远等不到「关掉了」。 -->
  <div style="position:relative;height:80px">
  <div class="app_header" style="position:absolute;z-index:1000;left:0;right:0">
  <div class="eui_tipBoxStyle">
    <div class="eui_tipBoxOuterStyle">
      <span>系统检测到您未绑定手机号码和电子邮箱，为保证您的账号安全、便于在异常登录时及时通知到您，请尽快前往个人中心完成绑定</span>
      <span class="tipBox_close" style="display:inline-block;width:14px;height:14px;background:#777"></span>
    </div>
  </div>
  </div>
  </div>

  <!-- 反例：删除某一行的图标。点完那一行同样消失 —— 但这是破坏性操作，
       绝不能因此被标成「可选」。 -->
  <div class="list_row"><span>待删除的资产</span>
    <span class="row_del" style="display:inline-block;width:14px;height:14px;background:#a33"></span>
  </div>

  <!-- 叠着两层弹窗，各自一个关闭叉，正文都上百字。图标没文本没 role，
       只靠 CSS 绝对路径会得到一串 nth-of-type —— 弹窗层级一变就失效。
       实测同一个关闭叉两次录制分别录成 div:nth-of-type(8) 和 (9)。 -->
  <div class="dlg_panel" style="position:fixed;right:8px;bottom:8px;z-index:1000;width:180px;background:#eee">
    <span class="dlg_close" style="display:inline-block;width:16px;height:16px;background:#999"></span>
    <div>卸载校验码 1. 本校验码用于本地卸载客户端，请在有效期内使用，过期后需重新获取；
      2. 每个校验码仅可使用一次；3. 如需批量卸载，请联系管理员开通批量通道。</div>
  </div>
  <div class="dlg_panel" style="position:fixed;right:8px;bottom:8px;z-index:1000;width:180px;background:#eee">
    <span class="dlg_close" style="display:inline-block;width:16px;height:16px;background:#999"></span>
    <div>校验码历史记录 仅保留近 10 条校验码，无可使用校验码时，可前往策略页重新申请；
      历史记录不支持导出，如需留档请自行截图保存。</div>
  </div>

  <!-- 点在滑块上：滑块和轨道都带 toggle 字样，但状态只写在容器那一层。
       撞到的第一层是滑块，它永远读不出状态 —— 那样整步会退化成盲点击。 -->
  <div id="row_thumb_on" class="labelAndItem" style="padding:24px">
    <span>已开启滑块</span>
    <div class="eui_toggle"><div class="eui_toggle_container toggled">
      <i class="eui_toggle_track" style="display:inline-block;width:40px;height:20px;background:#ccc"></i><i class="eui_toggle_thumb" style="display:inline-block;width:18px;height:18px;background:#888"></i>
    </div></div>
  </div>

  <!-- 同样点滑块，但容器当前一个状态标记都没有：静态读不出「关」还是
       「这层不带状态」，只能看拨完之后哪一层的 class 变了。 -->
  <div id="row_thumb_off" class="labelAndItem" style="padding:24px">
    <span>待开启滑块</span>
    <div class="eui_toggle"><div class="eui_toggle_container">
      <i class="eui_toggle_track" style="display:inline-block;width:40px;height:20px;background:#ccc"></i><i class="eui_toggle_thumb" style="display:inline-block;width:18px;height:18px;background:#888"></i>
    </div></div>
  </div>

  <!-- 二次确认型开关：点了先弹确认，class 要等点「确认」之后才变。
       原来只等 1.2 秒，等不到就退回盲点击 —— 回放时可能朝反方向拨。 -->
  <div id="row_confirm" class="labelAndItem" style="padding:24px">
    <span>需确认自保护</span>
    <div class="eui_toggle"><div class="eui_toggle_container"><i class="eui_toggle_thumb"></i></div></div>
  </div>
  <button id="confirm_btn" style="display:none">确认</button>

  <!-- 慢开关：拨动后 500ms 才更新 class。固定等 60ms 的话检测不到变化，
       会退回盲点击 —— 回放时方向取决于当时的初始状态，而且不报错 -->
  <div id="row_slow" class="labelAndItem" style="padding:24px">
    <span>延迟自保护</span>
    <div class="eui_toggle"><div class="eui_toggle_container"><i class="eui_toggle_thumb"></i></div></div>
  </div>

  <!-- 整行可点：点行会展开（加一个子节点），点行内的 span 只高亮。
       录到的是行，若生成 getByText 就会解析到 span —— 回放时展不开，
       而且那一步还报成功。 -->
  <div id="row_expand" class="tree_row" style="padding:10px;border:1px solid #ccc">
    <span class="row_hit" style="cursor:pointer;display:inline-block;width:14px;height:14px;background:#888"></span>
    <span class="tree_label">分组甲</span>
  </div>
  <div id="expanded_marker"></div>

  <!-- 两棵结构完全相同的深子树：cssPath 只取最近 6 层，截断后两条路径一模一样。
       图标无文字、无 role，必然落到 CSS 兜底 —— 正是最容易撞车的那条路。 -->
  <div id="tree_a"><div><div><div><div><div><span class="ic_x" style="display:inline-block;width:16px;height:16px;background:#333"></span></div></div></div></div></div>
  <div id="tree_b"><div><div><div><div><div><span class="ic_x" style="display:inline-block;width:16px;height:16px;background:#333"></span></div></div></div></div></div>

  <!-- 两个叠着的弹窗，各自都 id="dialog_panel"（HTML 上不合法，现实里就这么写）。
       遇到 id 就短路的话，产出的 CSS 路径命中 2 个元素，回放必然 strict mode 报错。 -->
  <div id="dialog_panel"><span class="dlg_close" style="display:inline-block;width:16px;height:16px;background:#999"></span></div>
  <div id="dialog_panel"><span class="dlg_close" style="display:inline-block;width:16px;height:16px;background:#999"></span></div>

  <!-- 组件框架批量吐出的同名 testid：不唯一，用它回放必然 strict mode 失败 -->
  <div data-testid="text-comp-span">重复标记甲</div>
  <div data-testid="text-comp-span">重复标记乙</div>

  <!-- 只有 data-cy：Playwright 默认的 testIdAttribute 是 data-testid，认不到 -->
  <button data-cy="cy-only">仅有 data-cy</button>

  <!-- 两个 placeholder 完全相同的输入框：真的那个有 id，另一个是诱饵。
       录制时点哪个都能跑通，回放必然 strict mode 报错。 -->
  <div id="real_box"><input id="real_user" placeholder="账号/手机号"></div>
  <div id="decoy_box"><input placeholder="账号/手机号" autocomplete="on"></div>

  <!-- 时间显示在 value 上的输入框：innerText 恒为空，读错了断言永远不通过。
       值是页面自己按当天算的 —— 正是「可反复运行」的那种目标。 -->
  <input id="today_box" readonly>
  <!-- 结构上读不出文本：canvas 画的图表。时间断言加在它上面永远失败，
       而录制、生成、回放四段都会显得正常，最后以 actual='' 收场。 -->
  <canvas id="chart" role="img" aria-label="逐小时折线图" width="80" height="40"></canvas>

  <!-- 日期筛选框：填死值的脚本不会报错，只会悄悄查错区间 ——
       录制那天填的「N 天前」，下个月回放就成了「N+30 天前」。 -->
  <input id="date_from" placeholder="开始日期">

  <!-- 侧栏：分组标题「统计」被好几层 div 包着。锚文本本身全页唯一，
       但 hasText 是按子树文本匹配的 —— 每一层祖先都命中。
       只验锚唯一、不验作用域唯一的话，产出的 locator("div", {hasText:"统计"})
       会撞一整条祖先链，回放 strict mode 直接报错。 -->
  <div class="shell"><div class="side"><div class="nav">
    <div class="nav-group">统计</div>
    <div class="nav-item"><span>用量概览</span></div>
  </div></div></div>
  <div class="page-title">用量概览</div>

  <!-- 时间字段：同一行里既有稳定的名字，也有每次都变的时间戳。
       右键那个时间单元格时，selectorFor 只会算出 getByText("<那串时间>") ——
       而它一刷新元素就没了，断言会以「找不到元素」失败。所以要换成
       「稳定的行 + 第几列」。这张表就是那个形状。 -->
  <table id="key_table">
    <tr><td>maa-fw</td><td>sk-abc</td><td>2026-08-12 09:58:40</td>
        <td id="last_used">2026-08-18 20:33:47</td></tr>
  </table>

  <!-- 触发器显示的值，和下面浮层里的选项文本一模一样 -->
  <div id="trigger">Windows系统</div>
  <div id="pop" role="listbox" style="position:absolute;z-index:999;display:none">
    <div class="opt">Windows系统</div><div class="opt">Linux系统</div>
  </div>

  <script>
    // 请求体里混着三类值：稳定的、字符串型雪花 ID、数字型毫秒时间戳。
    // 后两类每次运行都不同，钉进断言就等于让用例必挂。
    function sendOrder() {
      fetch('/api/ok', { method: 'POST', body: JSON.stringify({
        a: 1, pageSize: 100, id: '39049753287328', endTime: Date.now(),
      }) });
    }
    (() => { const d=new Date(), p2=n=>String(n).padStart(2,'0');
      document.getElementById('today_box').value =
        `${d.getFullYear()}-${p2(d.getMonth()+1)}-${p2(d.getDate())}`; })();
    sw1.addEventListener('click', () => sw1.setAttribute('aria-checked',
      sw1.getAttribute('aria-checked') === 'true' ? 'false' : 'true'));
    sw2.addEventListener('click', () => sw2.classList.toggle('off') || sw2.classList.toggle('on'));
    trigger.addEventListener('click', () => { pop.style.display = 'block'; });
    row_sp.addEventListener('click', () =>
      row_sp.querySelector('.eui_toggle_container').classList.toggle('toggled'));
    row_expand.addEventListener('click', (ev) => {
      // 只有点在行上才展开；点里面的标签只高亮 —— 和真实资产树一致
      if (ev.target.classList.contains('tree_label')) { ev.stopPropagation(); return; }
      expanded_marker.textContent = '终端甲';
    });
    // 点滑块，状态写在容器上；「待开启」那个还要过 300ms 才写
    for (const [row, delay] of [[row_thumb_on, 0], [row_thumb_off, 300]]) {
      row.querySelector('.eui_toggle_thumb').addEventListener('click', () => setTimeout(
        () => row.querySelector('.eui_toggle_container').classList.toggle('toggled'), delay));
    }
    document.querySelector('.tipBox_close').addEventListener('click', (ev) => {
      ev.currentTarget.closest('.eui_tipBoxStyle').remove();
    });
    document.querySelector('.row_del').addEventListener('click', (ev) => {
      ev.currentTarget.closest('.list_row').remove();      // 整行删掉
    });
    // 关闭叉收掉所有弹窗层 —— 和真实应用一致（点第一层会一起关）
    for (const x of document.querySelectorAll('.dlg_close')) {
      if (!x.closest('.dlg_panel')) continue;
      x.addEventListener('click', () => {
        for (const p of document.querySelectorAll('.dlg_panel')) p.style.display = 'none';
      });
    }
    row_confirm.addEventListener('click', () => { confirm_btn.style.display = 'inline-block'; });
    confirm_btn.addEventListener('click', () => {
      row_confirm.querySelector('.eui_toggle_container').classList.toggle('toggled');
      confirm_btn.style.display = 'none';
    });
    row_slow.addEventListener('click', () => setTimeout(() =>
      row_slow.querySelector('.eui_toggle_container').classList.toggle('toggled'), 500));
  </script>
</body>"""

FRAME = """<!doctype html><meta charset="utf-8"><body>
  <input placeholder="iframe内用户名">
  <button>iframe内登录</button></body>"""

NEXT = """<!doctype html><meta charset="utf-8"><body><h1>第二页</h1>
  <button data-testid="after-nav">跳转后的按钮</button></body>"""

REPO = Path(__file__).resolve().parent.parent
RECORDER_MJS = REPO / "scripts/recorder-inject.mjs"


def _pages():
    return {"/": HTML, "/login_frame.html": FRAME, "/next": NEXT}


def _serve(pages):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _404(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self.path in pages:
                return self._send(200, "text/html; charset=utf-8", pages[self.path])
            self._404()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/api/ok":
                return self._send(200, "application/json", '{"code":"200"}')
            if self.path == "/api/bad":
                return self._send(400, "application/json",
                                  '{"error":"subnetIdList 不能为空"}')
            self._404()

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def drive(chrome: str | None = None) -> dict:
    srv = _serve(_pages())
    base = f"http://127.0.0.1:{srv.server_port}"

    steps, seen, net = [], set(), []

    def accept(st):
        if not st or not st.get("id"):
            return
        # 和 record.py 一致：升级记录按 id 覆盖，不能当重复上报丢掉
        # 升级也走双通道，两份内容一样，应用一次就够（第二次只是噪音）
        if st.get("_upgrade") and f"{st['id']}:upgrade" in seen:
            return
        if st.get("_upgrade"):
            for old_step in steps:
                if old_step["id"] == st["id"]:
                    # 和 record.py 一致：原地只改语义字段，视觉字段保留点击那一刻的
                    # 和 record.py 一致：_only 指明只改哪几个字段
                    allowed = st.get("_only") or (
                        "type", "to", "via", "sel", "kind",
                        "ambiguous", "matches", "label", "css", "dismissesOverlay",
                    )
                    old_step.update({k: v for k, v in st.items() if k in allowed})
                    seen.add(f"{st['id']}:upgrade")
                    return
            return
        if st["id"] not in seen:
            seen.add(st["id"])
            steps.append(st)
            # 真实驱动是**事后**把模板写进这个字典对象的（截图不能在 binding
            # 回调里做）。这里照同样的时序放一个标记：升级如果换掉了字典，
            # 标记就成了孤儿 —— 那正是模板丢失、轨迹变 incomplete 的原因。
            if st.get("type") in ("click", "dblclick", "switch", "check", "uncheck"):
                st.setdefault("ui", {})["templates"] = {"element": f"{st['id']}.png"}

    try:
        with sync_playwright() as p:
            launch = {"headless": True}
            if chrome:
                launch["executable_path"] = chrome
            browser = p.chromium.launch(**launch)
            ctx = browser.new_context()

            # 主通道必须在 add_init_script 之前建立，这样页面里 __recPush 一定存在
            ctx.expose_binding("__recPush", lambda source, st: accept(st))
            ctx.add_init_script(script=recorder_source(RECORDER_MJS))

            page = ctx.new_page()

            # 给请求编号、响应带上它 —— 与 record.py 一致，让生成器走 id 配对路径
            request_ids = weakref.WeakKeyDictionary()
            seq = {"n": 0}

            def on_request(r):
                if r.resource_type in ("xhr", "fetch"):
                    seq["n"] += 1
                    request_ids[r] = seq["n"]
                    net.append({"id": seq["n"], "t": _now(), "phase": "req",
                                "method": r.method, "url": r.url,
                                "body": r.post_data})

            def on_response(r):
                if r.request.resource_type not in ("xhr", "fetch"):
                    return
                e = {"requestId": request_ids.get(r.request), "t": _now(),
                     "phase": "res", "method": r.request.method,
                     "url": r.url, "status": r.status}
                # 响应体必须**当场**取。攒到最后再取会拿到
                # `Network.getResponseBody: No resource with given identifier found`——
                # 中间的导航和后续流量会让 Chromium 把 body 从网络缓存里淘汰。
                # sync API 在事件回调里同步调 r.text() 是安全的（已实测无重入问题）。
                if r.status >= 400 or r.request.method != "GET":
                    try:
                        e["body"] = r.text()[:2000]
                    except Exception:
                        e["body"] = None
                net.append(e)

            page.on("request", on_request)
            page.on("response", on_response)

            page.goto(base)
            page.locator("#synthetic_change").dispatch_event("change")
            page.click("[data-testid=save-btn]")
            page.fill('input[placeholder="请输入用户名"]', "alice")
            page.fill("input[type=password]", "sup3rs3cret")
            page.check("#agree")
            page.click("#submit_9")
            page.wait_for_timeout(300)
            page.click("text=触发失败")
            page.wait_for_timeout(400)
            page.locator("tr", has_text="李四").locator(".op").click()
            page.click("#tip_box_10059 .close_x")
            page.wait_for_timeout(300)

            # iframe 内的操作：回放时必须 frameLocator 进去
            page.frame_locator("iframe").get_by_placeholder("iframe内用户名").fill("frame-user")
            page.wait_for_timeout(200)

            # 点完立刻跳转：步骤只存在页面内数组的话，会随页面卸载一起消失
            with page.expect_navigation(url="**/next"):
                page.click("#go")
            page.click("[data-testid=after-nav]")
            page.wait_for_timeout(300)

            # ── 开关与浮层 ──
            page.goto(base)
            page.wait_for_timeout(500)
            page.click("#sw1")                       # aria 开关：false → true
            page.wait_for_timeout(300)
            page.click("#trigger")                   # 打开浮层
            page.wait_for_timeout(300)
            page.locator("#pop .opt", has_text="Windows系统").click()   # 与触发器同名
            page.wait_for_timeout(400)

            # 整行可点的开关：点在行的 padding 上，开关是这一下的后代
            page.click("#row_sp", position={"x": 5, "y": 5})
            page.wait_for_timeout(400)

            # 打字 + 回车：值在 change 才记，按键当场记 → 录出来是「先回车后填值」
            page.click('input[placeholder="请输入用户名"]')
            page.fill('input[placeholder="请输入用户名"]', "bob")
            page.press('input[placeholder="请输入用户名"]', "Enter")
            page.wait_for_timeout(300)

            # 点在页面空白处：会一路上溯到 html/body，回放时点了等于没点
            page.mouse.click(2, 2)
            page.wait_for_timeout(300)

            page.locator("[data-testid=text-comp-span]").first.click()
            page.wait_for_timeout(300)
            page.click("[data-cy=cy-only]")
            page.wait_for_timeout(300)

            # 展开箭头：没有文本、没有 role，但它是独立控件。
            # 点它和点外面那一行是两件事 —— 录制器必须抓住它本身。
            page.click("#row_expand .row_hit")
            page.wait_for_timeout(300)

            # 深子树里的无文字图标：两条 CSS 路径截断后相同，必然撞车
            page.locator("#tree_b .ic_x").click()
            page.wait_for_timeout(300)

            # 重复 id 的弹窗：点第二个的关闭图标
            page.locator("#dialog_panel").nth(1).locator(".dlg_close").click()
            page.wait_for_timeout(300)

            # 页内提示条的关闭图标
            page.click(".tipBox_close")
            page.wait_for_timeout(400)

            # 反例：删掉一行 —— 行也消失了，但这不是「关提示」
            page.click(".row_del")
            page.wait_for_timeout(400)

            # 长文本弹窗里的关闭叉
            page.locator(".dlg_panel").first.locator(".dlg_close").click()
            page.wait_for_timeout(200)

            # 点滑块：状态在祖先容器上，不在滑块自己身上
            page.click("#row_thumb_on .eui_toggle_thumb")
            page.wait_for_timeout(200)
            page.click("#row_thumb_off .eui_toggle_thumb")
            page.wait_for_timeout(700)

            # 二次确认型开关：拨完要过好几秒才点确认，class 那时才变
            page.click("#row_confirm", position={"x": 5, "y": 5})
            page.wait_for_timeout(2500)          # 比原来的 1.2 秒检测窗口更久
            page.click("#confirm_btn")
            page.wait_for_timeout(800)

            # 日期筛选：填一个「3 天前」的日期，看录制器记不记得住这个相对关系
            from datetime import datetime, timedelta
            three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            page.fill("#date_from", three_days_ago)
            page.locator("#date_from").blur()
            page.wait_for_timeout(300)

            # 点侧栏里的 span：作用域会往上找到包着「统计」的那一层。
            # 锚唯一但祖先链全命中 —— 作用域必须自证唯一才行。
            page.locator("div.nav-item span", has_text="用量概览").click()
            page.wait_for_timeout(300)

            # 慢开关：class 要 500ms 后才变。固定等 60ms 检测不到变化，
            # 会退回盲点击 —— 回放时方向取决于当时的初始状态，而且不报错
            page.click("#row_slow", position={"x": 5, "y": 5})
            page.wait_for_timeout(1500)

            # 同 placeholder 的两个输入框，填真的那个。
            # 必须失焦：fill 的值是在 change 事件里记的，不失焦就不会产生步骤。
            page.fill("#real_user", "zhangsan")
            page.locator("#real_user").blur()
            page.wait_for_timeout(300)

            # ── 断言菜单：右键 → 改 expected → 提交 ──
            page.goto(base)
            page.wait_for_timeout(500)

            def sh(sel):
                return page.locator("#__rec_assert_menu__").locator(sel)

            # 1) 文本断言，用户把默认值改掉
            page.locator("[data-testid=save-btn]").click(button="right")
            page.wait_for_timeout(300)
            sh("#es").fill("用户确认过的值")
            sh("#ok").click()
            page.wait_for_timeout(300)

            # 1.5) 时间断言：期望值不由录制决定，由回放此刻的时钟决定
            page.locator("#last_used").click(button="right")
            page.wait_for_timeout(300)
            sh("#t").select_option("nowtext")
            page.wait_for_timeout(200)
            time_fmt_shown = sh("#fmt").input_value()
            time_expected_readonly = sh("#es").get_attribute("readonly") is not None
            sh("#ok").click()
            page.wait_for_timeout(300)

            # 1.6) 时间断言加在输入框上：时间在 value 上，格式该被推断成纯日期
            page.locator("#today_box").click(button="right")
            page.wait_for_timeout(300)
            sh("#t").select_option("nowtext")
            page.wait_for_timeout(250)
            value_fmt = sh("#fmt").input_value()
            value_label = sh("#esLabel").inner_text()
            value_seen = sh("#es").input_value()
            sh("#ok").click()
            page.wait_for_timeout(300)

            # 1.7) canvas 读不出文本，必须被拦住 —— 否则这条断言永远不会通过
            page.locator("#chart").click(button="right")
            page.wait_for_timeout(300)
            sh("#t").select_option("nowtext")
            page.wait_for_timeout(250)
            canvas_blocked = sh("#ok").is_disabled()
            canvas_hint = sh("#hint").inner_text()
            sh("#cancel").click()
            page.wait_for_timeout(200)

            # 2) 空 expected 必须被挡住，勾了「允许空值」才能提交
            page.locator("[data-testid=save-btn]").click(button="right")
            page.wait_for_timeout(300)
            sh("#es").fill("")
            page.wait_for_timeout(200)
            blocked = sh("#ok").is_disabled()
            sh("#allowEmpty").check()
            page.wait_for_timeout(200)
            unblocked = not sh("#ok").is_disabled()
            sh("#cancel").click()

            # 3) 勾选状态断言：expected 用布尔
            page.locator("#agree").click(button="right")
            page.wait_for_timeout(300)
            sh("#t").select_option("checked")
            page.wait_for_timeout(200)
            sh("#ok").click()
            page.wait_for_timeout(300)

            # 4) 可见性断言，显式选 false
            page.locator("[data-testid=save-btn]").click(button="right")
            page.wait_for_timeout(300)
            sh("#t").select_option("visible")
            page.wait_for_timeout(200)
            sh("#eb").select_option("false")
            sh("#ok").click()
            page.wait_for_timeout(300)

            # 5) 文本断言，直接用默认值 —— 元素本身就是按这段文本找到的，
            #    「文本等于自己」是同义反复
            page.get_by_text("待删除的资产", exact=True).click(button="right")
            page.wait_for_timeout(300)
            sh("#ok").click()
            page.wait_for_timeout(300)

            # 副通道收尾
            for st in page.evaluate("() => (window.__rec ? window.__rec.drain() : [])"):
                accept(st)

            browser.close()
    finally:
        srv.shutdown()

    steps.sort(key=lambda s: s["t"])
    # startUrl 必须报出来：生成器用它算 origin，把接口 URL 削成不带端口的路径。
    # origin 对不上时 strip() 不会报错，只会产出 ":56964/api/ok" 这种垃圾匹配器 ——
    # 生成的代码看着正常，回放时永远等不到响应。
    return {"startUrl": base, "steps": steps, "net": net,
            "emptyGuard": {"blocked": blocked, "unblocked": unblocked},
            "timeMenu": {"fmt": time_fmt_shown,
                         "expectedReadonly": time_expected_readonly,
                         "valueFmt": value_fmt, "valueLabel": value_label,
                         "valueSeen": value_seen,
                         "canvasBlocked": canvas_blocked,
                         "canvasHint": canvas_hint}}


def _now():
    import time
    return int(time.time() * 1000)


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(drive(sys.argv[1] if len(sys.argv) > 1 else None),
                     ensure_ascii=False, indent=1))
