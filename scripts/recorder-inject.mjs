/**
 * 页面内录制器 —— 会被 addInitScript 注入到每个页面（含每次导航后）
 *
 * 单独成文件有两个原因：一是 record.mjs 主流程更好读，二是可以被测试直接引用，
 * 不用把逻辑复制一份去测（复制出来的测试只能证明副本是对的）。
 */
export const RECORDER = () => {
  if (window.__rec) return;
  const steps = [];
  const txt = (e) => ((e && e.textContent) || '').replace(/\s+/g, ' ').trim();

  const roleOf = (e) => {
    const r = e.getAttribute?.('role');
    if (r) return r;
    const t = e.tagName.toLowerCase();
    if (t === 'button') return 'button';
    if (t === 'a' && e.getAttribute('href')) return 'link';
    if (t === 'textarea') return 'textbox';
    if (t === 'select') return 'combobox';
    if (t === 'input') {
      const ty = (e.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (['button', 'submit', 'reset'].includes(ty)) return 'button';
      return 'textbox';
    }
    return null;
  };

  const nameOf = (e) => {
    const al = (e.getAttribute?.('aria-label') || '').trim();
    if (al) return al;
    if (e.labels?.[0]) return txt(e.labels[0]);
    const ti = (e.getAttribute?.('title') || '').trim();
    if (ti) return ti;
    const t = txt(e);
    if (t && t.length <= 60) return t;
    return (e.getAttribute?.('placeholder') || '').trim();
  };

  const cssPath = (e) => {
    const p = [];
    let n = e;
    while (n && n.nodeType === 1 && p.length < 6) {
      let s = n.tagName.toLowerCase();
      // 运行时自增 id（如 tip_box_10059）每次加载都变，不能用来定位
      const stableId = n.id && !/\d{3,}/.test(n.id) ? n.id : null;
      if (stableId) { p.unshift(`${s}#${stableId}`); break; }
      const cls = typeof n.className === 'string'
        ? n.className.trim().split(/\s+/).filter((c) => c && !/^\d/.test(c) && !/\d{3,}/.test(c)).slice(0, 2)
        : [];
      if (cls.length) s += '.' + cls.join('.');
      const par = n.parentElement;
      if (par) {
        const sib = [...par.children].filter((x) => x.tagName === n.tagName);
        if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(n) + 1})`;
      }
      p.unshift(s);
      n = par;
    }
    return p.join(' > ');
  };

  // 统计「可见且文本恰好等于 t 的叶子元素」个数 —— 用来判断 getByText 会不会撞车。
  // 撞车的选择器在录制时能跑通（点的是当前那个），回放时却可能点到另一个，
  // 这种失败最难查，所以宁可在生成脚本时就标出来。
  const countText = (t) =>
    [...document.querySelectorAll('*')].filter(
      (x) => x.offsetParent && txt(x) === t && x.querySelectorAll('*').length === 0,
    ).length;

  /**
   * 为撞车的文本找一个作用域
   *
   * 向上逐层看：在这个祖先内部，目标文本是不是唯一？第一个满足的就是最小可用作用域。
   * 然后给这个祖先算定位方式，按可靠性排序：
   *   role（dialog / row / region…）+ 无障碍名  >  role 单独  >  标签 + 内部特征文本
   *
   * 最后一种是表格行的典型形态 —— page.locator('tr', { hasText: '张三' })，
   * 用行内某个全局唯一的文本把行钉住，再在行内找按钮。
   */
  const scopeFor = (el, name) => {
    let n = el.parentElement, depth = 0;
    while (n && n !== document.body && depth < 8) {
      const inside = [...n.querySelectorAll('*')].filter(
        (x) => x.offsetParent && txt(x) === name && x.querySelectorAll('*').length === 0,
      );
      if (inside.length === 1) {
        const role = n.getAttribute('role') || (n.tagName === 'TR' ? 'row' : n.tagName === 'DIALOG' ? 'dialog' : null);
        if (role) {
          const rn = (n.getAttribute('aria-label') || '').trim();
          if (rn) return `getByRole(${JSON.stringify(role)}, { name: ${JSON.stringify(rn)} })`;
          // 同 role 的容器只有一个时可以直接用 role 定位（弹窗最常见）
          if (document.querySelectorAll(`[role="${role}"], ${n.tagName.toLowerCase()}`).length === 1) {
            return `getByRole(${JSON.stringify(role)})`;
          }
        }
        // 找一段能把这个容器和同类容器区分开的文本
        const marker = [...n.querySelectorAll('*')]
          .filter((x) => x.offsetParent && x.querySelectorAll('*').length === 0)
          .map((x) => txt(x))
          .find((t) => t && t !== name && t.length <= 30 && countText(t) === 1);
        if (marker) {
          const tag = n.tagName.toLowerCase();
          return `locator(${JSON.stringify(tag)}, { hasText: ${JSON.stringify(marker)} })`;
        }
      }
      n = n.parentElement; depth++;
    }
    return null;
  };

  const selectorFor = (e) => {
    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = e.getAttribute?.(attr);
      if (v) return { kind: 'testid', code: `getByTestId(${JSON.stringify(v)})` };
    }
    // 文本输入框优先用 label / placeholder，而不是 role+name。
    // 原因：只有 placeholder 的输入框，其无障碍名恰好就是 placeholder，
    // 于是 role 分支会产出 getByRole('textbox', { name: '请输入用户名' })。
    // 那样能用，但一旦后来给它补了 <label>，无障碍名就变了，选择器随之失效 ——
    // 而 getByPlaceholder 只依赖 placeholder 本身，更稳也更直观。
    if (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') {
      if (e.labels?.[0]) {
        const lab = txt(e.labels[0]);
        if (lab) return { kind: 'label', code: `getByLabel(${JSON.stringify(lab)}, { exact: true })` };
      }
      const ph = (e.getAttribute('placeholder') || '').trim();
      if (ph) return { kind: 'placeholder', code: `getByPlaceholder(${JSON.stringify(ph)})` };
    }

    const role = roleOf(e);
    const name = nameOf(e);
    if (role && name) {
      return { kind: 'role', code: `getByRole(${JSON.stringify(role)}, { name: ${JSON.stringify(name)}, exact: true })` };
    }
    if (name) {
      const n = countText(name);
      if (n <= 1) return { kind: 'text', code: `getByText(${JSON.stringify(name)}, { exact: true })` };

      // 文本撞车。与其甩个 .first() 让人自己收拾，不如就地找一个作用域：
      // 向上找到「该文本在其内部唯一」的最近祖先，再为那个祖先算一个稳定的定位方式。
      // 这正是人工修选择器时会做的事（限定到某一行、某个弹窗），只是自动做了。
      const scoped = scopeFor(e, name);
      if (scoped) {
        return { kind: 'scoped', code: `${scoped}.getByText(${JSON.stringify(name)}, { exact: true })`, matches: n };
      }
      return {
        kind: 'text',
        code: `getByText(${JSON.stringify(name)}, { exact: true }).first()`,
        ambiguous: true, matches: n,
      };
    }
    return { kind: 'css', code: `locator(${JSON.stringify(cssPath(e))})` };
  };

  // 事件 target 常是内层 span/svg/i，往上找到真正「可操作」的那个元素
  const meaningful = (e) => {
    let n = e, d = 0;
    while (n && n !== document.body && d < 6) {
      const t = n.tagName.toLowerCase();
      if (['button', 'a', 'input', 'textarea', 'select'].includes(t) || n.getAttribute('role')) return n;
      const s = txt(n);
      if (s && s.length <= 40 && n.children.length <= 2) return n;
      n = n.parentElement; d++;
    }
    return e;
  };

  let seq = 0;
  const tag = Math.random().toString(36).slice(2, 8);

  const push = (type, el, extra) => {
    try {
      const t = meaningful(el);
      const sel = selectorFor(t);
      const step = {
        id: `${tag}-${++seq}`,
        t: Date.now(), type, sel: sel.code, kind: sel.kind,
        ambiguous: !!sel.ambiguous, matches: sel.matches,
        label: txt(t).slice(0, 60), css: cssPath(t),
        url: location.pathname + location.hash,
        ...extra,
      };

      // 双通道上报。
      //
      // 主通道 __recPush 由 Node 侧的 exposeBinding 提供，一产生就推走。
      // 这很重要：「操作完立即跳转」的步骤（最典型的就是点登录按钮）会在页面
      // 卸载时连同页面内的数组一起消失，靠轮询搬运必然丢。
      //
      // 副通道是本地数组，由 Node 侧定时 drain 兜底 —— 覆盖 binding 尚未注入
      // 完成的极早期，以及 binding 调用本身失败的情况。
      // 两个通道都带同一个 id，Node 侧按 id 去重。
      steps.push(step);
      if (typeof window.__recPush === 'function') {
        try { window.__recPush(step); } catch { /* 页面正在卸载，靠副通道 */ }
      }
    } catch { /* 录制出错绝不能影响用户操作 */ }
  };

  document.addEventListener('click', (e) => {
    // 复选框和单选框的 click 会紧跟一个 change，两个都记就会生成
    // 「先 click 再 check」这种连续操作同一元素的冗余步骤。
    // change 携带了勾选状态，信息更完整，所以 click 这边跳过它们。
    const el = e.target;
    if (el?.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) return;
    // 点 <label> 也会连带触发内部 input 的 change，同样跳过
    if (el?.closest?.('label')?.querySelector('input[type=checkbox],input[type=radio]')) return;
    push('click', el);
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (el.type === 'checkbox' || el.type === 'radio') {
      push(el.checked ? 'check' : 'uncheck', el);
    } else if (el.type === 'password') {
      // 步骤照记，值不记。生成的脚本用 process.env.REC_PASSWORD 代替 ——
      // 明文密码写进 spec 会让脚本绑死在一个账号上，而且 spec 通常是要进版本库的。
      push('fill', el, { secret: true });
    } else {
      push('fill', el, { value: String(el.value ?? '').slice(0, 200) });
    }
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') push('press', e.target, { key: 'Enter' });
  }, true);

  window.__rec = { steps, drain: () => steps.splice(0, steps.length) };
};
