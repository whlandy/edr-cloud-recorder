# 排查

## 浏览器起不来

### `Executable doesn't exist at .../chromium-XXXX/...`

Playwright 每个版本只认自己那一版的 browser build，升级 `@playwright/test` 后就会
要求下载新的（约 170MB）。在弱网或内网环境里这一步经常卡死。

**不用下载。** 本机缓存里往往已有别的版本的完整构建，直接指过去：

```bash
node scripts/chrome-path.mjs      # 打印探测到的浏览器路径
```

`scripts/chrome-path.mjs` 会按 `REC_CHROME_BIN` → ms-playwright 缓存里版本号最高的
→ 系统安装的 Chrome 这个顺序找。`record.mjs` 和 `assets/playwright.config.ts` 都用它。

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

自签证书、IP 直连、内网域名都会触发。三个地方都要关：

```ts
chromium.launch({ args: ['--ignore-certificate-errors'] })   // 浏览器进程
browser.newContext({ ignoreHTTPSErrors: true })              // Playwright 上下文
```

```python
session.verify = False   # Python 侧
```

`assets/playwright.config.ts` 里已经配好。

## 请求没被记录

录制器只抓 `resourceType` 为 `xhr` 或 `fetch` 的请求。以下不会被记录：

- 表单原生提交（`<form method="post">` 直接导航）
- WebSocket
- Service Worker 内部发起的请求
- 页面导航本身（document 请求）
- 静态资源

如果确认接口被调用了却没出现在记录里，先在浏览器 Network 面板确认它的类型。
WebSocket 需要单独监听 `page.on('websocket')`。

用了 `--api` 过滤时，检查过滤片段是否匹配。不确定就先去掉过滤录一遍，看全量。

## 偶发 5xx

服务端在短时间内收到大量请求时可能返回 503/500，重试即恢复。这跟脚本无关，但会造成
大量假失败。

配置里开重试：

```ts
retries: process.env.CI ? 2 : 1,
```

如果失败集中在某几个接口，说明它们对并发敏感 —— 在这些操作之间加等待，或者把
`workers` 设成 1 串行执行。

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

## 录制器本身报错

录制器的所有事件处理都包在 `try/catch` 里，**录制失败不会影响用户操作** —— 这是有意的，
不能因为算选择器出错就让用户的点击失效。

代价是出错时静默跳过，那一步不会出现在记录里。如果发现某个操作没被录到，
用 `node --trace-warnings scripts/record.mjs ...` 看有没有线索。

## 关掉浏览器后没有生成文件

正常流程是关闭窗口触发生成。如果进程被强杀（Ctrl-C 或 kill），生成逻辑不会执行，
已录的步骤会丢失。

**关窗口，不要 Ctrl-C。**
