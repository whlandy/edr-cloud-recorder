/**
 * 从录制数据生成 Playwright 脚本草稿
 *
 * 单独成文件是为了能脱离浏览器测试 —— 喂一份录制 JSON 进来就能验证生成结果，
 * 不必每次都真跑一遍录制。也方便对旧录制重新生成（改进生成逻辑后回炉）。
 */
export function generateSpec({ steps, net, startUrl, name }) {
  const ORIGIN = new URL(startUrl).origin;
  const strip = (u) => u.replace(ORIGIN, '');
  const between = (a, b) => net.filter((n) => n.t >= a && n.t < b && n.phase === 'res');

/**
 * 把抓到的请求体变成断言用的字面量
 *
 * 直接把整个 body 塞进 toMatchObject 会立刻失效：里面的 UUID、雪花 ID、时间戳
 * 每次运行都不一样。但把它们整条删掉又丢了「这个字段必须存在」的信息。
 * 折中办法是保留结构、把易变值换成 expect.any(String) —— 字段在不在、类型对不对
 * 仍然被守住，具体值不参与比较。
 */
  const VOLATILE = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{10,})$/i;
  const toMatcher = (v, indent = 4) => {
  const pad = ' '.repeat(indent);
  if (v === null) return 'null';
  if (Array.isArray(v)) {
    if (!v.length) return '[]';
    return `[\n${v.map((x) => pad + '  ' + toMatcher(x, indent + 2)).join(',\n')}\n${pad}]`;
  }
  if (typeof v === 'object') {
    const ks = Object.keys(v);
    if (!ks.length) return '{}';
    return `{\n${ks.map((k) => `${pad}  ${JSON.stringify(k)}: ${toMatcher(v[k], indent + 2)}`).join(',\n')}\n${pad}}`;
  }
  if (typeof v === 'string' && VOLATILE.test(v)) return 'expect.any(String)';
  // 数字型的时间戳和雪花 ID 也要放宽。只处理字符串的话，像「最近 30 天」这种
  // 默认查询条件会把录制那一刻的毫秒时间戳原样钉进断言 —— 下一次运行必然对不上，
  // 而这恰恰是上面那段注释声称要防住的失效方式。
  // 门槛取 1e9：10 位是秒级时间戳，13 位是毫秒级，业务上的页码、数量都远小于它。
  if (typeof v === 'number' && Number.isInteger(v) && Math.abs(v) >= 1e9) return 'expect.any(Number)';
  return JSON.stringify(v);
};

// 为某个响应找回它对应的请求体
  const reqBodyOf = (res) => {
  const r = net.filter((n) => n.phase === 'req' && n.url === res.url && n.method === res.method && n.t <= res.t).pop();
  if (!r?.body) return null;
  try { return JSON.parse(r.body); } catch { return null; }
};

