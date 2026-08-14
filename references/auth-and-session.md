# 登录态：为什么脚本第二次跑就跳登录页

自动化脚本最常见的失败不是选择器错，是登录态没带对。这份文档按「症状 → 原因 → 解法」
组织，遇到哪个看哪个。

## 症状一：录制时好好的，回放直接跳登录页

### 先判断登录态存在哪

在浏览器控制台跑：

```js
JSON.stringify({
  cookie: document.cookie.split(';').map(s => s.trim().split('=')[0]),
  local: Object.keys(localStorage),
  session: Object.keys(sessionStorage),
})
```

然后对照：

| 登录态位置 | Playwright `storage_state` 是否覆盖 |
|---|---|
| Cookie | ✅ 覆盖 |
| localStorage | ✅ 覆盖 |
| **sessionStorage** | ❌ **不覆盖** |

`storage_state` 只存 cookies + localStorage。**这是最容易踩的坑**：登录流程写得完全正确，
`state.json` 也生成了，但应用启动时读的是 `sessionStorage.token`，读不到就跳登录页。

### 解法：用 add_init_script 在页面脚本执行前注回去

时机是关键。`add_init_script` 在页面任何脚本运行**之前**执行，所以 SPA 启动时能读到。
换成 `page.evaluate()` 就晚了 —— 那时应用已经判定未登录并开始跳转。

导出（登录之后）：

```python
(auth_dir / "session-storage.json").write_text(
    page.evaluate("() => JSON.stringify(sessionStorage)"), encoding="utf-8")
```

注入（每个测试之前）：

```python
raw = (auth_dir / "session-storage.json").read_text(encoding="utf-8")
page.add_init_script(script="""(() => {
  try {
    for (const [k, v] of Object.entries(JSON.parse(%s))) {
      try { sessionStorage.setItem(k, v); } catch { /* 只读键或超配额 */ }
    }
  } catch { /* 文件损坏时不要拖垮整个用例 */ }
})()""" % json.dumps(raw))
```

> **Python 的 `add_init_script` 没有 `arg` 参数。** JS 的
> `addInitScript(fn, arg)` 能把数据当参数传进页面，Python 版只收 `script` / `path`。
> 所以数据必须内联进脚本字符串，而且要用 `json.dumps` 转义 —— 直接 f-string 拼裸文本，
> 登录态里的引号会把脚本弄坏。

`assets/auth_setup.py` 和 `assets/conftest.py` 已经实现了这套，直接复制即可
（`authed_page` fixture 负责注入）。

## 症状二：同一个浏览器，这个标签页是登录的，新标签页却要重新登录

说明登录态是**按标签页隔离**的 —— 几乎可以确定在 sessionStorage 里（cookie 和
localStorage 都是跨标签页共享的）。

这个特性有个隐蔽的后果：**任何「复用用户已打开的浏览器」的方案都会失效**，因为新开的
标签页拿不到旧标签页的 sessionStorage。只能在脚本控制的那个上下文里完成登录，或者按
上面的办法注入。

## 症状三：走 SSO，登录要跳好几次

典型流程是 `应用 → SSO 登录页 → 回调 → 应用`。写登录流程时注意两点：

**不要用固定 URL 判断登录成功。** 回调可能带各种参数，也可能多跳一次。用应用侧的
状态判断更可靠：

```python
poll_until(lambda: logged_in(page), True, timeout=60.0, interval=1.0)
```

**登录后常有一次性弹窗**（首次引导、公告、隐私协议、验证码提示）。它们会挡住后续操作，
在登录流程里就关掉，别留给每个用例各自处理 —— `auth_setup.dismiss_dialogs(page)` 干这个。

## 症状四：requests / curl 复用浏览器登录态失败

从 Playwright 导出 cookie 给别的地方用是可行的（`auth_setup.export_state` 会单独写一份
`cookies.json`），但有两个常见障碍：

**CSRF token。** 很多框架要求把某个 cookie 的值回填到请求头（`X-CSRF-Token`、
`X-XSRF-TOKEN`、`roarand` 等）。浏览器里是前端代码自动做的，换成 requests 就得手动：

```python
for name in ("XSRF-TOKEN", "csrfToken", "_xsrf"):
    if name in session.cookies:
        session.headers[name] = session.cookies[name]
```

**登录态在 sessionStorage 而不是 cookie。** 这种情况下光有 cookie 不够，服务端认的是
请求头里的 token。先在浏览器 Network 面板看一个已认证的请求带了什么头，照着补。

**不要凭空实现登录接口。** SSO 的登录 POST 往往有加密参数、验证码、时间戳签名。
没抓过就写，写出来的是猜测。可靠做法是让 Playwright 完成登录并导出凭据，其他地方只负责复用。

## 别让失败现场泄露密码

Playwright 默认在失败时保留 trace、录像和截图。登录失败时，输入框里的**明文密码**
会原样进到这些文件里 —— 这是凭据泄露最容易被忽视的一条路径，因为它不经过代码、
不经过版本库，只是静静躺在 `test-results/` 里。

**在 pytest 这套结构里，这条路径是被结构消掉的，不靠记得配开关。**

