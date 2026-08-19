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

  // 作用域锚点**不能用时间、数字 id 这类会变的值**。实测栽过：一行合规检查
  // 结果被锚在 hasText: "2026-08-14 10:22:29" 上，当时跑三遍全绿，几小时后
  // 重跑、时间推进，整条用例就再也找不到那一行了。
  // 这类失败最坏的地方在于**延迟发作**：录完当场验证不出来。
  /**
   * 填进输入框的值如果是个日期，记下它相对**录制当天**的偏移
   *
   * 日期筛选框里填死值的脚本不会报错，只会悄悄查错区间：录制那天
   * "2026-08-09" 是「9 天前」，下个月回放就成了「40 天前」。用例照样绿，
   * 只是查的已经不是当初那个区间了 —— 这类错不报错，最难发现。
   *
   * 这里只记事实（是不是日期、差几天），要不要按相对值回放由生成器决定，
   * 字面量也原样保留，随时能钉回去。
   */
  const dateValueMeta = (value) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value).trim());
    if (!m) return {};
    const picked = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    if (Number.isNaN(picked.getTime())) return {};
    const today = new Date();
    // 用 Date.UTC 给日历日编号，不要用「本地零点 / 86400000」。
    //
    // 后者在 UTC+0/+1 的时区跨夏令时会算错：伦敦 BST 期间本地零点落到**前一个
    // UTC 日**，floor 就跨错了格。全年逐日扫描实测 Europe/London
    // 2026-03-30 算出相差 0 天、2026-10-26 算出相差 2 天（都应为 1）。
    // 纽约（UTC−5/−4）和上海（恒定 UTC+8）零点始终在同一 UTC 日内，
    // 是**运气**躲过去的，不是算法对。
    //
    // Date.UTC 只取年月日，与时区和夏令时完全无关。
    const dayMs = 86400000;
    const dayNo = (d) => Math.floor(
      Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / dayMs);
    const offsetDays = dayNo(picked) - dayNo(today);
    // 差得太离谱的多半不是「相对今天」的意思（生日、固定归档日）
    if (Math.abs(offsetDays) > 366) return {};
    return { valueFrom: { kind: 'localtime', format: '%Y-%m-%d', offsetDays } };
  };

  const volatileMarker = (t) =>
    /\d{4}-\d{2}-\d{2}/.test(t) ||           // 2026-08-14
    /\d{1,2}:\d{2}(:\d{2})?/.test(t) ||      // 10:22:29
    /\d{6,}/.test(t) ||                      // 雪花 id / 时间戳
    /^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(t) ||  // UUID
    // 度量值：token 数、耗时、占比、计数。它们不含日期也不够长，
    // 前面几条一个都拦不住 —— 实测 "96.1K" 就这么被选成了行锚，
    // 而它是一行请求记录的 token 计数，换个查询区间这一行就不存在了。
    //
    // 判据是「整串就是一个数（可带 K/M/G/B、%、单位后缀或千分位）」。
    // 只要还夹着别的字（"3 个任务"、"maa-fw"）就不算 —— 那种串里
    // 真正起区分作用的是文字部分。
    /^[+-]?[\d,]+(\.\d+)?\s*(%|[KMGTB]i?[Bb]?|ms|s|min|h|次|个|条|项)?$/.test(t.trim());

  /**
   * 开关识别
   *
   * 自研 UI 库的开关多半是 div/span 加个 class，不是 input[type=checkbox]：
   * 没有 role、没有文本，一路掉到 CSS 兜底，选择器又脆又看不懂。
   * 而且只录一次 click 的话，回放时若初始状态与录制时不同，就会朝反方向拨——
   * 脚本不报错，只是把开关拨错了，这种失败最难查。
   *
   * 所以这里既认它是开关，也读出它的当前状态，交给生成器产出"拨到指定状态"的代码。
   */
  const switchInfo = (e) => {
    if (!e || e.nodeType !== 1) return null;
    const role = e.getAttribute('role');
    const aria = e.getAttribute('aria-checked');
    const cls = typeof e.className === 'string' ? e.className : '';
    const looksLikeSwitch =
      role === 'switch' || aria !== null ||
      /(^|[-_ ])(switch|toggle)([-_ ]|$)/i.test(cls);
    if (!looksLikeSwitch) return null;

    // via 记录「状态是怎么表达的」，生成器据此产出对应的读法。
    // 不记的话生成器只能假定 aria-checked —— 对 class 型开关必然读不到状态，
    // 于是每次都点、每次断言都挂。
    let on = null, via = null;
    if (aria !== null) { on = aria === 'true'; via = { type: 'aria' }; }
    else if (typeof e.checked === 'boolean') { on = e.checked; via = { type: 'checked' }; }
    else {
      // 没有 aria 的自研开关，多数用 class 表示状态。toggled 是实测遇到的写法：
      // <div class="eui_toggle_container toggled">。认不出来就留 null，
      // 由生成器退化成普通点击，而不是猜一个可能相反的状态。
      const onHit = cls.match(/(^|[-_ ])(checked|active|on|selected|toggled|opened)([-_ ]|$)/i);
      const offHit = cls.match(/(^|[-_ ])(unchecked|inactive|off|untoggled|closed)([-_ ]|$)/i);
      if (onHit) { on = true; via = { type: 'class', token: onHit[2], polarity: 'on' }; }
      else if (offHit) { on = false; via = { type: 'class', token: offHit[2], polarity: 'off' }; }
    }

    return { on, via, name: nameOf(e) };
  };

  /** 点击目标内部所有像开关的元素（外壳、容器、轨道、滑块都可能算） */
  const switchInside = (e) => [...(e.querySelectorAll?.('*') ?? [])].filter((x) => switchInfo(x));

  /**
   * 找开关本体
   *
   * 先往上：点在滑块/轨道上时，开关是事件目标的祖先。
   * 再往下：很多表单把「标签 + 说明文字 + 开关」放在一整行里，整行都可点，
   * 这时事件目标是行容器，开关是它的**后代** —— 只向上找会漏掉，退化成盲点击，
   * 回放时朝反方向拨。要求行内开关唯一，多于一个就不猜是哪个。
   */
  const switchChain = (e) => {
    const chain = [];
    let n = e, d = 0;
    while (n && n !== document.body && d < 4) {
      if (switchInfo(n)) chain.push(n);
      n = n.parentElement; d++;
    }
    return chain;
  };

  const switchAncestor = (e) => {
    // 开关组件是嵌套的：外壳 / 容器 / 轨道 / 滑块每层都带 toggle 字样，但状态
    // 只写在其中一层。点在滑块上时，撞到的第一层就是滑块 —— 它永远读不出状态，
    // 于是整步退化成盲点击，回放时朝哪个方向拨取决于当时的状态，且不报错。
    // 实测这就是「拨开关的步骤大多是盲点」的原因。所以要在整条链里挑读得出
    // 状态的那一层，而不是撞到的第一层。
    const up = switchChain(e).filter((x) => switchInfo(x).on !== null);
    if (up.length) return { el: up[0], info: switchInfo(up[0]) };
    // 再往下：很多表单把「标签 + 说明 + 开关」放一整行，整行可点，
    // 这时开关是事件目标的后代。要求唯一，多于一个就不猜是哪个。
    const down = switchInside(e).filter((x) => switchInfo(x).on !== null);
    if (down.length === 1) return { el: down[0], info: switchInfo(down[0]) };
    // 一层都读不出状态：交给点击后的观察器去看哪一层的 class 变了
    return null;
  };

  const roleOf = (e) => {
    const r = e.getAttribute?.('role');
    if (r) return r;
    // aria-checked 存在但没写 role 的，按 switch 处理 —— getByRole('switch') 找得到
    if (e.getAttribute?.('aria-checked') !== null && e.getAttribute?.('aria-checked') !== undefined) return 'switch';
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
      // 运行时自增 id（如 tip_box_10059）每次加载都变，不能用来定位。
      //
      // 而且**不能假定 id 唯一**：实测一个页面上叠了两个弹窗，各自都
      // id="dialog_panel"（HTML 上不合法，现实里就这么写）。以前遇到 id 就
      // 短路返回，产出的路径命中 2 个元素 —— 录制时能跑通，回放必然
      // strict mode 报错，而且视觉回退同样分不清那两个一模一样的图标。
      const stableId = n.id && !/\d{3,}/.test(n.id)
        && document.querySelectorAll(`[id="${CSS.escape(n.id)}"]`).length === 1
        ? n.id : null;
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

  // 统计「文本恰好等于 t 的叶子元素」个数 —— 用来判断 getByText 会不会撞车。
  // 撞车的选择器在录制时能跑通（点的是当前那个），回放时却可能点到另一个，
  // 这种失败最难查，所以宁可在生成脚本时就标出来。
  //
  // 这里**不能**过滤可见性。实测 Playwright 各定位器对隐藏元素的态度并不一致：
  //   getByText / getByPlaceholder / getByLabel / getByTestId → 匹配隐藏元素
  //   getByRole                                              → 走无障碍树，不匹配
  // 只数可见元素的话，「触发器文本」与「收起的浮层里同名选项」会被算成 1 个，
  // 于是产出不带作用域的 getByText —— 回放时 strict mode 必然报
  // "resolved to 2 elements"，而录制当时一切正常。
  // （另外 offsetParent 本身也是坏判据：position:fixed 的可见元素它判为 null。）
  const countText = (t) =>
    [...document.querySelectorAll('*')].filter(
      (x) => txt(x) === t && x.querySelectorAll('*').length === 0,
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
  /**
   * 判断元素是否在浮层里（下拉、菜单、弹窗、气泡）
   *
   * 下拉选项常渲染在 portal 中——挂到 body 底下而不是触发器旁边。这带来两个问题：
   *   1. 往上找作用域会找到一个和业务无关的容器
   *   2. 触发器显示的值和选项文本往往一模一样，直接撞车
   * 认出浮层后把作用域限定到它，两个问题一起解决。
   */
  const FLOATING_ROLES = ['listbox', 'menu', 'dialog', 'tooltip', 'combobox', 'tree', 'grid'];
  const floatingAncestor = (el) => {
    let n = el, d = 0;
    while (n && n !== document.body && d < 10) {
      const role = n.getAttribute?.('role');
      if (role && FLOATING_ROLES.includes(role)) return { el: n, role };
      const st = getComputedStyle(n);
      // 绝对/固定定位 + 明显的层级，是浮层最通用的特征
      if ((st.position === 'fixed' || st.position === 'absolute') &&
          Number(st.zIndex) >= 100) return { el: n, role: null };
      n = n.parentElement; d++;
    }
    return null;
  };

  const scopeFor = (el, name) => {
    // 浮层优先：它天然把选项和页面其他同名文本隔开
    const fl = floatingAncestor(el);
    if (fl) {
      // 同 countText：作用域内的唯一性也要按「含隐藏元素」来数，
      // 否则作用域看着够用，回放时被隐藏的同名元素撞掉
      const inside = [...fl.el.querySelectorAll('*')].filter(
        (x) => txt(x) === name && x.querySelectorAll('*').length === 0);
      if (inside.length === 1) {
        if (fl.role && document.querySelectorAll(`[role="${fl.role}"]`).length === 1) {
          return `getByRole(${JSON.stringify(fl.role)})`;
        }
        const cls = typeof fl.el.className === 'string'
          ? fl.el.className.trim().split(/\s+/)
              .filter((c) => c && !/\d{3,}/.test(c) && !/^\d/.test(c))[0]
          : null;
        if (cls) return `locator(${JSON.stringify('.' + cls)})`;
      }
    }

    let n = el.parentElement, depth = 0;
    while (n && n !== document.body && depth < 8) {
      const inside = [...n.querySelectorAll('*')].filter(
        (x) => txt(x) === name && x.querySelectorAll('*').length === 0,
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
        // 找一段能把这个容器和同类容器区分开的文本。
        //
        // 但**不能用时间、数字 id 这类会变的值**。实测栽过：一行合规检查结果被
        // 锚在 hasText: "2026-08-14 10:22:29" 上，当时跑三遍全绿，几小时后检查
        // 重跑、时间推进，整条用例就再也找不到那一行了。
        // 这类失败最坏的地方在于**延迟发作**：录完当场验证不出来。
        const marker = [...n.querySelectorAll('*')]
          .filter((x) => x.offsetParent && x.querySelectorAll('*').length === 0)
          .map((x) => txt(x))
          .find((t) => t && t !== name && t.length <= 30 && !volatileMarker(t) && countText(t) === 1);
        if (marker) {
          // 锚文本唯一 ≠ 作用域唯一。
          //
          // hasText 是按**子树文本**匹配的：锚再唯一，所有包含它的祖先也全都命中。
          // 实测 locator("div", { hasText: "统计" }) 撞了 2 个 —— 侧栏那层和
          // 页面标题那层，回放直接 strict mode violation。
          //
          // 失败方式还特别隐蔽：视觉回退会把它接住，于是用例照样绿，只是每步
          // 多花约 900ms 做全屏匹配；等哪天模板也失效了，报出来的是「视觉匹配
          // 分数不足」，真正的原因（选择器早就废了）已经被埋了两层。
          //
          // 所以这里要求作用域选择器**自己**在全页只命中一个。先用类名收紧，
          // 不行就放弃这个作用域，让外层继续往上找。
          // hasText 传字符串时是**大小写不敏感的子串匹配**（实测：
          // locator('div.a', {hasText:'data center'}) 命中 'DATA CENTER'）。
          // 这里用大小写敏感的 includes 会少数，于是把实际有歧义的作用域
          // 判成唯一 —— 回放照样 strict mode 报错，和这段代码本来要防的
          // 是同一个毛病。
          //
          // 注意 countText 那边**不能**跟着改：它对应的是
          // getByText(exact:true)，而 exact 匹配是大小写敏感的。
          const needle = marker.toLowerCase();
          const scopeHits = (css) => {
            try {
              return [...document.querySelectorAll(css)].filter(
                (x) => txt(x).toLowerCase().includes(needle)).length;
            } catch { return 0; }
          };
          const tag = n.tagName.toLowerCase();
          const cls = typeof n.className === 'string'
            ? n.className.trim().split(/\s+/)
                .filter((c) => c && !/^\d/.test(c) && !/\d{3,}/.test(c))[0]
            : null;
          // 越具体的排前面
          for (const css of [cls ? `${tag}.${cls}` : null, tag]) {
            if (css && scopeHits(css) === 1) {
              return `locator(${JSON.stringify(css)}, { hasText: ${JSON.stringify(marker)} })`;
            }
          }
        }
      }
      n = n.parentElement; depth++;
    }
    return null;
  };

  const selectorFor = (e, opts = {}) => {
    const requireOwnText = !!opts.requireOwnText;
    // testid 排第一是因为它「本该」为测试唯一标识而设。但组件框架常常批量吐出
    // 同一个 data-testid（实测有 245 个元素共用 text-comp-span），那时它不但不是
    // 最稳的，反而是最不可靠的 —— 回放时必然 strict mode 报错。
    // 所以先验唯一性：不唯一就当它不存在，往下走 role / text 分支。
    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = e.getAttribute?.(attr);
      if (!v) continue;
      if (document.querySelectorAll(`[${attr}=${JSON.stringify(v)}]`).length !== 1) continue;
      // getByTestId 只认 Playwright 配置里的 testIdAttribute（默认 data-testid）。
      // 其余几个属性得走属性选择器，否则生成的代码回放时根本找不到元素。
      return attr === 'data-testid'
        ? { kind: 'testid', code: `getByTestId(${JSON.stringify(v)})` }
        : { kind: 'testid', code: `locator(${JSON.stringify(`[${attr}=${JSON.stringify(v)}]`)})` };
    }
    // 文本输入框优先用 label / placeholder，而不是 role+name。
    // 原因：只有 placeholder 的输入框，其无障碍名恰好就是 placeholder，
    // 于是 role 分支会产出 getByRole('textbox', { name: '请输入用户名' })。
    // 那样能用，但一旦后来给它补了 <label>，无障碍名就变了，选择器随之失效 ——
    // 而 getByPlaceholder 只依赖 placeholder 本身，更稳也更直观。
    //
    // label / placeholder 同样要验唯一性。实测一个登录表单里有两个 placeholder
    // 完全相同的输入框（真的那个有 id，另一个是诱饵），录制时点的是哪个都能跑通，
    // 回放必然 strict mode 报错 —— 而且是在第一步就挂，看起来像"页面变了"。
    if (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') {
      if (e.labels?.[0]) {
        const lab = txt(e.labels[0]);
        const same = [...document.querySelectorAll('input,textarea,select')]
          .filter((x) => x.labels?.[0] && txt(x.labels[0]) === lab).length;
        if (lab && same === 1) return { kind: 'label', code: `getByLabel(${JSON.stringify(lab)}, { exact: true })` };
      }
      const ph = (e.getAttribute('placeholder') || '').trim();
      if (ph && document.querySelectorAll(`[placeholder=${JSON.stringify(ph)}]`).length === 1) {
        return { kind: 'placeholder', code: `getByPlaceholder(${JSON.stringify(ph)})` };
      }
      // label 和 placeholder 都撞车时，稳定的 id 是最好的退路。
      // 含 3 位以上数字的 id 多半是运行时生成的（tip_box_10059），第二次跑就变了，
      // 所以只认不带长数字的。实测的登录表单正是这种情况：两个输入框 placeholder
      // 一模一样，真的那个有 id="username"。
      const id = e.getAttribute('id');
      if (id && !/\d{3,}/.test(id) && document.querySelectorAll(`#${CSS.escape(id)}`).length === 1) {
        return { kind: 'id', code: `locator(${JSON.stringify('#' + id)})` };
      }
    }

    const role = roleOf(e);
    const name = nameOf(e);
    if (role && name) {
      // role + 无障碍名也会撞车：两个 placeholder 相同的输入框，无障碍名同样相同。
      // 不验的话只是把 strict mode 报错从 placeholder 分支挪到了 role 分支。
      const sameRoleName = [...document.querySelectorAll('*')]
        .filter((x) => x.offsetParent && roleOf(x) === role && nameOf(x) === name).length;
      if (sameRoleName <= 1) {
        return { kind: 'role', code: `getByRole(${JSON.stringify(role)}, { name: ${JSON.stringify(name)}, exact: true })` };
      }
    }
    // 表单控件不能走 getByText —— 它按文本内容匹配，而 input 没有文本内容。
    // 这里的 name 是无障碍名（多半来自 placeholder 或 label），拿它去 getByText
    // 会生成一个语法正确、回放却永远找不到元素的选择器。
    const isFormControl = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName);

    // getByText 解析到的是「最深的那个匹配元素」。如果本元素的文本其实来自某个
    // 后代（整行可点的开关行就是这样：文本在行内的 <span> 里），那么生成的
    // getByText 选中的是那个后代，不是本元素。
    //
    // 对普通点击影响不大 —— 事件会冒泡回来。但开关要在选中的元素**下面**读状态
    // （sw.locator('.eui_toggle_container')），选中 span 就读不到了：状态层是
    // span 的兄弟，不是它的后代。回放时报 30 秒超时，而录制当时一切正常。
    const textIsOwn = name
      ? ![...e.querySelectorAll('*')].some((x) => txt(x) === name)
      : false;

    // 文本在后代里 → getByText 会选中那个后代，而不是被点的这个容器。
    //
    // 原来的注释说「普通点击影响不大，事件会冒泡回来」—— **实测推翻了这个假设**：
    // 资产树的一行里，点整行会展开+选中，点行内的 span.eui_tree_text 只选中。
    // 录到的是整行，生成的却是 getByText → 回放时子节点永远不出现，
    // 而那一步还报 success（点击确实成功了，只是做的不是同一件事）。
    //
    // 所以这里给容器本身算一个可读的定位方式：locator('div.类名', { hasText: 名字 })。
    // 这正是人工会写的形态，而且 hasText 匹配的是容器、不是里面的文本节点。
    if (name && !isFormControl && !textIsOwn) {
        const tag = e.tagName.toLowerCase();
        const cls = typeof e.className === 'string'
          ? e.className.trim().split(/\s+/)
              .filter((c) => c && !/^\d/.test(c) && !/\d{3,}/.test(c))[0]
          : null;
        if (cls) {
          const base = `${tag}.${cls}`;
          let owners = [];
          try {
            owners = [...document.querySelectorAll(base)].filter((x) => txt(x) === name);
          } catch { owners = []; }
          if (owners.length === 1) {
            return {
              kind: 'scoped',
              code: `locator(${JSON.stringify(base)}, { hasText: ${JSON.stringify(name)} })`,
            };
          }
        }
        // 算不出可读的容器定位就落到 CSS 路径 —— 它至少指向被点的那个元素本身
      }

    if (name && !isFormControl && (!requireOwnText || textIsOwn)) {
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
    // 稳定 id 是位置型 CSS 路径之外最好的退路：它不随 DOM 结构变化。
    // 含 3 位以上数字的 id 多半是运行时生成的（tip_box_10059），第二次跑就变了。
    const anyId = e.getAttribute?.('id');
    if (anyId && !/\d{3,}/.test(anyId)
        && document.querySelectorAll(`#${CSS.escape(anyId)}`).length === 1) {
      return { kind: 'id', code: `locator(${JSON.stringify('#' + anyId)})` };
    }

    // CSS 兜底同样要验唯一性。testid / placeholder / role / 文本几条路都验了，
    // 唯独这条没验 —— 而它恰恰是最容易撞车的：路径由 tag + class + nth-of-type
    // 拼出来，两个结构相同的组件（比如叠着的两个弹窗）会产出完全一样的路径。
    //
    // 撞车了也不能不给选择器，否则这一步就丢了。标出来交给生成器提示人工处理，
    // 这和文本撞车走的是同一套处理方式。
    // 图标类控件（展开箭头、关闭叉）没有文本也没有 role，直接走 CSS 路径的话
    // 会得到一长串 nth-of-type —— 既难读，又会因为弹窗层级变化而失效。
    //
    // 更好的形态是人工会写的那种：用外层那一行做作用域，再点进图标。
    //   locator('div.eui_tree_node_cont', { hasText: 'default-group' }).locator('.eui_tree_hit')
    // 作用域来自最近的带文本祖先，图标本身用它的稳定类名。
    const ownCls = typeof e.className === 'string'
      ? e.className.trim().split(/\s+/)
          .filter((c) => c && !/^\d/.test(c) && !/\d{3,}/.test(c))[0]
      : null;
    if (ownCls && !txt(e)) {
      let host = e.parentElement, depth = 0;
      while (host && host !== document.body && depth < 4) {
        const hostText = txt(host);
        const hostCls = typeof host.className === 'string'
          ? host.className.trim().split(/\s+/)
              .filter((c) => c && !/^\d/.test(c) && !/\d{3,}/.test(c))[0]
          : null;
        // 容器文本很长也能当作用域（弹窗正文动辄上百字）：hasText 是**子串**
        // 匹配，取开头一段就够。原来要求整段 ≤40 字，于是关闭叉这类图标只能
        // 退到 CSS 绝对路径 —— 那串路径带 nth-of-type，弹窗多一层少一层就失效。
        // 实测同一个关闭叉两次录制分别录成 div:nth-of-type(8) 和 (9)。
        const anchor = hostText.length <= 40 ? hostText : hostText.slice(0, 24).trim();
        if (hostText && hostCls && anchor && !volatileMarker(anchor)) {
          const base = `${host.tagName.toLowerCase()}.${hostCls}`;
          let owners = [];
          try {
            // 用和 Playwright 一致的语义验唯一性：整段等值 or 前缀包含
            owners = [...document.querySelectorAll(base)].filter(
              (x) => (anchor === hostText ? txt(x) === hostText : txt(x).includes(anchor)));
          } catch { owners = []; }
          if (owners.length === 1 && owners[0].querySelectorAll(`.${ownCls}`).length === 1) {
            return {
              kind: 'scoped',
              code: `locator(${JSON.stringify(base)}, { hasText: ${JSON.stringify(anchor)} })`
                + `.locator(${JSON.stringify('.' + ownCls)})`,
            };
          }
        }
        host = host.parentElement; depth++;
      }
    }

    const path = cssPath(e);
    const code = `locator(${JSON.stringify(path)})`;
    // 校验不了唯一性时按**可疑**处理，不能按干净放行 ——
    // 静默兜底正是这份 skill 反复在批的东西，这里别自己再犯一次。
    let count = null;                       // null = 没验成
    try { count = document.querySelectorAll(path).length; } catch { count = null; }
    if (count === null) return { kind: 'css', code, ambiguous: true, matches: '未知（选择器无法校验）' };
    return count > 1 ? { kind: 'css', code, ambiguous: true, matches: count } : { kind: 'css', code };
  };

  // 事件 target 常是内层 span/svg/i，往上找到真正「可操作」的那个元素
  /**
   * 判断一个元素本身是不是「独立控件」
   *
   * 图标类控件（展开箭头、关闭叉、排序标记）通常既没有文本也没有 role，
   * 但它们是**独立的交互目标** —— 点它和点它外面那一行是两件事。
   *
   * cursor:pointer 是最通用的信号：CSS 明确声明了「这里可以点」。
   * tabindex 说明它能被键盘聚焦，同样是控件的标志。
   */
  const isOwnControl = (n) => {
    if (!n || n === document.body) return false;
    if (n.hasAttribute?.('tabindex')) return true;
    try {
      if (getComputedStyle(n).cursor === 'pointer') return true;
    } catch { /* 元素已脱离文档 */ }
    return false;
  };

  /**
   * 从事件目标找出「这一下到底点了什么」—— 最小捕获原则
   *
   * 事件 target 常是内层的 span/svg/i，往上找能得到更好的选择器（有 role、有名字）。
   * 但**不能无限上溯**：实测踩过一次代价很大的坑 ——
   *
   *   资产树的一行里有个展开箭头 span.eui_tree_hit，没有文本也没有 role。
   *   人点的是箭头（展开，列出终端），录制器却一路上溯停在带文本的整行上，
   *   于是生成的选择器指向行。回放点行 → 只选中不展开 → 终端永远不出现，
   *   而那一步还报 success：点击确实成功了，只是做的不是同一件事。
   *
   * 所以顺序是：
   *   1. 语义祖先（button / a / input / role）—— 选择器最好，优先
   *   2. 事件目标自己就是独立控件 —— **就地停下**，别爬到外层容器
   *   3. 都不是，才上溯到带文本的容器
   */
  const meaningful = (e) => {
    let n = e, d = 0;
    while (n && n !== document.body && d < 6) {
      const t = n.tagName.toLowerCase();
      if (['button', 'a', 'input', 'textarea', 'select'].includes(t) || n.getAttribute('role')) return n;
      // 起点自己是独立控件时就地停下：它和外层容器是两个不同的交互目标
      if (n === e && isOwnControl(n) && !txt(n)) return n;
      const s = txt(n);
      if (s && s.length <= 40 && n.children.length <= 2) return n;
      n = n.parentElement; d++;
    }
    return e;
  };

  /**
   * 状态元素相对于「步骤元素」的位置
   *
   * 开关的可点区域和承载状态的那一层常常不是同一个元素：整行可点，但 toggled
   * 写在内层容器上。步骤的选择器指向行（那才是有名字、点得动的东西），读状态却
   * 必须往里走一层 —— 不记下这个偏移，生成的 classList.contains 读的是行，
   * 永远读不到，于是每次都点、断言必挂。
   *
   * 返回 '' 表示状态就在步骤元素本身；返回 undefined 表示够不着，
   * 这时宁可退回普通点击，也不要生成一个读不到状态的"幂等"代码。
   */
  const stateWithin = (stateEl) => {
    const root = meaningful(stateEl);
    if (root === stateEl) return '';
    const cls = String(stateEl.className || '').trim().split(/\s+/).filter(
      (c) => c && !/\d{3,}/.test(c) && !/^\d/.test(c) &&
        // 状态词本身不能进选择器：那样只有「开」的时候才选得中
        !/^(checked|active|on|selected|toggled|opened|unchecked|inactive|off|untoggled|closed)$/i.test(c),
    );
    for (const c of cls) {
      try {
        if (root.querySelectorAll('.' + CSS.escape(c)).length === 1) return '.' + c;
      } catch { /* 类名不合法就换下一个 */ }
    }
    return undefined;
  };

  // 只记录浏览器实际渲染后的可见特征，不把完整 DOM/属性树塞进录制文件。
  // rect 也供驱动层在 selector 截图失败时做 clip 兜底。
  const renderedUi = (el, event) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const x = Number(event?.clientX);
    const y = Number(event?.clientY);
    let pageRect, pageViewport;
    try {
      let px = r.x, py = r.y, w = window;
      while (w !== w.top) {
        const frame = w.frameElement;
        if (!frame) throw new Error('cross-origin frame');
        const fr = frame.getBoundingClientRect();
        px += fr.x; py += fr.y; w = w.parent;
      }
      pageRect = { x: px, y: py, width: r.width, height: r.height };
      pageViewport = { width: w.innerWidth, height: w.innerHeight };
    } catch { /* 跨域 iframe 无法换算到顶层坐标 */ }
    return {
      rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      pageRect,
      pageViewport,
      click: Number.isFinite(x) && Number.isFinite(y) ? {
        x, y,
        rx: r.width ? (x - r.x) / r.width : null,
        ry: r.height ? (y - r.y) / r.height : null,
      } : undefined,
      viewport: { width: innerWidth, height: innerHeight },
      deviceScaleFactor: devicePixelRatio,
      style: {
        color: s.color,
        backgroundColor: s.backgroundColor,
        borderRadius: s.borderRadius,
        opacity: s.opacity,
      },
    };
  };

  const push = (type, el, extra, event) => {
    try {
      const now = Date.now();
      const t = meaningful(el);
      // 点在页面空白处会一路上溯到 html/body。这种步骤回放时点了等于没点，
      // 却会以 locator("html") 的形式留在草稿里，读的人还得判断它是不是有意义。
      if (['click', 'dblclick'].includes(type) &&
          (t === document.body || t === document.documentElement)) return;
      const sel = selectorFor(t, { requireOwnText: type === 'switch' });
      const step = {
        id: extra?._id || `${tag}-${++seq}`,
        t: now, actionT: extra?.actionT ?? now, type, sel: sel.code, kind: sel.kind,
        ambiguous: !!sel.ambiguous, matches: sel.matches,
        label: txt(t).slice(0, 60), css: cssPath(t),
        url: location.pathname + location.hash,
        // 元素在 iframe 里时，回放必须先 frameLocator 进去 —— 直接 page.getByX()
        // 只搜主文档，一定找不到。登录表单放在 iframe 里是很常见的做法。
        inFrame: window !== window.top,
        framePath: window !== window.top ? location.pathname : undefined,
        ...extra,
      };
      if (['click', 'dblclick', 'switch', 'check', 'uncheck'].includes(type)) {
        step.ui = renderedUi(t, event);
      }

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
      return step;                    // 开关观察器要用它的 id 发升级记录
    } catch { /* 录制出错绝不能影响用户操作 */ }
    return null;
  };

  let seq = 0;
  const tag = Math.random().toString(36).slice(2, 8);

  /* ────────── 断言菜单 ────────── */

  const MENU_ID = '__rec_assert_menu__';

  // 断言类型 → 该类型的 expected 怎么取默认值、用什么控件编辑
  const ASSERTIONS = {
    text:      { label: '文本等于',   kind: 'string', of: (e) => txt(e) },
    value:     { label: '输入值等于', kind: 'string', of: (e) => String(e.value ?? '') },
    visible:   { label: '可见性',     kind: 'bool',   of: () => true },
    checked:   { label: '勾选状态',   kind: 'bool',   of: (e) => !!e.checked },
    attribute: { label: '属性等于',   kind: 'attr',   of: () => '' },
    // 期望值不从录制里搬，而是回放那一刻按本机时钟算出来。
    // 页面上显示时间的字段（「最近使用」「更新于」）断字面量隔一会儿就红，
    // 而红的原因和被测功能无关 —— 那种断言守不住任何东西。
    nowtext:   { label: '显示的是当前时间', kind: 'time',
                 of: (e) => timeSourceOf(e) === 'value'
                   ? String(e.value ?? '') : txt(e) },
  };

  // 时间显示在哪：输入框在 value 上，inner_text 恒为空 —— 读错了断言永远不通过。
  const timeSourceOf = (e) =>
    ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) ? 'value' : 'text';

  // 按元素当前显示的样子推断默认格式：只有日期就比到日，带时刻就比到分。
  // 比错粒度的后果很实际：日期框拿 %H:%M 去比永远不通过。
  const guessTimeFormat = (sample) => {
    const s = String(sample || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return '%Y-%m-%d';
    if (/\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(s)) return '%Y-%m-%d %H:%M';
    return TIME_FORMAT_DEFAULT;
  };

  // 只做值的比对，不解析时间、不换算时区。默认只比到分钟：
  // 秒一定对不上（页面渲染和断言求值之间必然有间隔）。
  const TIME_FORMAT_DEFAULT = '%H:%M';
  const nowByFormat = (fmt) => {
    const d = new Date(), p2 = (n) => String(n).padStart(2, '0');
    return String(fmt)
      .replace(/%Y/g, String(d.getFullYear()))
      .replace(/%m/g, p2(d.getMonth() + 1))
      .replace(/%d/g, p2(d.getDate()))
      .replace(/%H/g, p2(d.getHours()))
      .replace(/%M/g, p2(d.getMinutes()))
      .replace(/%S/g, p2(d.getSeconds()));
  };

  const closeMenu = () => document.getElementById(MENU_ID)?.remove();

  const openMenu = (el, x, y) => {
    closeMenu();
    const host = document.createElement('div');
    host.id = MENU_ID;
    Object.assign(host.style, {
      position: 'fixed', left: `${x}px`, top: `${y}px`, zIndex: 2147483647,
    });
    // Shadow DOM 隔离：页面自己的 CSS 五花八门，不隔离的话菜单会被改得没法用
    const sh = host.attachShadow({ mode: 'open' });
    sh.innerHTML = `
      <style>
        :host { all: initial; }
        .box { font: 13px/1.5 system-ui, sans-serif; background:#fff; color:#222;
               border:1px solid #bbb; border-radius:6px; box-shadow:0 4px 16px rgba(0,0,0,.18);
               padding:10px; min-width:260px; }
        .row { display:flex; align-items:center; gap:6px; margin-bottom:6px; }
        label { flex:0 0 68px; color:#555; }
        select, input[type=text] { flex:1; padding:3px 5px; border:1px solid #ccc;
                                   border-radius:3px; font:inherit; min-width:0; }
        .hint { color:#a00; font-size:12px; min-height:16px; margin-bottom:4px; }
        .acts { display:flex; justify-content:flex-end; gap:8px; }
        button { font:inherit; padding:3px 12px; border-radius:4px; border:1px solid #bbb;
                 background:#f6f6f6; cursor:pointer; }
        button.primary { background:#1a73e8; color:#fff; border-color:#1a73e8; }
        button[disabled] { opacity:.45; cursor:not-allowed; }
        .empty { display:flex; align-items:center; gap:4px; font-size:12px; color:#666; }
      </style>
      <div class="box">
        <div class="row"><label>断言类型</label>
          <select id="t">${Object.entries(ASSERTIONS)
            .map(([k, v]) => `<option value="${k}">${v.label}</option>`).join('')}</select></div>
        <div class="row" id="attrRow" style="display:none"><label>属性名</label>
          <input type="text" id="attr" placeholder="如 href、data-state"></div>
        <div class="row" id="fmtRow" style="display:none"><label>时间格式</label>
          <input type="text" id="fmt" value="%H:%M" placeholder="%H:%M"></div>
        <div class="row" id="fmtPreview" style="display:none"><label></label>
          <span id="fmtNow" style="color:#555;font-size:12px"></span></div>
        <div class="row"><label id="esLabel">Expected</label>
          <select id="eb" style="display:none">
            <option value="true">true</option><option value="false">false</option>
          </select>
          <input type="text" id="es"></div>
        <div class="hint" id="hint"></div>
        <div class="acts">
          <span class="empty"><input type="checkbox" id="allowEmpty"><label for="allowEmpty"
            style="flex:none;cursor:pointer">允许空值</label></span>
          <button id="cancel">取消</button>
          <button id="ok" class="primary">添加断言</button>
        </div>
      </div>`;
    document.documentElement.appendChild(host);

    // 收进视口。菜单是 position:fixed 钉在鼠标处的，右键靠底部/右侧的元素时
    // 「添加断言」按钮会落到视口外 —— Playwright 直接报「element is outside of
    // the viewport」，人工操作时则是按钮根本点不到。表格行尤其容易踩：
    // 时间字段那类单元格往往就在长表格的下半部分。
    //
    // 必须在**每次高度变化之后**都收一遍，不能只在插入时收一次：切换断言类型会
    // 增减控件行（时间类型多两行），插入时量到的是切换前那个矮的形态。
    // 实测就是这么漏的 —— 收边代码在，按钮照样落在视口外 11px。
    const fit = () => {
      try {
        const box = sh.querySelector('.box').getBoundingClientRect();
        const margin = 8;
        let left = x, top = y;
        if (left + box.width > innerWidth - margin) left = Math.max(margin, innerWidth - box.width - margin);
        if (top + box.height > innerHeight - margin) top = Math.max(margin, innerHeight - box.height - margin);
        host.style.left = `${left}px`;
        host.style.top = `${top}px`;
      } catch { /* 量不出来就维持原位，总比不显示好 */ }
    };
    fit();

    const $ = (id) => sh.getElementById(id);
    const sync = () => {
      const a = ASSERTIONS[$('t').value];
      $('attrRow').style.display = a.kind === 'attr' ? 'flex' : 'none';
      $('fmtRow').style.display = a.kind === 'time' ? 'flex' : 'none';
      $('fmtPreview').style.display = a.kind === 'time' ? 'flex' : 'none';
      $('eb').style.display = a.kind === 'bool' ? 'block' : 'none';
      // time 类型没有要人填的期望值 —— 它由回放时的时钟决定。
      // 仍然把录制当时看到的文本显示出来（只读），好让人确认选的是不是那个元素。
      $('es').style.display = a.kind === 'bool' ? 'none' : 'block';
      $('es').readOnly = a.kind === 'time';
      // 时间类型下这个框**不是**期望值 —— 期望值由回放时的时钟算出。
      // 还叫 Expected 会直接误导人：实测有人据此以为断言比的是这个字符串。
      $('esLabel').textContent = a.kind === 'time' ? '录制时看到' : 'Expected';
      if (a.kind === 'time') $('fmt').value = guessTimeFormat(a.of(el));
      if (a.kind === 'bool') $('eb').value = String(a.of(el));
      else $('es').value = String(a.of(el) ?? '');
      validate();
      fit();                     // 控件行数变了，高度跟着变，得重新收边
    };
    const validate = () => {
      const a = ASSERTIONS[$('t').value];
      let bad = '';
      if (a.kind === 'time') {
        const fmt = $('fmt').value.trim() || TIME_FORMAT_DEFAULT;
        const now = nowByFormat(fmt);
        const seen = String(a.of(el) ?? '');
        $('fmtNow').textContent = `此刻求值 → ${now}` +
          (seen.includes(now) ? '（当前文本已含它）' : '（当前文本不含它）');
        // 不校验元素现在是否含此刻时间：录制时这个字段本来就可能是过去的时间，
        // 断言要守的是「回放时它会被刷新成当时的时间」。
        //
        // 但**结构上读不出文本**的元素要拦住：canvas / img / svg 画的图表没有
        // 文本内容，这条断言永远不可能通过。实测有人把它加在一张折线图上，
        // 录制、生成、回放四段全都正常，最后以「actual=''」失败 —— 机制没错，
        // 目标从一开始就不可能对。
        const TEXTLESS = ['CANVAS', 'IMG', 'SVG', 'VIDEO'];
        // 输入框的 seen 已经是 value（见 timeSourceOf），不会被误判成无文本
        if (!fmt) bad = '请填写时间格式';
        else if (TEXTLESS.includes(el.tagName)) {
          bad = `<${el.tagName.toLowerCase()}> 读不出文本，时间断言永远不会通过。`
              + '请选显示时间的那段文字本身';
        } else if (!seen.trim()) {
          bad = '这个元素当前没有文本。确认它回放时会显示时间，否则断言必然失败';
        }
      } else if (a.kind === 'attr' && !$('attr').value.trim()) bad = '请填写属性名';
      // expected 不能默默为空：空字符串是合法断言，但必须是明确的选择
      else if (a.kind !== 'bool' && !$('es').value && !$('allowEmpty').checked)
        bad = 'Expected 为空。确实要断言空字符串就勾「允许空值」';
      $('hint').textContent = bad;
      $('ok').disabled = !!bad;
    };
    sh.addEventListener('input', validate);
    $('t').addEventListener('change', sync);
    $('cancel').addEventListener('click', closeMenu);
    $('ok').addEventListener('click', () => {
      const type = $('t').value;
      const a = ASSERTIONS[type];
      const expected = a.kind === 'bool' ? $('eb').value === 'true' : $('es').value;
      if (a.kind === 'time') {
        // 断言类型仍然是 text —— 变的只是「期望值从哪来」。
        // 这样回放侧读取文本的那套逻辑一行都不用改。
        pushAssert(el, timeSourceOf(el) === 'value' ? 'value' : 'text',
                   expected, undefined, {
          kind: 'localtime',
          format: $('fmt').value.trim() || TIME_FORMAT_DEFAULT,
          match: 'contains',
        });
      } else {
        pushAssert(el, type, expected, a.kind === 'attr' ? $('attr').value.trim() : undefined);
      }
      closeMenu();
    });
    sync();
  };

  /**
   * 时间类断言的选择器不能锚在它自己要断言的那段文本上
   *
   * 右键「最近使用」那个单元格，selectorFor 会算出
   * getByText("2026-08-18 20:33:47") —— 因为那就是它唯一的特征。可这个字段
   * 一刷新，元素就不存在了：断言以「找不到元素」失败，而不是「时间不对」。
   * 报错指不到真正的原因，而这恰恰是时间断言唯一的用途。
   *
   * 表格里的正解是人工会写的那种：**稳定的行 + 第几列**。锚取同一行里
   * 不易变、且全页唯一的那段文本（API Key 那张表里就是 key 的名字）。
   * 找不到这样的锚就返回 null，交给调用方降级并标出来 —— 不硬造一个。
   */
  const stableCellSelector = (el) => {
    const cell = el.closest?.('td, th');
    const row = cell?.closest?.('tr');
    if (!cell || !row) return null;
    const cells = [...row.children].filter((c) => /^(TD|TH)$/.test(c.tagName));
    const index = cells.indexOf(cell);
    if (index < 0) return null;
    const anchor = cells
      .filter((c) => c !== cell)
      .map((c) => txt(c))
      .find((s) => s && s.length <= 40 && !volatileMarker(s) && countText(s) === 1);
    if (!anchor) return null;
    return {
      kind: 'scoped',
      code: `locator("tr", { hasText: ${JSON.stringify(anchor)} })`
            + `.locator("td").nth(${index})`,
      anchor,
    };
  };

  const pushAssert = (el, assertion, expected, attribute, expectedFrom) => {
    try {
      const t = meaningful(el);
      let sel = selectorFor(t);
      // 期望值动态算的断言：选择器若把录到的那段文本包在里面，回放必然找不到元素。
      // 换成「稳定行 + 第几列」；换不到就保留原样，让生成器把问题说出来。
      let cellAnchor;
      if (expectedFrom && expected && String(sel.code).includes(String(expected))) {
        const cell = stableCellSelector(t);
        if (cell) { sel = cell; cellAnchor = cell.anchor; }
      }
      const step = {
        id: `${tag}-${++seq}`, t: Date.now(),
        type: 'assert', assertion, expected, sel: sel.code, kind: sel.kind,
        ambiguous: !!sel.ambiguous, matches: sel.matches,
        label: txt(t).slice(0, 60), css: cssPath(t),
        url: location.pathname + location.hash,
        inFrame: window !== window.top,
        framePath: window !== window.top ? location.pathname : undefined,
      };
      if (attribute) step.attribute = attribute;
      // 录下来的 expected 保留作证据（当时看到的是什么），但回放时不用它比对
      if (expectedFrom) step.expectedFrom = expectedFrom;
      if (cellAnchor) step.cellAnchor = cellAnchor;
      steps.push(step);
      if (typeof window.__recPush === 'function') {
        try { window.__recPush(step); } catch { /* 页面正在卸载 */ }
      }
      return step;
    } catch { /* 录制出错不能影响用户操作 */ }
    return null;
  };

  /**
   * 开关的状态变化可能被二次确认挡在后面
   *
   * 实测：拨这个页面的开关会先弹确认框，class 要等人点了「确认」才变 ——
   * 那可能是好几秒之后。原来只等 1.2 秒，等不到就退回普通点击，于是这一步
   * 被录成盲点：回放时起始状态一变就朝反方向拨，而且不报错。
   *
   * 改成：**先照常把点击记下来**（不能为了等状态而拖着不记，页面随时可能跳转），
   * 之后继续观察；真变了再用同一个 id 发一条升级记录，改写成「拨到指定状态」。
   * 驱动侧按 id 覆盖。
   *
   * 只在恰好一层 class 变化时升级 —— 多层一起变说明分不清是哪个，
   * 宁可留着普通点击，也不猜一个可能相反的状态。
   */
  const watchSwitchChange = (candidates, before, stepId, sourceEvent) => {
    const deadline = Date.now() + 20000;
    // 状态变化之前有没有别的步骤被录进来？有，就说明这一拨要靠后续交互
    // （点「确认」）才落地。这不是时间长短的猜测，是有没有交互的事实。
    const fromIndex = steps.length;
    const tick = () => {
      const moved = candidates.filter((x, i) => String(x.className) !== before[i]);
      // 变化可能分几帧到达（滑块先动、容器后加 toggled），也可能好几层一起变。
      // 认的是「变了**并且**现在读得出状态」的那一层，恰好一层才认；
      // 一时分不清就继续等，等到最后还分不清就老实留着普通点击。
      const stated = moved.filter((x) => (switchInfo(x) || {}).on !== null);
      if (stated.length !== 1) {
        if (Date.now() < deadline) setTimeout(tick, 150);
        return;
      }
      const info = switchInfo(stated[0]);
      const within = stateWithin(stated[0]);
      if (info.on === null || within === undefined) return;
      const between = steps.slice(fromIndex).map((x) => x.id);
      push('switch', stated[0], {
        _id: stepId, _upgrade: true,
        to: info.on,
        via: {
          ...info.via, within,
          // 需要后续交互才落地：回放时点完就走，不能堵在这里等状态
          gated: between.length > 0,
          gatedSteps: between,
        },
      }, sourceEvent);
    };
    setTimeout(tick, 150);
  };

  /**
   * 这一下是不是「关掉一个浮层」
   *
   * 首启弹窗、公告、提示条出现与否取决于账号状态和历史操作，回放时不一定在 ——
   * 所以关它的那一步天生是条件步骤（存在则点）。
   *
   * 原来靠「选择器落到 CSS 兜底」当代理信号，因为这类图标以前只能产出绝对路径。
   * 选择器变好之后代理就不成立了，弹窗步骤反而变成必经节点。换成有据可查的
   * 判据：点之前目标在一个浮层里，点之后那个浮层不在了 —— 那这一步的作用
   * 就是关掉它。回放据此把它当条件步骤，被遮罩挡住时还能把它提前做掉。
   *
   * 只认**没有文本的图标**（关闭叉）。「点在浮层里且浮层消失」这个条件太宽：
   * 选中一个下拉选项同样满足它，而那是实打实的操作，标成可选就等于允许
   * 悄悄跳过。带文字的确认按钮（「我知道了」「确定」）因此漏判 —— 宁可漏判
   * 也不误判：漏判只是维持原状，误判会让一步真操作被跳过而且不报错。
   * 「确定」尤其危险，它在表单弹窗里就是提交。
   */
  // 提示条不是浮层，但同样是「出现与否取决于账号状态」的条件元素。
  // 实测：「系统检测到您未绑定手机号码和电子邮箱」这条横幅挂在 #platform-root
  // 里，没有 fixed/absolute + 高 z-index，floatingAncestor 认不出来 —— 于是关它
  // 那一步成了必经步骤，而下次登录它可能根本不出现，整条轨迹就断在那里。
  const NOTICE_CLS = /(tip|notice|toast|alert|banner|message|snackbar)/i;
  const CLOSER_CLS = /(close|dismiss)/i;

  const dismissCandidates = (el) => {
    // 收一组候选，点完之后谁消失了算谁 —— 不能只认往上撞到的第一层。
    // 实测：提示条挂在一个绝对定位的头部容器里，floatingAncestor 越过提示条
    // 撞到头部，而头部不会消失，于是永远等不到「关掉了」。这和开关那个
    // 「撞到滑块就返回、而状态写在容器上」是同一个形状的错。
    const out = [];
    // 容器证据弱（只是类名像提示条），就要求元素证据更强：图标本身得是个关闭件。
    // 不能只看「祖先消失了」—— 删除某一行的图标也满足那个条件，
    // 把一步破坏性操作标成可选是最坏的一类错。
    if (CLOSER_CLS.test(String(el.className || ''))) {
      let n = el.parentElement, d = 0;
      while (n && n !== document.body && d < 6) {
        // 提示条带着一段说明文字，这是它和「一排图标按钮」的区别
        if (NOTICE_CLS.test(String(n.className || '')) && txt(n).length >= 8) out.push(n);
        n = n.parentElement; d++;
      }
    }
    const floating = floatingAncestor(el);
    if (floating && !out.includes(floating.el)) out.push(floating.el);
    return out;
  };

  const layerGone = (layer) => {
    if (!layer.isConnected || !layer.getClientRects().length) return true;
    const st = getComputedStyle(layer);
    if (st.display === 'none' || st.visibility === 'hidden') return true;
    // 收起动画常把高度压到 0 而不摘掉节点
    const r = layer.getBoundingClientRect();
    return r.width === 0 || r.height === 0;
  };

  const watchOverlayDismiss = (layers, stepId, sourceEvent) => {
    const deadline = Date.now() + 3000;
    const tick = () => {
      const gone = layers.some(layerGone);
      if (gone) {
        // 只加这一个标记。浮层此刻已经消失，重算选择器只能退到路径兜底，
        // 那会把点击时算出的好选择器覆盖掉 —— 实测正是这样把
        // locator("div.eui_Dialog_Panel", { hasText: ... }) 换成了一串 nth-of-type。
        push('click', sourceEvent.target, {
          _id: stepId, _upgrade: true, _only: ['dismissesOverlay'],
          dismissesOverlay: true,
        }, sourceEvent);
        return;
      }
      if (Date.now() < deadline) setTimeout(tick, 150);
    };
    setTimeout(tick, 150);
  };

  // 右键打开断言菜单。菜单自身的交互不能被当成被录的操作，
  // 所以下面所有监听都先检查事件是否来自菜单内部（composedPath 才能穿透 Shadow DOM）。
  const fromMenu = (e) => e.composedPath?.().some((n) => n?.id === MENU_ID);

  document.addEventListener('contextmenu', (e) => {
    if (fromMenu(e)) return;
    e.preventDefault();
    openMenu(e.target, e.clientX, e.clientY);
  }, true);

  document.addEventListener('mousedown', (e) => {
    if (!fromMenu(e)) closeMenu();
  }, true);

  document.addEventListener('click', (e) => {
    if (fromMenu(e)) return;
    if (e.detail > 1) return; // 第二次 click 由 dblclick 步骤表示
    // 复选框和单选框的 click 会紧跟一个 change，两个都记就会生成
    // 「先 click 再 check」这种连续操作同一元素的冗余步骤。
    // change 携带了勾选状态，信息更完整，所以 click 这边跳过它们。
    const el = e.target;
    const actionT = Date.now();
    if (el?.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) return;
    // 点 <label> 也会连带触发内部 input 的 change，同样跳过
    if (el?.closest?.('label')?.querySelector('input[type=checkbox],input[type=radio]')) return;

    // 开关单独记：带上"这一下要把它拨到什么状态"。
    // 只记 click 的话，回放时初始状态一旦不同就会朝反方向拨，而且不报错。
    const sw = switchAncestor(el);
    if (sw && sw.info.on !== null) {
      // 状态在 click 之后才更新，等一拍再读，读不到就用取反兜底
      const before = sw.info.on;
      setTimeout(() => {
        const now = switchInfo(sw.el);
        const via = (now && now.via) || sw.info.via;
        const within = stateWithin(sw.el);
        if (within === undefined) return push('click', el, { t: actionT, actionT }, e);
        push('switch', sw.el, {
          t: actionT, actionT,
          to: now && now.on !== null ? now.on : !before,
          via: { ...via, within },
        }, e);
      }, 60);
      return;
    }

    // 点击当下读不出状态的情形：class 型开关在「关」的时候往往什么标记都没有，
    // 只有开启才多一个 toggled —— 标记缺席既可能是关，也可能是这一层根本不带状态，
    // 静态看分不出来。那就看拨完之后**哪一层的 class 变了**：变的那层就是状态层。
    // 只有恰好一层变化时才认，多层一起变说明猜不准，老实退回普通点击。
    // 状态层可能在事件目标的上面（点滑块）也可能在下面（点整行），两边都要看
    const inside = [...switchChain(el), ...switchInside(el)];
    if (inside.length) {
      const before = inside.map((x) => String(x.className));
      // 先如实记下点击，再观察状态是否变化（可能要等二次确认）
      const step = push('click', el, { t: actionT, actionT }, e);
      if (step) watchSwitchChange(inside, before, step.id, e);
      return;
    }

    const step = push('click', el, undefined, e);
    // 点在浮层／提示条里的无文本图标：看这一下是不是把它关掉了
    const layers = txt(el) ? [] : dismissCandidates(el);
    if (step && layers.length) watchOverlayDismiss(layers, step.id, e);
  }, true);
  document.addEventListener('dblclick', (e) => {
    if (!fromMenu(e)) push('dblclick', e.target, undefined, e);
  }, true);
  document.addEventListener('change', (e) => {
    if (fromMenu(e)) return;
    const el = e.target;
    // 一些站点会在普通 div 上派发自定义 change（Bing 首页轮播就是如此）。
    // 这不是用户输入；若继续读取不存在的 value，会每次都录成空 fill。
    if (!el?.matches?.('input, textarea, select')) return;
    if (el.type === 'checkbox' || el.type === 'radio') {
      push(el.checked ? 'check' : 'uncheck', el);
    } else if (el.type === 'password') {
      // 步骤照记，值不记。生成的脚本用 process.env.REC_PASSWORD 代替 ——
      // 明文密码写进 spec 会让脚本绑死在一个账号上，而且 spec 通常是要进版本库的。
      push('fill', el, { secret: true });
    } else {
      const filled = String(el.value ?? '').slice(0, 200);
      push('fill', el, { value: filled, ...dateValueMeta(filled) });
    }
  }, true);
  document.addEventListener('keydown', (e) => {
    if (fromMenu(e)) return;
    if (e.key === 'Escape') { closeMenu(); return; }
    if (e.key === 'Enter') push('press', e.target, { key: 'Enter' });
  }, true);

  window.__rec = { steps, drain: () => steps.splice(0, steps.length) };
};