// 生成一次操作的代码：若该操作触发了写请求，就把它包成「等待响应 + 断言」
  let respSeq = 0;
  const emitAction = (actionCode, calls, warn) => {
  const writes = calls.filter((c) => c.method !== 'GET' && c.status < 400);
  if (!writes.length) {
    const out = [`  ${actionCode};${warn}`];
    for (const c of calls) {
      out.push(`  //   ↳ ${c.method} ${strip(c.url)} -> ${c.status}`);
      if (c.status >= 400 && c.body) out.push(`  //     ⚠ 失败响应: ${c.body.slice(0, 160).replace(/\s+/g, ' ')}`);
    }
    return out;
  }

  // 写请求才值得断言：它是这一步真正产生的副作用，也是最不该悄悄变化的契约。
  // GET 留作注释 —— 一次点击可能连带十几个读请求，全断言只会让用例难读又易碎。
  const out = [];
  // 变量名必须全局唯一：整个 test 是一个作用域，重复的 const resp 会直接编译失败
    const base = ++respSeq;
    const names = writes.map((_, i) => (writes.length === 1 ? `resp${base}` : `resp${base}_${i + 1}`));
  out.push(`  const [${names.join(', ')}] = await Promise.all([`);
  for (const w of writes) {
    const p = strip(w.url).split('?')[0];
    out.push(`    page.waitForResponse((r) => r.url().includes(${JSON.stringify(p)}) && r.request().method() === ${JSON.stringify(w.method)}),`);
  }
  out.push(`    ${actionCode.replace(/^await /, '')},`);
  out.push(`  ]);${warn}`);
  for (let i = 0; i < writes.length; i++) {
    out.push(`  expect(${names[i]}.status()).toBe(${writes[i].status});`);
    const body = reqBodyOf(writes[i]);
    if (body && typeof body === 'object') {
      out.push(`  expect(${names[i]}.request().postDataJSON()).toMatchObject(${toMatcher(body)});`);
    }
  }
  for (const c of calls.filter((c) => c.method === 'GET')) {
    out.push(`  //   ↳ GET ${strip(c.url)} -> ${c.status}`);
  }
  return out;
};

  /**
   * 修正「先回车、后填值」
   *
   * 值是在 change 事件里记的，而 change 要等失焦或回车**之后**才触发；按键则是
   * 按下就记。于是「打字 + 回车」这个最普通的登录动作，录出来必然是 press 在
   * fill 前面 —— 照原样回放就是在空输入框上回车，然后才填值。
   *
   * 同一个字段上「先回车、再填值」不可能成立：那个值本来就是回车之前打进去的。
   * 所以直接交换，不设时间阈值 —— 阈值只会在人打字慢的时候漏修。
   */
  steps = steps.slice();
  for (let i = 0; i < steps.length - 1; i++) {
    const a = steps[i], b = steps[i + 1];
    if (a.type === 'press' && b.type === 'fill' && a.sel === b.sel && a.inFrame === b.inFrame) {
      // 时间戳跟着换：接口是按时间窗口挂到步骤下面的，不换的话窗口会倒过来，
      // 回车触发的登录请求会挂到填值那一步下面。
      steps[i] = { ...b, t: a.t };
      steps[i + 1] = { ...a, t: b.t };
    }
  }

  const lines = [
  `import { test, expect } from '@playwright/test';`,
  ``,
  `// 由 web-record 生成：${name}`,
  `// 写请求已自动生成断言（状态码 + 请求体形态）；GET 保留为注释。`,
  `// 请求体里的 UUID / 长数字 ID 已换成 expect.any(String)，避免每次运行都失效。`,
  `//`,
  `// 仍需人工处理：`,
  `//   1. 收紧仍标着 AMBIGUOUS 的选择器（多数已自动加了作用域）`,
  `//   2. 删掉与意图无关的误操作步骤`,
  `//   3. 会产生数据的用例补上清理逻辑`,
  ``,
  `test(${JSON.stringify(name)}, async ({ page }) => {`,
  `  await page.goto(${JSON.stringify(strip(startUrl) || '/')});`,
];

