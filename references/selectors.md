# 选择器：怎么让脚本活过第二次运行

录制器生成的选择器是起点。这份文档讲怎么把它们改成能长期存活的形态。

## 核心判断：这个选择器依赖什么

每个选择器都建立在某个假设上。假设越接近「用户怎么认这个元素」，越稳。

| 依赖 | 什么时候会坏 | 稳定性 |
|---|---|---|
| `data-testid` | 有人手动改了它（很少） | ★★★★★ |
| role + 无障碍名 | 按钮改了文案 | ★★★★ |
| placeholder | 文案改动 | ★★★ |
| 可见文本 | 文案改动、i18n 切换、同名元素出现 | ★★ |
| 手写的 id | 有人改了它；被换成运行时生成的 | ★★★ |
| CSS 路径 | 布局微调、加个 div、className 哈希变化 | ★ |
| 坐标 | 几乎任何改动 | ☆ |

**绝对不要用坐标。** 录制器不产生坐标定位，但如果你在别处看到 `page.mouse.click(x, y)`，
那是个必须替换的定时炸弹。

## 陷阱一：文本撞车

录制器会标 `⚠ AMBIGUOUS: N 个元素匹配`。这是最危险的一类问题，因为**脚本不会报错，
只会做错事** —— 它点了页面上第 1 个「删除」，而你想删的是第 3 行。

不要用 `.first` 打发它。正确做法是加语义限定：

```python
# ❌ 能跑，但删错行只是时间问题
page.get_by_text("删除", exact=True).first.click()

# ✅ 限定到目标行
page.get_by_role("row", name=re.compile("张三")).get_by_text("删除").click()

# ✅ 限定到区域
page.get_by_role("dialog").get_by_role("button", name="确认").click()

# ✅ 限定到容器（自定义组件没有 role 时）
page.locator(".toolbar").get_by_text("导出").click()
```

注意 Python 的 `.first` 是**属性**不是方法，写成 `.first()` 会 `TypeError`。

> **数撞车要把隐藏元素算进去。** `get_by_text` / `get_by_placeholder` /
> `get_by_label` / `get_by_test_id` 都匹配隐藏元素，只有 `get_by_role` 走无障碍树
> 不匹配。所以「收起的浮层里有个同名选项」照样会让你 strict mode 失败 ——
> 录制器的计数已经按这个规则来，手写时也别只看屏幕上有几个。

## 陷阱二：位置会漂

界面上很多元素的位置随状态变化。常见的几种：

- 页签前面多了个状态图标（已修改、有告警、已锁定），后面所有页签整体位移
- 列表加了一行，后面的行全部下移
- 顶部出现一条提示横幅，整个表单下移几十像素

**这就是坐标定位必然失败的原因**，也是 `nth-of-type` 靠不住的原因。用文本或 role 定位
的选择器天然免疫这类变化。

如果确实需要按位置取（比如「表格第一行」），用语义化的方式：

```python
page.get_by_role("row").nth(1).click()   # 而不是 CSS nth-of-type
```

## 陷阱三：运行时生成的 id 和 class

```html
<div id="tip_box_10059">        <!-- 自增计数器，每次加载都变 -->
<div class="Button_root__x7Fq2">  <!-- CSS Modules 哈希，构建一次变一次 -->
```

录制器会自动跳过含 3 位以上数字的 id 和 class；反过来，**不含长数字且全页唯一的 id 是
录制器倒数第二档的退路**（在 CSS 路径之前），因为手写的 id 通常比位置路径稳得多。
但如果你手写选择器，注意别踩上面这两种。

判断方法：刷新页面，看这个值变不变。

## 陷阱四：自定义组件没有 role

很多 UI 框架（尤其是自研的）的下拉框、开关、树控件是一堆 `div` 拼的，没有
`role` 也没有 `aria-label`。这时候：

**优先找可见文本。** 下拉框通常会把当前值显示出来：

```python
page.locator(".form-item", has_text="系统类型").get_by_text("Windows").click()
```

