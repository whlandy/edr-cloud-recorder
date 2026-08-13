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
const { steps, net, emptyGuard } = JSON.parse(raw);

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

chk('写请求生成状态码断言', /expect\(resp\d+\.status\(\)\)\.toBe\(200\);/.test(spec), spec.match(/expect\(resp\d+\.status\(\)\)\.toBe\(\d+\);/)?.[0]);
chk('写请求生成请求体断言', spec.includes('postDataJSON()).toMatchObject('), spec.match(/"a": 1/) ? '含捕获到的字段' : '');
chk('响应变量名不重复', (() => {
  const names = [...spec.matchAll(/const \[(resp[\w]*)\]/g)].map((m) => m[1]);
  return names.length === new Set(names).size;
})(), [...spec.matchAll(/const \[(resp[\w]*)\]/g)].map((m) => m[1]).join(','));
chk('GET 不生成断言，仍是注释', !/waitForResponse[^\n]*"GET"/.test(spec), 'GET 保持注释');

const dele = steps.find((s) => s.label === '删除');
chk('撞车文本自动加作用域（不再是 .first()）', dele && dele.kind === 'scoped' && !dele.sel.includes('.first()'), dele?.sel);

const frameStep = find((s) => s.value === 'frame-user');
chk('iframe 内的操作被标记归属', frameStep?.inFrame === true, `framePath=${frameStep?.framePath}`);
chk('生成 frameLocator 而非直接 page', spec.includes('frameLocator('), spec.match(/page\.frameLocator\([^)]*\)/)?.[0]);

// ── 开关与浮层 ──
const swStep = steps.find((s) => s.type === 'switch');
chk('开关录成 switch 步骤而非普通 click', !!swStep, swStep?.sel);
chk('开关记录了目标状态', swStep?.to === true, `to=${swStep?.to}`);
chk('开关用 getByRole(switch)', swStep?.kind === 'role' && swStep.sel.includes('switch'), swStep?.sel);
chk('生成状态感知的拨动而非盲点',
  /getAttribute\('aria-checked'\)/.test(spec) && /toHaveAttribute\('aria-checked'/.test(spec),
  spec.match(/if \(\(await sw[^\n]*/)?.[0]?.slice(0, 70));

// 整行可点时，开关是点击目标的后代而不是祖先。只向上找会漏掉，
// 退化成盲点击 —— 回放时朝反方向拨，脚本不报错但把开关设错了。
const rowSw = steps.find((s) => s.type === 'switch' && s.label === '行内自保护');
chk('后代开关也能被识别（整行可点的情形）', !!rowSw, rowSw?.sel);
chk('记录了状态是靠 class 表达的', rowSw?.via?.type === 'class' && rowSw.via.token === 'toggled',
  `${rowSw?.via?.type}:${rowSw?.via?.token}`);
chk('记录了状态在内层哪一级', rowSw?.via?.within === '.eui_toggle_container', rowSw?.via?.within);
chk('class 型开关生成 classList 读法而非 aria-checked',
  /classList\.contains\("toggled"\)/.test(spec) && /expect\.poll\(isOn\)/.test(spec),
  spec.match(/const isOn[^\n]*/)?.[0]);
chk('点外层、读内层',
  /const state = sw\.locator\("\.eui_toggle_container"\)/.test(spec) &&
  /const isOn = \(\) => state\.evaluate/.test(spec) && /await sw\.click\(\)/.test(spec),
  spec.match(/const state = sw[^\n]*/)?.[0]);

// testid 本该最稳，但组件框架常批量吐出同一个值。不验唯一性就会生成
// 命中几百个元素的 getByTestId，回放必然 strict mode 失败。
const dupTid = steps.find((s) => s.label?.includes('重复标记甲'));
chk('不唯一的 testid 不被采用', dupTid && !dupTid.sel.includes('getByTestId'), dupTid?.sel);
const cyStep = steps.find((s) => s.label?.includes('仅有 data-cy'));
chk('data-cy 生成属性选择器而非 getByTestId',
  cyStep?.sel.includes('[data-cy=') && !cyStep.sel.includes('getByTestId'), cyStep?.sel);

// 浮层里的选项与触发器同名（两处都叫 Windows系统），必须靠浮层作用域区分。
// 注意按 css 找选项那一步 —— 只按 label 找会先命中触发器。
const optStep = steps.find((s) => s.type === 'click' && (s.css || '').includes('#pop'));
chk('浮层选项识别到撞车', optStep?.matches === 2, `matches=${optStep?.matches}`);
chk('浮层选项被限定作用域，不是 .first()',
  optStep?.kind === 'scoped' && !optStep.sel.includes('.first()'), optStep?.sel);
const trigStep = steps.find((s) => s.type === 'click' && (s.css || '').includes('#trigger'));
chk('触发器本身不受影响', trigStep?.kind === 'text', trigStep?.sel);

// ── 断言菜单 ──
const asserts = steps.filter((s) => s.type === 'assert');
chk('右键能添加断言', asserts.length === 3, `${asserts.length} 条`);

const textA = asserts.find((s) => s.assertion === 'text');
chk('expected 保存的是用户改过的值，不是元素当前值',
  textA?.expected === '用户确认过的值', JSON.stringify(textA?.expected));

chk('expected 为空时提交被禁用', emptyGuard?.blocked === true, `disabled=${emptyGuard?.blocked}`);
chk('勾选「允许空值」后可提交', emptyGuard?.unblocked === true, `enabled=${emptyGuard?.unblocked}`);

const checkedA = asserts.find((s) => s.assertion === 'checked');
chk('checked 断言用布尔 expected', typeof checkedA?.expected === 'boolean', String(checkedA?.expected));

const visA = asserts.find((s) => s.assertion === 'visible');
chk('visible 可以显式断言 false', visA?.expected === false, String(visA?.expected));

chk('生成 toHaveText 且带 expected',
  spec.includes('toHaveText("用户确认过的值")'), spec.match(/toHaveText\([^)]*\)/)?.[0]);
chk('visible=false 生成 toBeHidden', spec.includes('toBeHidden()'), spec.match(/toBe(Visible|Hidden)\(\)/)?.[0]);
chk('checked=false 生成 not.toBeChecked',
  /not\.toBeChecked\(\)|toBeChecked\(\)/.test(spec), spec.match(/(not\.)?toBeChecked\(\)/)?.[0]);

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
