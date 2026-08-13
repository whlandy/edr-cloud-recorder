import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * globalTeardown：删掉登录用例的失败现场
 *
 * 光在 config 里关 trace/video/screenshot 是不够的 —— Playwright 还会单独写一份
 * error-context.md，里面是失败时的页面快照，**包含输入框里的明文密码**。
 * 这条泄露路径不经过代码、不经过版本库，只是静静躺在 test-results/ 里，
 * 而且每次登录失败都会重新生成，靠人记得清理迟早会漏。
 *
 * 这里按目录名匹配 setup 项目的产物并整个删除。其他用例的现场保留 —— 它们不碰凭据。
 */
export default async function scrub() {
  const dir = path.join(__dirname, '..', 'test-results');
  if (!fs.existsSync(dir)) return;
  let removed = 0;
  for (const name of fs.readdirSync(dir)) {
    if (!/setup/i.test(name)) continue;
    fs.rmSync(path.join(dir, name), { recursive: true, force: true });
    removed++;
  }
  if (removed) console.log(`[teardown] 已清除 ${removed} 份登录用例现场（可能含明文凭据）`);
}
