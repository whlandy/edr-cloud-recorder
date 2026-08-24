# 读 UI 做断言的陷阱

自动化里最费时间的排查，往往不是业务逻辑错了，而是**你读到的不是你以为的那个东西**。
这类失败有个共同特征：**现象把你指向错误的方向**。断言失败时，先问一句
「我读的是不是我以为的对象」，通常比检查业务逻辑更快。

下面三条都是实测踩出来的，按踩中的代价从大到小排。

## 一、界面不会自己重画

表格、列表、日志这类视图，数据到了不代表**已渲染的界面**变了。很多桌面应用要点
「刷新」才重画，Web 端也有大量组件只在特定事件下重取。

轮询一个不会自动刷新的视图，等多久都是旧内容：

```python
# ❌ 轮询 240 秒，全是旧数据，结论是"策略没生效"
for _ in range(24):
    if "预期记录" in read_table(): break
    time.sleep(10)

# ✅ 每次读之前先刷新
def read_table(refresh=True):
    if refresh:
        click("Refresh"); time.sleep(2)
    return dump()
```

实测代价：一条 **5 秒就到达**的记录，被判成 240 秒都没出现，然后往「心跳周期多长」
「产品到底写不写这个日志」方向查了很久 —— 全是无用功，加一次刷新就好了。

**判断方法**：手动在界面上点一下刷新，如果内容变了，那你的轮询就少了这一步。

## 二、同一个进程可能有多个顶层窗口

「连接到进程」通常抓的是**当前最上层**的窗口。子窗口（设置、日志、详情）一旦打开，
后续的 `dump_tree` 读到的就是它，而不是你以为的主界面。

```python
# ❌ 主界面上明明有 IP 和主机名，却读出 None
connect(process_name="Client.exe")
tree = dump_tree()            # 实际拿到的是「日志中心」子窗口

# ✅ 明确指定要读哪个窗口
tree = dump_tree(window_title_re=HOME_WINDOW)
```

**这类失败最有迷惑性**：信息明明存在，却读成 None，让人怀疑是不是产品没显示、
是不是权限不够。

Qt 应用尤其常见 —— 它的对话框往往是独立顶层窗口，不是主窗口的子控件。

**顺带**：可选参数要**省略**，不要传 `None`。不少工具的 schema 拒绝 null，
`window_title_re=None` 会直接报参数校验失败，而错误信息通常不会告诉你「省略即可」。

## 三、确认操作对象和观测对象是同一个

跨系统验证（云端下发 → 端侧生效）时，两边各自指定目标，很容易指到不同的东西上。

实测代价：云端一直往 `11.27` 下发，端侧连的是 `11.26`。整条链路每一步都「成功」，
但验证的是两台无关的机器，现象是**端侧永远没反应**。往心跳、日志机制、产品行为
查了很久，根因只是参数传错了一位数字。

界面上通常就写着身份信息（IP、主机名、序列号），开跑前比对一次：

```python
def assert_matches(self, expected_name: str) -> str:
    me = self.identity()          # 从界面读 IP / 主机名
    hits = [v for v in (me["ip"], me["hostname"]) if v and v in expected_name]
    if not hits:
        raise EndpointError(
            f"端侧靶机与云端资产不是同一台：\n"
            f"  端侧 → ip={me['ip']} hostname={me['hostname']}\n"
            f"  云端 → {expected_name}"
        )
    return f"匹配 {', '.join(hits)}"
```

关键是**报错要把两边都打出来**。只说「不匹配」，人还是得自己去查两边分别是什么。

## 选断言点：优先选"留痕"而不是"状态"

验证一个变更是否生效时，有两类观测对象：

| | 例子 | 问题 |
|---|---|---|
| **状态** | 某个开关当前是开还是关 | 界面上未必显示；显示了也分不清是本次改的还是原本就是 |
| **留痕** | 操作日志里的一条记录 | 带时间戳，能和基线做差集，天然可区分 |

实测中「自保护」这个开关在客户端主界面上**根本不显示**，最初写的
`text_present("自保护")` 永远跑不通 —— 那是在没看过真实界面时凭空想的探针。
换成读操作日志、和基线做差集之后，一次就命中。

**留痕类断言的标准套路**：

```
1. 采基线（记下当前所有记录）
2. 执行变更
3. 轮询（每次先刷新），取和基线的差集
4. 断言差集里有预期的那条
```

差集比「包含某文本」强得多：它能区分「这次产生的」和「本来就有的」，
而后者是假通过的常见来源。

## 按文本定位、再断言那段文本 —— 这是同义反复

```python
# ❌ 只有元素消失才会失败：元素本来就是按这段文本找到的
expect(page.get_by_text("WannaCry.exe", exact=True)).to_have_text("WannaCry.exe")

# ✅ 如实写出它真正在断言的东西
expect(page.get_by_text("WannaCry.exe", exact=True).first).to_be_visible()
```

实测一次真实录制里加的两条断言**全是**这个形状 —— 看着像断言，其实什么都没断。
右键选「文本等于」的意思通常是「这段文字应该在」，那就该生成存在性断言。

The recorder renders its assertion menu inside a dedicated same-origin iframe. Shadow DOM alone
is insufficient because page-level capture listeners still receive menu events and can stop
`mousedown`, `click`, `input`, or `change` before controls handle them. The iframe also prevents
host-page CSS resets from changing menu visibility or pointer behavior.
录制器现在自动改写这一种，并在草稿里说明为什么改了。

改写只在 expected **没被改过**时发生。用户把默认值改成别的（比如断言这一格
稍后会变成「已隔离」），那是实打实的命题，不能动它。同理，用 testid / role /
placeholder 定位的元素，断言它的文本也是实打实的。

**顺带解掉一个假问题**：这种断言撞车不算缺陷。「至少有一个 WannaCry.exe 可见」
本来就是确定的命题，`.first()` 在这里是诚实的，不是掷骰子 —— 所以草稿里不再
对它报「回放时可能点错」。要断言**某一行**的话才需要作用域，而那时你得先有一个
不会变的锚点：实测那两行只有发现时间不同，而时间正是最不能拿来当锚点的东西。

## 一个提醒

写探针之前**先把真实界面 dump 出来看一眼**。凭印象或凭合理推测写出来的探针，
跑不通的时候你会先怀疑功能坏了，而不是怀疑探针本身 —— 这个方向一旦搞反，
排查成本会高出一个数量级。
