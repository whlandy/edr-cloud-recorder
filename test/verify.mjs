#!/usr/bin/env node
/**
 * 自检 —— 验证录制器确实按 SKILL.md 承诺的那样工作
 *
 * 造一个包含全部边界情况的页面（同名元素、自增 id、密码框、会发请求的按钮），
 * 用真实浏览器跑一遍，逐条断言。改动录制器后跑这个，比肉眼看输出可靠。
 *
 *   node test/verify.mjs
 */
import { execFileSync } from 'node:child_process';
import { generateSpec } from '../scripts/generate-spec.mjs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const raw = execFileSync('node', [path.join(dir, 'fixture-drive.mjs')], { encoding: 'utf-8', maxBuffer: 32 * 1024 * 1024 });
const { steps, net } = JSON.parse(raw);

const find = (p) => steps.find(p);
const checks = [];
const chk = (name, cond, evidence = '') => checks.push({ name, ok: !!cond, evidence: String(evidence) });

const testid = find((s) => s.sel.includes('save-btn'));
chk('data-testid 优先于其他方式', testid?.kind === 'testid', testid?.sel);

const input = find((s) => s.type === 'fill' && !s.secret);
chk('文本输入框用 getByPlaceholder', input?.kind === 'placeholder', input?.sel);
chk('输入的值被记录', input?.value === 'alice', JSON.stringify(input?.value));

const secret = find((s) => s.secret);
chk('密码框标记为 secret', !!secret, secret?.sel);
chk('密码明文未出现在记录里', !raw.includes('sup3rs3cret'), '全文搜索无明文');

const amb = find((s) => s.matches > 1);
chk('撞车文本被识别且计数正确', amb?.matches === 3, `${amb?.sel} → ${amb?.matches} 个匹配`);

const gen = find((s) => s.css?.includes('close_x'));
chk('运行时自增 id 未进入选择器', gen && !gen.css.includes('tip_box_10059'), gen?.css);

const cbs = steps.filter((s) => s.type === 'check' || s.type === 'uncheck' || s.sel.includes('checkbox'));
chk('勾选框只录一步（click 与 change 不重复）', cbs.length === 1 && cbs[0].type === 'check', `${cbs.length} 步`);

const btn = find((s) => s.label?.includes('提交订单'));
chk('按钮用 getByRole', btn?.kind === 'role', btn?.sel);

if (btn) {
  const next = steps.filter((s) => s.t > btn.t)[0];
  const hi = next ? next.t : Infinity;
  const calls = net.filter((n) => n.phase === 'res' && n.t >= btn.t && n.t < hi);
  chk('接口被关联到触发它的那一步', calls.some((c) => c.url.includes('/api/ok')), calls.map((c) => c.url.split('/').pop()).join(','));
}

const bad = net.find((n) => n.phase === 'res' && n.status >= 400);
chk('失败响应体被保留', bad?.body?.includes('subnetIdList'), bad?.body);

const okReq = net.find((n) => n.phase === 'req' && n.url.includes('/api/ok'));
chk('写请求的请求体被保留', okReq?.body === '{"a":1}', okReq?.body);

const okRes = net.find((n) => n.phase === 'res' && n.url.includes('/api/ok'));
chk('写请求的响应体被保留', !!okRes?.body, okRes?.body);

// 点完立刻跳转的步骤最容易丢：页面卸载时，还留在页面内数组里的记录就没了。
// 登录按钮是最典型的一例，所以这条单独验。
const navStep = find((s) => s.label?.includes('立刻跳转'));
chk('点击后立即跳转的步骤未丢失', !!navStep, navStep?.sel);
const afterNav = find((s) => s.sel.includes('after-nav'));
chk('跳转后新页面仍在录制', !!afterNav, afterNav?.sel);

// ── 生成器：把接口调用变成断言、给撞车的文本加作用域 ──
const spec = generateSpec({ steps, net, startUrl: 'http://127.0.0.1/', name: 'gen-check' });

chk('写请求生成状态码断言', /expect\(resp\d+\?\.status\(\)\)\.toBe\(200\);/.test(spec), spec.match(/expect\(resp\d+\?\.status\(\)\)\.toBe\(\d+\);/)?.[0]);
chk('写请求生成请求体断言', spec.includes('postDataJSON()).toMatchObject('), spec.match(/"a": 1/) ? '含捕获到的字段' : '');
chk('响应变量名不重复', (() => {
  const names = [...spec.matchAll(/const (resp[\w]*) =/g)].map((m) => m[1]);
  return names.length === new Set(names).size;
})(), [...spec.matchAll(/const (resp[\w]*) =/g)].map((m) => m[1]).join(','));
chk('GET 不生成断言，仍是注释', !/waitForRequest[^\n]*"GET"/.test(spec), 'GET 保持注释');

const dele = steps.find((s) => s.label === '删除');
chk('撞车文本自动加作用域（不再是 .first()）', dele && dele.kind === 'scoped' && !dele.sel.includes('.first()'), dele?.sel);

let passed = 0;
console.log(`\n  ${'检查项'.padEnd(34)}结果`);
console.log('  ' + '-'.repeat(86));
for (const c of checks) {
  console.log(`  ${c.name.padEnd(34)}${c.ok ? '✅' : '❌'}  ${c.evidence ?? ''}`);
  if (c.ok) passed++;
}
console.log('  ' + '-'.repeat(86));
console.log(`  ${passed}/${checks.length} 通过\n`);
process.exit(passed === checks.length ? 0 : 1);
