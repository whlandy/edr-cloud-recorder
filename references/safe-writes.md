# 安全地验证会改数据的操作

录制只读流程没有风险。但用户常常真正想固化的恰恰是**会改东西**的操作 ——
下发配置、创建任务、删除条目。这份文档讲两种做法，按「能不能承受真实写入」选。

## 做法一：只抓请求体，不真正发出去

**适用**：你只想知道前端构造的请求长什么样，不需要服务端真的执行。

Playwright 的 `page.route()` 可以拦截请求并返回伪造的响应。请求体照样能读到，
但一个字节都不会到服务端。

```ts
let captured: any = null;

await page.route('**/api/v1/**', async (route) => {
  if (route.request().method() === 'GET') return route.continue();
  captured = JSON.parse(route.request().postData() ?? '{}');
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ code: '200', msg: 'success' }),
  });
});

// 正常操作界面，包括点「确认」
await page.getByRole('button', { name: '强制应用' }).click();
await page.getByRole('button', { name: '确认' }).click();

expect(captured.scope).toMatchObject({ type: 'group', mode: 'force' });
```

这个办法特别适合枚举**危险选项的组合**。比如一个「强制应用 + 应用到下级」的操作会覆盖
一整组对象的配置，还原成本极高 —— 但你可以把四种勾选组合全点一遍，把请求体差异摸清楚，
而实际什么都没发生。

**注意**：界面会以为操作成功了，可能显示成功提示、刷新列表。这是预期行为，别被误导成
「真的写进去了」。

## 做法二：基线 → 执行 → 还原 → 逐字节比对

**适用**：需要验证服务端真实行为（返回码、副作用、幂等性）。

四步缺一不可：

```
1. 采基线    把即将被改动的对象的当前状态完整读下来（原始响应文本，不是解析后的对象）
2. 执行      不改任何表单值，读到什么就提交什么 —— 内容上等价于空操作
3. 还原      用系统提供的还原入口，或把基线值重新提交回去
4. 比对      再读一次，与基线**逐字节**比较
```

比对必须用原始文本，不能用解析后的对象做深比较。字段顺序、数字精度、空值表示
（`null` vs 缺失）的差异，只有字节比较才抓得住。

```ts
const before = await readState(page, objectId);       // 返回 response.text()
await applyUnchanged(page);
await revert(page);
const after = await readState(page, objectId);
expect(after, '未能还原到基线').toBe(before);
```

### 先弄清楚「原值重放」到底改了什么

即使一个字段都没改，提交本身也可能有副作用。最常见的是**继承关系**：

```
提交前: 该对象继承上级配置
提交后: 该对象变成「自定义配置」，不再跟随上级
```

配置内容一模一样，但继承链断了。这类变化容易被忽略，因为逐字段比对看不出来 ——
它藏在元数据里。所以基线要包含元数据字段（谁创建的、是继承还是自定义、状态是什么）。

**动手前先确认有还原入口。** 很多系统会在对象变成「自定义」后显示一个
「恢复继承」链接。有它，整条链路可逆；没有，就别做真实写入。

### 先挑代价最小的目标

如果某个对象**本来就是**自定义状态，对它做原值重放是真正的空操作 —— 连元数据都不变。
从这种目标开始验证链路，确认无误后再扩大范围。

## 绝不要做的事

**不要对「会波及下级」的操作做真实写入**。强制应用、批量下发、级联删除这类操作会覆盖
下级对象各自的自定义配置，还原意味着逐个重放它们各自的原值 —— 而你未必采过它们的基线。

这类操作一律用做法一（路由拦截）。在脚本里显式拒绝：

```ts
if (scope === 'group' && cascade) {
  throw new Error('级联写入不做真实执行，请用路由拦截验证请求体');
}
```

## 把还原写进用例，而不是靠人记得

写入型用例的标准结构：

```ts
test('下发配置并还原', async ({ page }) => {
  const before = await readState(page, TARGET);

  const { status, body } = await applyAndCapture(page);
  expect(status).toBe(200);
  expect(body.scope).toMatchObject({ id: TARGET, mode: 'apply' });

  await revert(page);

  const after = await readState(page, TARGET);
  expect(after, '未能还原').toBe(before);
});
```

用例失败时基线还在内存里 —— 把它写到文件，人工回滚时用得上：

```ts
} catch (e) {
  fs.writeFileSync(`rollback-${TARGET}.json`, before);
  throw e;
}
```

## 记录你做过什么

真实写入结束后，在报告或提交信息里列清楚：动了哪个对象、哪个操作、是否真发到服务端、
现在是什么状态。别让「跑过一轮测试」变成一笔糊涂账。

| 目标 | 操作 | 真发到服务端 | 现状 |
|---|---|---|---|
| 对象 A | 下发 → 还原 | 是（均 200） | 已还原 |
| 组 B | 四种组合 | 否，本地短路 | 无变化 |