**利用 label 和控件的位置关系。** 很多表单是 `label + 控件` 并排：

```python
row = page.locator(".form-row", has_text="系统类型")
row.locator("input").click()
```

**实在不行就推动加 `data-testid`。** 这是唯一的根治办法。一个 `data-testid` 换来的
稳定性，比任何 CSS 技巧都值。

## 陷阱五：弹窗不总是出现

首次引导、公告、验证码提示这类元素，出现与否取决于账号状态和历史操作。录制时它出现了，
回放时可能不出现 —— 如果脚本把它当必经步骤，就会在等待时超时。

录制器对 CSS 兜底的点击会自动包成「存在则点」。手写时也照此处理：

```python
banner = page.get_by_role("button", name="我知道了")
if is_present(banner):
    banner.click()
```

`is_present`（在 `rec_helpers.py` 里）对应 JS 的 `isVisible().catch(() => false)` ——
元素不存在时 `is_visible()` 会抛错，不接住的话容错逻辑本身会变成失败点。
Python 的 `if` 里塞不进 try，所以单独成一个函数。

## 陷阱六：确认弹窗

危险操作后面往往跟一个二次确认。**漏掉它是「静默通过」假测试的头号来源**：脚本点了
「删除」，断言也过了，但因为没点「确认」，其实什么都没发生。

把「操作 + 确认」封装成一个函数，别让调用方有机会忘：

```python
def confirm_action(page, trigger: str) -> None:
    page.get_by_role("button", name=trigger, exact=True).click()
    page.get_by_role("button", name="确认", exact=True).click()
```

`rec_helpers.confirm_and_capture` 就是这个模式加上接口断言。

更好的做法是顺便断言接口真的发出去了，见 [safe-writes.md](safe-writes.md)。

## 陷阱七：一行里塞了好几个可点区域

树节点、列表行这类组件，**一整行往往是好几个功能拼起来的**。实测过一个资产树，
每行 `.eui_tree_node_cont` 里有三样东西：

| 子元素 | 点下去会发生什么 |
|---|---|
| `span.eui_tree_hit` | 展开/收起，钻进这个节点 |
| `span.eui_tree_text` | **选中该节点**（真正想要的） |
| `div.eui_tree_node_suffix` | 「返回」，跳回上一层 |

对着整行 `.click()` 时，Playwright 点的是**元素中心** —— 命中哪个功能取决于这一行
当时有多宽、名字有多长。表现出来就是：同一份脚本、同一条 CSS 路径，有时选中了，
有时展开了，有时什么都没发生。查这种问题会很久，因为「点击成功」这件事本身没出错。

**定位到承担你要的那个语义的子元素上**，不要点容器：

```python
# ❌ 点在哪个功能上取决于行宽和文字长度
page.locator(".eui_tree_node_cont", has_text="default-group").click()

# ✅ 明确点「名称」那一段
page.locator(".eui_tree_text", has_text="default-group").click()
```

识别方法：先把整行的子元素打出来看一眼，别凭截图猜。

```python
print(row.evaluate(
    "(e) => [...e.children].map(c => `${c.tagName}.${c.className} \"${c.textContent.trim()}\"`)"))
```

## 陷阱八：点已经选中的东西，什么都不会发生

选中类操作（树节点、页签、单选项）在**已经处于选中态**时通常是空操作 ——
不重新渲染，也不发请求。于是这样写就会时灵时不灵：

```python
# ❌ 该节点恰好已被选中时，这里会一直等到超时
with page.expect_response(lambda r: "/policy-config/" in r.url):
    node.click()
