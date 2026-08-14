# 排查

## 浏览器起不来

### `Executable doesn't exist at .../chromium-XXXX/...`

Playwright 每个版本只认自己那一版的 browser build，升级 `playwright` 后就会
要求下载新的（约 170MB）。在弱网或内网环境里这一步经常卡死。

**不用下载。** 本机缓存里往往已有别的版本的完整构建，直接指过去：

```bash
python assets/chrome_path.py      # 打印探测到的浏览器路径
```

`chrome_path.py` 会按 `REC_CHROME_BIN` → ms-playwright 缓存里版本号最高的
→ 系统安装的 Chrome 这个顺序找。`scripts/record.py` 和 `assets/conftest.py` 共用它 ——
JS 版这套逻辑因为一边 ESM 一边 TS 写了两份，Python 侧只有这一份。

跨版本用一般没问题 —— Playwright 与 Chromium 的协议在相邻若干版本间是兼容的。真遇到
不兼容会有明确报错，那时再老老实实下载。

手动指定：

```bash
export REC_CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

### 下载卡在很低的速度

先确认是不是走了代理：

```bash
env | grep -i proxy
lsof -nP -p <下载进程PID> | grep TCP
```

看到 `127.0.0.1:<代理端口>` 就说明流量绕了代理。要么等，要么按上面的办法复用已有构建。

## 证书报错

自签证书、IP 直连、内网域名都会触发。两个地方都要关：

```python
browser = playwright.chromium.launch(args=["--ignore-certificate-errors"])  # 浏览器进程
context = browser.new_context(ignore_https_errors=True)                     # Playwright 上下文
```

```python
session.verify = False   # 如果还用 requests 直接调接口
```

`assets/conftest.py` 里已经配好（`browser_type_launch_args` 和 `browser_context_args`
两个 fixture）。

## 请求没被记录

录制器只抓 `resource_type` 为 `xhr` 或 `fetch` 的请求。以下不会被记录：

- 表单原生提交（`<form method="post">` 直接导航）
- WebSocket
- Service Worker 内部发起的请求
- 页面导航本身（document 请求）
- 静态资源

如果确认接口被调用了却没出现在记录里，先在浏览器 Network 面板确认它的类型。
WebSocket 需要单独监听 `page.on("websocket")`。

用了 `--api` 过滤时，检查过滤片段是否匹配。不确定就先去掉过滤录一遍，看全量。

## 记录里有请求、但响应体是空的

响应体必须在 `page.on("response")` 回调里**当场**取。攒到最后再统一取会拿到：

```
Protocol error (Network.getResponseBody): No resource with given identifier found
```

中间的导航和后续流量会让 Chromium 把 body 从网络缓存里淘汰掉。录制器已经是当场取的
（sync API 在事件回调里同步调 `response.text()` 是安全的，无重入问题）；
自己加抓包逻辑时别踩这个。

## 偶发 5xx

服务端在短时间内收到大量请求时可能返回 503/500，重试即恢复。这跟脚本无关，但会造成
大量假失败。

`pytest.ini` 里已经开了重试（`pytest-rerunfailures`）：

```ini
addopts = --reruns=1
```

CI 上建议调到 2。如果失败集中在某几个接口，说明它们对并发敏感 —— 在这些操作之间加
等待。并发本身不用管：pytest 默认串行，正是想要的行为（装了 `pytest-xdist` 才会并行，
界面用例别开）。

## 脚本第一遍过、第二遍挂

几乎总是这两个原因之一：

**登录态。** 见 [auth-and-session.md](auth-and-session.md)。

**状态残留。** 第一遍改了数据（创建了条目、切换了开关），第二遍的初始状态就不同了。
要么让用例自己清理，要么把断言写成对增量的断言而不是对绝对值的断言。

**先跑两遍再交付。** 只跑一遍的脚本约等于没验证过。

## 生成的脚本点错了元素

看草稿里有没有 `⚠ AMBIGUOUS` 标记。有就按 [selectors.md](selectors.md) 加限定容器。

没有标记却仍然点错，可能是元素在录制和回放时的可见性不同（比如折叠面板的状态不一样）。
在点击前显式展开，别依赖上一次的状态。

## `Locator.evaluate` 在开关上超时 30 秒

选择器解析到了内层的文本元素，而状态层是它的**兄弟**、不是后代 ——
`sw.locator(".xxx_container")` 于是永远找不到。

正常情况下录制器已经避开这种情形（文本来自后代时不用 `get_by_text`，退到稳定 id 或
CSS 路径），见 SKILL.md 的「选择器优先级」。见到这个报错说明是回归，跑一遍 `pytest`。

## 生成的用例在第一个接口断言上超时

检查生成的匹配器里有没有端口号，比如 `":56964/api/ok" in r.url`。那是
`generate_spec` 的 `start_url` 与录制时真实用过的地址 origin 不一致造成的 ——
`strip()` 削不掉就把残渣留在了路径里，代码看着正常、回放永远等不到响应。

重新生成时 `start_url` 要用 `<name>.json` 里的 `startUrl`，不要自己编一个。

## 录制器本身报错

录制器的所有事件处理都包在 `try/catch` 里，**录制失败不会影响用户操作** —— 这是有意的，
不能因为算选择器出错就让用户的点击失效。

代价是出错时静默跳过，那一步不会出现在记录里。如果发现某个操作没被录到，
打开浏览器的开发者工具看 Console —— 注入层的异常会出现在那里，而不是在驱动侧的终端。

## 关掉浏览器后没有生成文件

正常流程是关闭窗口触发生成。如果进程被强杀（Ctrl-C 或 kill），生成逻辑不会执行，
已录的步骤会丢失。

**关窗口，不要 Ctrl-C。**

登录态是个例外：它每个轮询周期（800ms）就快照一次，所以即使强杀，上一次快照仍在内存里 ——
但没走到落盘那一步同样存不下来。
