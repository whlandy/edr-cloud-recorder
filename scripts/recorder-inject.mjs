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
      return {
        kind: 'text',
        code: `getByText(${JSON.stringify(name)}, { exact: true })${n > 1 ? '.first()' : ''}`,
        ambiguous: n > 1, matches: n,
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

  const push = (type, el, extra) => {
    try {
      const t = meaningful(el);
      const sel = selectorFor(t);
      steps.push({
        t: Date.now(), type, sel: sel.code, kind: sel.kind,
        ambiguous: !!sel.ambiguous, matches: sel.matches,
        label: txt(t).slice(0, 60), css: cssPath(t),
        url: location.pathname + location.hash,
        ...extra,
      });
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