pytest-playwright 的产物录制挂在**函数作用域**的 `new_context` fixture 上。
而 `conftest.py` 里的 `auth_state` fixture 走的是 `browser.new_context()` 直连 ——
那个 context 从头到尾不经过 pytest-playwright，因此不会为它写任何 trace / 录像 / 截图。
Playwright 测试运行器那份 `error-context.md`（JS 版最难防的一条泄露路径，不受
trace/video/screenshot 开关控制）在 pytest 侧根本不存在。

实测验证过：故意用错密码登录，同时开 `--tracing=on --video=on --screenshot=on`，
`test-results/` 目录压根没生成，磁盘上和终端输出里都搜不到明文。

> **别把登录逻辑改成走 `page` / `context` fixture。** 那一行 `browser.new_context()`
> 就是上面这个性质的来源。改掉之后产物会被录下来，泄露路径就回来了 ——
> `conftest.py` 里保留了一个清理动作作为兜底，但那是兜底，不是设计。

**Python 侧有一条 JS 侧没有的泄露路径：`pytest --showlocals`（或 `-l`）会把栈帧里的
局部变量原样打印出来。** 所以 `auth_setup.py` 刻意**不把密码绑定到局部变量** ——
它只以 `credentials()["password"]` 的形式出现在调用点 —— 那是个临时 dict，不是具名局部变量：

```python
user_box.fill(user)
pass_box.fill(credentials()["password"])   # 不绑定到名字
```

`require_credentials()` 里判完之后会 `del auth`。这一步不是多余的谨慎：
「只设了密码、忘了用户名」这一种情形会带着**真密码**抛异常，不 del 就漏出去了。

自己写的用例不受这个约束，`conftest.py` 在同时检测到 `--showlocals` 和可用凭据
时会发一条警告。

如果已经发生了，`test-results/` 整个删掉 —— trace.zip 里是完整的 DOM 快照，
光删截图不够。

## 登录后的状态轮询要容忍导航

SSO 登录后页面会连续跳转。轮询里的 `page.evaluate` 一旦撞上导航就抛
`Execution context was destroyed`。把异常当作"还没就绪"，轮询才真正起作用 ——
否则这一步变成靠运气：躲开导航窗口就过，撞上就挂。

```python
def logged_in(page) -> bool:
    try:
        return bool(page.evaluate(
            "() => sessionStorage.length > 0 || localStorage.getItem('token') !== null"))
    except Exception:
        return False        # 撞上导航了，下一轮再问


poll_until(lambda: logged_in(page), True, timeout=60.0, interval=1.0)
```

`poll_until` 在 `rec_assert.py` 里 —— Python 版 Playwright 没有 `expect.poll`。
JS 侧要特别小心的是 `expect.poll` **遇到异常直接失败、不重试**；`poll_until` 同样不吞
异常，所以 try/except 得写在被轮询的函数里面，不能省。

## 登录框定位：不要用 .or_() 组合

看起来稳妥的写法反而会出事：

```python
# ❌ 密码框没有 placeholder 时，这会解析到用户名框
pass_box = frame.get_by_placeholder(re.compile("密码")).or_(frame.locator("#password")).first
```

结果是用户名和密码被填进**同一个输入框**，登录失败，而失败现场里带着明文密码。

用单一稳定的定位方式，填完再断言一次：

```python
user_box = frame.locator("#username")
pass_box = frame.locator("#password")
user_box.fill(user)
pass_box.fill(credentials()["password"])
# 填错框是静默失败，断言让它显式
expect(user_box, "用户名框内容异常，疑似把密码也填了进去").to_have_value(user)
```

顺带一提，这个坑也是录制器为什么要验 placeholder 唯一性的原因 —— 见
[selectors.md](selectors.md)。

## 三种登录路径怎么共存

`auth_state` fixture 按凭据在不在来分流：

| 情形 | 行为 |
|---|---|
| 有凭据（env 或 config.json 的 auth 段） | 自动登录，刷新 `.auth/` |
| 没凭据，但 `.auth/state.json` 存在 | 直接复用（这是 `manual_login.py` 那条路） |
| 没凭据也没 state.json | 明确报错，把三条路都告诉你 |

第二条是刻意的：JS 版每次 `npm test` 都重跑 setup，会把手动登录导出的登录态**覆盖掉**，
于是验证码站点每跑一次测试就要重新人工登录一次。

## 凭据管理

- 优先用环境变量（`REC_USER` / `REC_PASSWORD`），什么都不落盘
- `.auth/` 加进 `.gitignore` —— 里面是有效会话，等同于密码
- 录制器对 `type=password` 的输入只记录「填了密码」这个动作，值替换成
  `os.environ.get("REC_PASSWORD", "")`。生成的草稿因此可以安全提交
- 真要落盘存密码，放 `~/.config/edr-cloud-recorder/config.json` 的 `auth` 段并
  `chmod 600`，别放项目目录（那通常就是仓库目录）。加载器会在权限过宽时提醒
- 会话会过期。脚本要能识别「跳到登录页」并给出清晰报错，而不是在某个选择器上超时
