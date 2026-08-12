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

  // Playwright 的 response 能直接拿到其 request，但落盘后的录制数据没有对象引用。
  // 按 method + URL 为每条请求维护 FIFO 队列，恢复响应与请求的一一对应关系。
  // 不能简单找“响应之前最后一条同 URL 请求”：同一操作并发发两次相同请求时，
  // 那会让两个响应都错误地关联到第二条请求。
  const requestOf = new Map();
  const requestsById = new Map(net.filter((event) => event.phase === 'req' && event.id != null).map((event) => [event.id, event]));
  const pending = new Map();
  for (const event of [...net].sort((a, b) => a.t - b.t || (a.phase === 'req' ? -1 : 1))) {
    const key = `${event.method}\n${event.url}`;
    if (event.phase === 'req') {
      const queue = pending.get(key) ?? [];
      queue.push(event);
      pending.set(key, queue);
    } else if (event.phase === 'res') {
      const req = requestsById.get(event.requestId) ?? pending.get(key)?.shift();
      if (req) requestOf.set(event, req);
    }
  }

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
  return JSON.stringify(v);
};

// 为某个响应找回它对应的请求体
  const reqBodyOf = (res) => {
  const r = requestOf.get(res);
  if (!r?.body) return null;
  try { return JSON.parse(r.body); } catch { return null; }
};

// 生成一次操作的代码：若该操作触发了写请求，就把它包成「等待响应 + 断言」
  let respSeq = 0;
  const emitAction = (actionCode, calls, warn) => {
  // 按请求发出次序生成等待，而不是响应完成次序；并发请求可能后发先回。
  const writes = calls
    .filter((c) => c.method !== 'GET' && c.status < 400)
    .sort((a, b) => (requestOf.get(a)?.t ?? a.t) - (requestOf.get(b)?.t ?? b.t));
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
  // 同一步可能向同一端点并发发送多次请求。相同 predicate 的 waitForRequest
  // 都会命中第一条请求，因此为每个 method + path 计算序号，让第 N 个等待只接第 N 条。
  const occurrences = new Map();
  const reqNames = names.map((name) => name.replace(/^resp/, 'req'));
  out.push(`  const [${reqNames.join(', ')}] = await Promise.all([`);
  for (const w of writes) {
    const p = strip(w.url).split('?')[0];
    const key = `${w.method}\n${p}`;
    const nth = (occurrences.get(key) ?? 0) + 1;
    occurrences.set(key, nth);
    const predicate = `r.url().includes(${JSON.stringify(p)}) && r.method() === ${JSON.stringify(w.method)}`;
    if (nth === 1) {
      out.push(`    page.waitForRequest((r) => ${predicate}),`);
    } else {
      out.push(`    (() => { let seen = 0; return page.waitForRequest((r) => ${predicate} && ++seen === ${nth}); })(),`);
    }
  }
  out.push(`    ${actionCode.replace(/^await /, '')},`);
  out.push(`  ]);${warn}`);
  for (let i = 0; i < writes.length; i++) {
    out.push(`  const ${names[i]} = await ${reqNames[i]}.response();`);
    out.push(`  expect(${names[i]}?.status()).toBe(${writes[i].status});`);
    const body = reqBodyOf(writes[i]);
    if (body && typeof body === 'object') {
      out.push(`  expect(${reqNames[i]}.postDataJSON()).toMatchObject(${toMatcher(body)});`);
    }
  }
  for (const c of calls.filter((c) => c.method === 'GET')) {
    out.push(`  //   ↳ GET ${strip(c.url)} -> ${c.status}`);
  }
  return out;
};

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
  const warn = s.ambiguous ? `   // ⚠ AMBIGUOUS: ${s.matches} 个元素匹配，回放时可能点错` : '';

  // CSS 兜底选择器基本都是关闭弹窗/提示这类「有就点、没有就跳过」的动作。
  // 生成成必经步骤会让脚本在弹窗不出现时直接失败。
  // CSS 兜底基本都是关弹窗/提示条这类「有就点、没有就跳过」的动作。
  // 生成成必经步骤会让脚本在弹窗不出现时直接失败。
  if (s.kind === 'css' && s.type === 'click') {
    lines.push(`  // ⚠ CSS 兜底（元素没有 role/label/稳定文本），建议改用语义定位`);
    lines.push(`  {`);
    lines.push(`    const el = page.${s.sel};`);
    lines.push(`    if (await el.isVisible().catch(() => false)) {`);
    // 等确认元素存在后再建立响应等待，避免可选弹窗未出现时空等到超时。
    for (const line of emitAction('await el.click()', calls, warn)) {
      lines.push(`  ${line}`);
    }
    lines.push(`    }`);
    lines.push(`  }`);
    return;
  }

  const action =
    s.type === 'click' ? `await page.${s.sel}.click()`
    : s.type === 'fill' && s.secret ? `await page.${s.sel}.fill(process.env.REC_PASSWORD ?? '')`
    : s.type === 'fill' ? `await page.${s.sel}.fill(${JSON.stringify(s.value ?? '')})`
    : s.type === 'check' ? `await page.${s.sel}.check()`
    : s.type === 'uncheck' ? `await page.${s.sel}.uncheck()`
    : s.type === 'press' ? `await page.${s.sel}.press('Enter')`
    : null;
  if (!action) return;

  lines.push(...emitAction(action, calls, warn));
});

lines.push(`});`, ``);

  return lines.join('\n');
}
