import { test as base, expect, Page, Locator } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// package.json 里 type=module，Playwright 按 ESM 处理 TS，__dirname 不存在
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const AUTH_DIR = path.join(__dirname, '..', '.auth');
const STORAGE_STATE = path.join(AUTH_DIR, 'state.json');
const SESSION_STORAGE = path.join(AUTH_DIR, 'session-storage.json');

/**
 * authedPage —— 带完整登录态的 page
 *
 * storageState 负责 cookies + localStorage；sessionStorage 得靠 addInitScript
 * 手动注回去，而且必须在**页面脚本执行之前** —— 换成 page.evaluate() 就晚了，
 * 那时应用已经判定未登录并开始跳转。
 */
export const test = base.extend<{ authedPage: Page }>({
  storageState: fs.existsSync(STORAGE_STATE) ? STORAGE_STATE : undefined,

  authedPage: async ({ page }, use) => {
    if (fs.existsSync(SESSION_STORAGE)) {
      const raw = fs.readFileSync(SESSION_STORAGE, 'utf-8');
      await page.addInitScript((data: string) => {
        try {
          for (const [k, v] of Object.entries(JSON.parse(data) as Record<string, string>)) {
            try { sessionStorage.setItem(k, v); } catch { /* 只读键或超配额 */ }
          }
        } catch { /* 文件损坏时不要拖垮整个用例 */ }
      }, raw);
    }
    await use(page);
  },
});

export { expect };

/* ────────── 通用助手 ────────── */

/**
 * 点击可选元素 —— 存在才点，不存在直接跳过。
 *
 * 首次引导、公告、提示条这类元素出现与否取决于账号状态和历史操作。
 * 录制时它出现了，回放时可能不出现；当成必经步骤会在等待时超时。
 *
 * 注意 .catch(() => false)：元素不存在时 isVisible() 会抛错，
 * 不接住的话容错逻辑本身会变成失败点。
 */
export async function clickIfPresent(target: Locator): Promise<boolean> {
  if (await target.isVisible().catch(() => false)) {
    await target.click().catch(() => {});
    return true;
  }
  return false;
}

/**
 * 关掉首启弹窗，并等到没有遮罩挡路为止
 *
 * 这一步是回放稳定性的分水岭，原因不直观：
 *
 * 1. **弹窗常常不止一个。** 实测某控制台首启会叠两个（校验码 + 校验码历史），
 *    各带一层遮罩。只关一个，剩下那层照样吞掉后面所有点击。
 * 2. **遮罩关掉后还会残留一会儿**，而且它拦截点击时，Playwright 认为
 *    click «成功了» —— 失败会报在后面某个 waitForResponse 上，
 *    看着像「接口没发」，实际是「点了没进去」。今天为此绕了三圈。
 * 3. **遮罩常驻 DOM，只靠 CSS 隐藏**，所以判「可见数为 0」，
 *    判 toHaveCount(0) 会永远等不到。
 *
 * 关闭按钮的 class 因组件库而异，用 selectors 参数覆盖。
 */
export async function dismissOverlays(
  page: Page,
  selectors: string[] = ['span.eui_Dialog_closeIcon', '.eui_tipBox_close', '[class*="closeIcon"]'],
  masks = '.eui-dialog-masking, .eui_Dialog_Over, [class*="masking"], [class*="Dialog_Over"]',
): Promise<void> {
  const closers = page.locator(selectors.join(', '));
  // 等第一个弹窗出现再动手：紧跟 goto 就问「在不在」，那一刻页面还是空的，
  // 「存在才点」会静默返回 false —— 弹窗没关掉，而失败要到很后面才暴露。
  await closers.first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => {});

  for (let i = 0; i < 5; i++) {
    const btn = closers.filter({ visible: true }).first();
    if (!(await btn.isVisible().catch(() => false))) break;
    await btn.click().catch(() => {});
    await page.waitForTimeout(400);
  }

  await expect
    .poll(() => page.locator(masks).filter({ visible: true }).count(),
      { timeout: 15_000, message: '遮罩层一直没消失，后续点击会被它拦住' })
    .toBe(0)
    .catch(() => { /* 没有遮罩的站点直接过 */ });
}

/**
 * 执行一个带二次确认的操作，并返回实际发出的请求。
 *
 * 漏掉确认弹窗是「静默通过」假测试的头号来源：脚本点了「删除」，断言也过了，
 * 但因为没点「确认」，其实什么都没发生。把两步绑在一起，调用方就没机会忘。
 *
 * 返回请求体和状态码，这样用例可以断言接口契约而不只是界面文字 ——
 * 界面文案会改，接口契约不会轻易改。
 */
export async function confirmAndCapture(
  page: Page,
  opts: {
    trigger: Locator;
    confirmName?: string | RegExp;
    urlPattern: string | RegExp;
    method?: string;
  },
): Promise<{ status: number; requestBody: any; responseBody: string }> {
  const { trigger, confirmName = /确认|确定|OK/, urlPattern, method = 'POST' } = opts;

  const match = (url: string) =>
    typeof urlPattern === 'string' ? url.includes(urlPattern) : urlPattern.test(url);

  const reqP = page.waitForRequest((r) => r.method() === method && match(r.url()));
  const resP = page.waitForResponse((r) => r.request().method() === method && match(r.url()));

  await trigger.click();
  await page.getByRole('button', { name: confirmName }).click();

  const [req, res] = await Promise.all([reqP, resP]);
  return {
    status: res.status(),
    requestBody: req.postData() ? JSON.parse(req.postData()!) : null,
    responseBody: await res.text().catch(() => ''),
  };
}

/**
 * 读取某个资源的原始响应文本 —— 用于基线快照。
 *
 * 返回文本而不是解析后的对象，因为比对必须逐字节做：字段顺序、数字精度、
 * 空值表示（null vs 缺失）的差异，只有字节比较才抓得住。
 */
export async function snapshot(page: Page, url: string): Promise<string> {
  return page.evaluate(async (u) => {
    const r = await fetch(u);
    return r.text();
  }, url);
}
