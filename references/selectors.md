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
| CSS 路径 | 布局微调、加个 div、className 哈希变化 | ★ |
| 坐标 | 几乎任何改动 | ☆ |

**绝对不要用坐标。** 录制器不产生坐标定位，但如果你在别处看到 `page.mouse.click(x, y)`，
那是个必须替换的定时炸弹。

## 陷阱一：文本撞车

录制器会标 `⚠ AMBIGUOUS: N 个元素匹配`。这是最危险的一类问题，因为**脚本不会报错，
只会做错事** —— 它点了页面上第 1 个「删除」，而你想删的是第 3 行。

不要用 `.first()` 打发它。正确做法是加语义限定：

```ts
// ❌ 能跑，但删错行只是时间问题
await page.getByText('删除', { exact: true }).first().click();

// ✅ 限定到目标行
await page.getByRole('row', { name: /张三/ }).getByText('删除').click();

// ✅ 限定到区域
await page.getByRole('dialog').getByRole('button', { name: '确认' }).click();

// ✅ 限定到容器（自定义组件没有 role 时）
await page.locator('.toolbar').getByText('导出').click();
```

## 陷阱二：位置会漂

界面上很多元素的位置随状态变化。常见的几种：

- 页签前面多了个状态图标（已修改、有告警、已锁定），后面所有页签整体位移
- 列表加了一行，后面的行全部下移
- 顶部出现一条提示横幅，整个表单下移几十像素

**这就是坐标定位必然失败的原因**，也是 `nth-of-type` 靠不住的原因。用文本或 role 定位
的选择器天然免疫这类变化。

如果确实需要按位置取（比如「表格第一行」），用语义化的方式：

```ts
await page.getByRole('row').nth(1).click();   // 而不是 CSS nth-of-type
```

## 陷阱三：运行时生成的 id 和 class

```html
<div id="tip_box_10059">        <!-- 自增计数器，每次加载都变 -->
<div class="Button_root__x7Fq2">  <!-- CSS Modules 哈希，构建一次变一次 -->
```

录制器会自动跳过含 3 位以上数字的 id 和 class。但如果你手写选择器，注意别踩。

判断方法：刷新页面，看这个值变不变。

## 陷阱四：自定义组件没有 role

很多 UI 框架（尤其是自研的）的下拉框、开关、树控件是一堆 `div` 拼的，没有
`role` 也没有 `aria-label`。这时候：

**优先找可见文本。** 下拉框通常会把当前值显示出来：

```ts
await page.locator('.form-item', { hasText: '系统类型' }).getByText('Windows').click();
```

**利用 label 和控件的位置关系。** 很多表单是 `label + 控件` 并排：

```ts
const row = page.locator('.form-row', { hasText: '系统类型' });
await row.locator('input').click();
```

**实在不行就推动加 `data-testid`。** 这是唯一的根治办法。一个 `data-testid` 换来的
稳定性，比任何 CSS 技巧都值。

## 陷阱五：弹窗不总是出现

首次引导、公告、验证码提示这类元素，出现与否取决于账号状态和历史操作。录制时它出现了，
回放时可能不出现 —— 如果脚本把它当必经步骤，就会在等待时超时。

录制器对 CSS 兜底的点击会自动包成「存在则点」。手写时也照此处理：

```ts
const banner = page.getByRole('button', { name: '我知道了' });
if (await banner.isVisible().catch(() => false)) await banner.click();
```

注意 `.catch(() => false)` —— 元素不存在时 `isVisible()` 会抛错，不接住的话
容错逻辑本身会变成失败点。

## 陷阱六：确认弹窗

危险操作后面往往跟一个二次确认。**漏掉它是「静默通过」假测试的头号来源**：脚本点了
「删除」，断言也过了，但因为没点「确认」，其实什么都没发生。

把「操作 + 确认」封装成一个函数，别让调用方有机会忘：

```ts
async function confirmAction(page: Page, trigger: string) {
  await page.getByRole('button', { name: trigger, exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
}
```

更好的做法是顺便断言接口真的发出去了，见 [safe-writes.md](safe-writes.md)。

## 检查清单

改完草稿后过一遍：

- [ ] 没有 `⚠ AMBIGUOUS` 残留，或每一处都加了限定容器
- [ ] 没有坐标点击
- [ ] 没有含长数字的 id / class
- [ ] 可选元素（弹窗、提示条）用了「存在则点」
- [ ] 每个危险操作后面的确认弹窗都处理了
- [ ] 关键步骤有接口断言，不只是界面断言
- [ ] 连续跑两遍，第二遍仍然通过（很多问题只在第二遍暴露）