steps.forEach((s, i) => {
  const hi = steps[i + 1]?.t ?? Infinity;
  const calls = between(s.t, hi);

  // 走到这一步时地址已经变了 —— 说明前面那串点击的净效果就是"导航到某个页面"。
  // 提示出来是因为菜单点击对 viewport 敏感：侧边栏深层菜单在小窗口下要滚动才可见，
  // 回放时 viewport 与录制时不同就点不到。直接 goto 没有这个问题。
  const prevUrl = i > 0 ? steps[i - 1]?.url : null;
  if (prevUrl && s.url && s.url !== prevUrl) {
    lines.push(`  //   ⇢ 地址已变为 ${s.url}`);
    lines.push(`  //     若前面几步只是为了导航到这里，可换成 page.goto(${JSON.stringify(s.url)})，`);
    lines.push(`  //     避开侧边栏菜单在小 viewport 下需要滚动才可见的问题。`);
  }
  const warn = s.ambiguous ? `   // ⚠ AMBIGUOUS: ${s.matches} 个元素匹配，回放时可能点错` : '';

  // CSS 兜底选择器基本都是关闭弹窗/提示这类「有就点、没有就跳过」的动作。
  // 生成成必经步骤会让脚本在弹窗不出现时直接失败。
  // iframe 里的元素必须先进 frame。用 src 片段定位 iframe：比 nth 稳，
  // 也比整条 src 宽容（src 常带随机 query）。
  const root = s.inFrame && s.framePath
    ? `page.frameLocator(${JSON.stringify(`iframe[src*="${s.framePath.split('/').pop()}"]`)})`
    : 'page';

  // ── 断言步骤 ──
  // expected 一律从录制数据里取，不在生成时重新推导：
  // 断言的意义就在于"当时人确认过的那个值"，生成器擅自改写等于把断言变成同义反复。
  if (s.type === 'assert') {
    const loc = `${root}.${s.sel}`;
    if (s.assertion === 'text') {
      lines.push(`  await expect(${loc}).toHaveText(${JSON.stringify(s.expected)});${warn}`);
    } else if (s.assertion === 'value') {
      lines.push(`  await expect(${loc}).toHaveValue(${JSON.stringify(s.expected)});${warn}`);
    } else if (s.assertion === 'visible') {
      lines.push(s.expected
        ? `  await expect(${loc}).toBeVisible();${warn}`
        : `  await expect(${loc}).toBeHidden();${warn}`);
    } else if (s.assertion === 'checked') {
      lines.push(s.expected
        ? `  await expect(${loc}).toBeChecked();${warn}`
        : `  await expect(${loc}).not.toBeChecked();${warn}`);
    } else if (s.assertion === 'attribute') {
      lines.push(`  await expect(${loc}).toHaveAttribute(${JSON.stringify(s.attribute)}, ${JSON.stringify(s.expected)});${warn}`);
    } else {
      lines.push(`  // ⚠ 未知断言类型 ${JSON.stringify(s.assertion)}，已跳过`);
    }
    for (const c of calls) lines.push(`  //   ↳ ${c.method} ${strip(c.url)} -> ${c.status}`);
    return;
  }

  // CSS 兜底基本都是关弹窗/提示条这类「有就点、没有就跳过」的动作。
  // 生成成必经步骤会让脚本在弹窗不出现时直接失败。
  if (s.kind === 'css' && s.type === 'click') {
    lines.push(`  // ⚠ CSS 兜底（元素没有 role/label/稳定文本），建议改用语义定位`);
    lines.push(`  {`);
    lines.push(`    const el = ${s.inFrame && s.framePath ? `page.frameLocator(${JSON.stringify(`iframe[src*="${s.framePath.split('/').pop()}"]`)})` : 'page'}.${s.sel};`);
    lines.push(`    if (await el.isVisible().catch(() => false)) await el.click();`);
    lines.push(`  }`);
    for (const c of calls) lines.push(`  //   ↳ ${c.method} ${strip(c.url)} -> ${c.status}`);
    return;
  }

  // ── 开关：拨到指定状态，而不是盲目点一下 ──
  // 只生成 click 的话，回放时初始状态若与录制时不同，就会朝反方向拨 ——
  // 脚本不报错，只是把开关拨错了。先读当前状态、需要时才点，这样重复跑也安全。
  if (s.type === 'switch') {
    const v = String(!!s.to);
    lines.push(`  {`);
    lines.push(`    const sw = ${root}.${s.sel};`);
    // 可点的是外层（有名字、点得动），状态常写在内层。点归点，读归读。
    let st = 'sw';
    if (s.via?.within) {
      lines.push(`    const state = sw.locator(${JSON.stringify(s.via.within)}).first();`);
      st = 'state';
    }
    if (s.via?.type === 'class') {
      // class 型开关：状态写在 class 上，没有 aria-checked 可读。
      // 按 aria 那套生成的话，getAttribute 恒为 null，于是每次都点、断言必挂。
      const t = JSON.stringify(s.via.token);
      const expr = s.via.polarity === 'off'
        ? `!e.classList.contains(${t})`
        : `e.classList.contains(${t})`;
      lines.push(`    const isOn = () => ${st}.evaluate((e) => ${expr});`);
      lines.push(`    if ((await isOn()) !== ${v}) await sw.click();`);
      lines.push(`    await expect.poll(isOn).toBe(${v});`);
    } else if (s.via?.type === 'checked') {
      lines.push(`    if ((await ${st}.isChecked()) !== ${v}) await sw.click();`);
      lines.push(`    await expect(${st}).${s.to ? 'toBeChecked()' : 'not.toBeChecked()'};`);
    } else {
      lines.push(`    if ((await ${st}.getAttribute('aria-checked')) !== ${JSON.stringify(v)}) await sw.click();`);
      lines.push(`    await expect(${st}).toHaveAttribute('aria-checked', ${JSON.stringify(v)});`);
    }
    lines.push(`  }${warn}`);
    for (const c of calls) {
      lines.push(`  //   ↳ ${c.method} ${strip(c.url)} -> ${c.status}`);
      if (c.status >= 400 && c.body) lines.push(`  //     ⚠ 失败响应: ${c.body.slice(0, 160).replace(/\s+/g, ' ')}`);
    }
    return;
  }

  const action =
    s.type === 'click' ? `await ${root}.${s.sel}.click()`
    : s.type === 'fill' && s.secret ? `await ${root}.${s.sel}.fill(process.env.REC_PASSWORD ?? '')`
    : s.type === 'fill' ? `await ${root}.${s.sel}.fill(${JSON.stringify(s.value ?? '')})`
    : s.type === 'check' ? `await ${root}.${s.sel}.check()`
    : s.type === 'uncheck' ? `await ${root}.${s.sel}.uncheck()`
    : s.type === 'press' ? `await ${root}.${s.sel}.press('Enter')`
    : null;
  if (!action) return;

  lines.push(...emitAction(action, calls, warn));
});

lines.push(`});`, ``);

  return lines.join('\n');
}
