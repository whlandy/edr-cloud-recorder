#!/usr/bin/env node
import assert from 'node:assert/strict';
import { generateSpec } from '../scripts/generate-spec.mjs';

const duplicate = generateSpec({
  startUrl: 'https://example.test/start',
  name: 'duplicate writes',
  steps: [{ t: 100, type: 'click', kind: 'text', sel: `getByText('Save')` }],
  net: [
    { id: 1, t: 110, phase: 'req', method: 'POST', url: 'https://example.test/api/item', body: '{"id":"first"}' },
    { id: 2, t: 120, phase: 'req', method: 'POST', url: 'https://example.test/api/item', body: '{"id":"second"}' },
    // 第二条请求先返回，验证生成器不依赖响应完成顺序。
    { requestId: 2, t: 130, phase: 'res', method: 'POST', url: 'https://example.test/api/item', status: 202 },
    { requestId: 1, t: 140, phase: 'res', method: 'POST', url: 'https://example.test/api/item', status: 201 },
  ],
});

assert.match(duplicate, /\+\+seen === 2/, '第二个同端点等待应只匹配第二条响应');
assert.match(duplicate, /waitForRequest/);
assert.match(duplicate, /expect\(resp1_1\?\.status\(\)\)\.toBe\(201\)/);
assert.match(duplicate, /expect\(resp1_2\?\.status\(\)\)\.toBe\(202\)/);
assert.match(duplicate, /"id": "first"[\s\S]*"id": "second"/, '响应应按 FIFO 关联各自的请求体');

const cssWrite = generateSpec({
  startUrl: 'https://example.test/',
  name: 'optional css write',
  steps: [{ t: 100, type: 'click', kind: 'css', sel: `locator('.icon-save')` }],
  net: [
    { t: 110, phase: 'req', method: 'PATCH', url: 'https://example.test/api/item/1', body: '{"enabled":true}' },
    { t: 120, phase: 'res', method: 'PATCH', url: 'https://example.test/api/item/1', status: 200 },
  ],
});

assert.match(cssWrite, /if \(await el\.isVisible\(\)[\s\S]*page\.waitForRequest[\s\S]*el\.click\(\)/,
  'CSS 兌底点击应在元素存在时等待写响应');
assert.match(cssWrite, /expect\(resp1\?\.status\(\)\)\.toBe\(200\)/);
assert.match(cssWrite, /req1\.postDataJSON\(\)\)\.toMatchObject/);

console.log('生成器回归测试通过');