```

更麻烦的是，**选中态常被 sessionStorage 记住**。同一份脚本在你机器上跑得好好的，
换台机器、或者换个浏览器 profile 就挂 —— 因为那边恢复出来的选中项不一样。

两种解法，按场景选：

- **先切到别处再切回来**，强制产生一次状态变化；
- **别依赖这个动作发请求**，改从别的确定性信号读状态。

但第二种要先确认那个信号真的确定。同一个页面上，我一度改成「切换分区页签来触发
重新加载」，结果切页签同样不发请求 —— 选中节点时页面已经把相邻分区预取了，
切过去是缓存命中。**任何「一定会发请求」的假设都要抓包验证过再用。**

## 陷阱九：等响应的条件不要写太宽

界面操作常常并发出好几个请求。等待条件写得宽，就会抓到**不是这一下触发的**那条：

```python
from urllib.parse import urlparse, parse_qs

# ❌ 抓到过上一次作用域还在飞的响应，于是选终端却读出一个组 id
page.expect_response(lambda r: "/policy-config/" in r.url
                     and "type" in parse_qs(urlparse(r.url).query))

# ✅ 把「我期望的是哪一种」写进条件
page.expect_response(lambda r: "/policy-config/" in r.url
                     and parse_qs(urlparse(r.url).query).get("type") == ["asset"])
```

生成器对同一端点的并发请求用的是另一招：`nth_request(path, method, n)` 给每个等待器
一个独立计数器，第 N 个只接第 N 条 —— 条件写得再窄也分不开两条一模一样的请求。

这类错误特别隐蔽：脚本不报错，只是拿到一个**看起来合法**的值，然后用它去请求，
后面全都 200 —— 只是全都打在了错的对象上。

## 陷阱十：`fill()` 可能根本没填进去

`fill()` 设 value 并派发一个 `input` 事件。**受控组件不一定认这套** —— 它从自己的
内部状态重渲染，而那个状态没被更新，于是值当场被抹回去。

实测（EUI 的搜索框）：

```python
box.fill("11.26")
box.input_value()      # → ""     值没进去
magnifier.click()      # → 零请求  拿着空关键字去搜
```

同一个框改成逐字敲就正常：

```python
box.click()
box.press_sequentially("11.26", delay=30)
expect(box).to_have_value("11.26")   # ← 这一句是关键，别省
with page.expect_response(lambda r: "assetName=" in r.url):
    page.locator("span.eui_searchInput_search").click()
```

**填完就断言值真的进去了。** 这一条不是多余的谨慎：填不进去时没有任何报错，
后面点搜索也「成功」，只是搜了个空 —— 最后失败在「找不到结果」，
而你会去查选择器、查数据、查权限，唯独不会怀疑第一步。

**提交方式必须实测，别照搬。** 常见的两种是回车和点搜索按钮，但同一个框上
未必都有效：实测这个组件**回车零请求**，只有点放大镜才发。判断标准只有一个 ——
**有没有真的发出请求**，界面看起来有没有反应不算数：

```python
page.on("request", lambda r: print(r.method, urlparse(r.url).path))
```

顺带一提：搜索的匹配规则未必是「包含即命中」。所以搜索这条路值得配一条兜底路径
（比如逐层展开），否则失败时只会得到一句「找不到元素」。

## 检查清单

改完草稿后过一遍：

- [ ] 没有 `⚠ AMBIGUOUS` 残留，或每一处都加了限定容器
- [ ] 没有坐标点击
- [ ] 没有含长数字的 id / class
- [ ] 可选元素（弹窗、提示条）用了「存在则点」
- [ ] 每个危险操作后面的确认弹窗都处理了
- [ ] 关键步骤有接口断言，不只是界面断言
- [ ] 点的是承担该语义的子元素，不是「一行」这种容器
- [ ] 等响应的条件足够窄，不会抓到并发在飞的别的请求
- [ ] 输入框填完断言了值真的进去（`fill()` 在受控组件上可能静默失效）
- [ ] 搜索/提交动作实测过「真的发出了请求」，不是看界面像有反应
- [ ] 连续跑两遍，第二遍仍然通过（很多问题只在第二遍暴露）
- [ ] 换一个浏览器 profile（或清掉 sessionStorage）再跑一遍 ——
      选中态、展开态这类"上次留下的状态"最容易让脚本在别人机器上挂
