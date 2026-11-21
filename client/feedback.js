/* agent-page-feedback · 页面批注组件（由 fb-loader.js 按需加载）
   - 首次用带 ?fb=1（或 ?fb=口令）的链接打开，输入口令和姓名后，左下角出现批注条
   - 点「批注」→ 点选页面位置（可先划选文字）→ 自动截一张位置图 → 写意见、可粘贴或添加图片 → 提交
   - 点「框选」→ 在页面上拖一个框（手机上用手指）→ 截图框出这块区域
   - 写意见时可以「说话」（浏览器语音识别；不支持时提示用键盘上的话筒），可以在截图上用手指 / 鼠标圈出要改的地方
   - 批注存在服务器（APF_CONFIG.api），多人共享；汇总页 feedback.html 可讨论、复盘、导出给 agent
   - 所有界面在 Shadow DOM 里，不影响网站自己的样式 */
(function () {
  if (window.__apf) return; window.__apf = 1;
  const CFG = window.APF_CONFIG || {};
  const SRC = (document.currentScript && document.currentScript.src) || (document.querySelector('script[src*="feedback.js"]') || {}).src || location.href;
  const API = CFG.api || new URL('../', SRC).href, BASE = CFG.base || '/', SITE = location.origin + BASE, SUMMARY = CFG.summary || new URL('feedback.html', SRC).href;
  const LS = { get: k => { try { return localStorage.getItem('apf:' + k) || ''; } catch (e) { return ''; } }, set: (k, v) => { try { v ? localStorage.setItem('apf:' + k, v) : localStorage.removeItem('apf:' + k); } catch (e) {} } };
  const qs = new URLSearchParams(location.search), fbParam = qs.get('fb'), focusId = +qs.get('fbid') || 0;
  if (!LS.get('cid')) LS.set('cid', Math.random().toString(36).slice(2) + Date.now().toString(36));
  if (!LS.get('code') && fbParam == null && !focusId) return;   // 没登录过、也不是邀请链接：什么都不加载

  const KIND = { bug: '问题', suggest: '建议', copy: '文案', style: '样式', data: '内容有误' };
  const PRIO = { high: '高', mid: '中', low: '低' };
  const STAT = { open: '待处理', question: '待决定', approved: '可以做', doing: '处理中', review: '待验收', done: '已完成', wontfix: '不改' };
  const SCOL = { open: '#C2410C', question: '#7C3AED', approved: '#0E7490', doing: '#1D4ED8', review: '#B45309', done: '#15803D', wontfix: '#6B7280' };
  // 负责人（管理员通行码）的决定按钮：按状态给出能做的决定；这些按钮就是 agent 的执行信号
  const DECIDE = { open: [['approve', '可以做'], ['wontfix', '不改']], question: [['approve', '按我的意见做'], ['wontfix', '不改']], approved: [['wontfix', '不改']], doing: [],
    review: [['accept', '通过'], ['rework', '再改…'], ['revert', '改回来']], done: [['reopen', '重新打开']], wontfix: [['reopen', '重新打开']] };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ago = t => { const d = Date.now() / 1000 - t; return d < 60 ? '刚刚' : d < 3600 ? Math.floor(d / 60) + ' 分钟前' : d < 86400 ? Math.floor(d / 3600) + ' 小时前' : new Date(t * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' ' + new Date(t * 1000).toTimeString().slice(0, 5); };
  // 页面标识：相对站点根的路径 + 查询参数（去掉批注自己的参数）+ 单页应用的 #/ 路由
  // 页面标识：相对网站根路径的路径 + 查询参数（去掉批注自己的参数）+ 单页应用的 #/ 路由
  const cleanQS = () => { const s = new URLSearchParams(location.search); ['fb', 'fbid', 'v', 'utm_source', 'utm_medium', 'utm_campaign'].forEach(k => s.delete(k)); return s.toString(); };
  function pageKey() { let p = location.pathname; if (p.startsWith(BASE)) p = p.slice(BASE.length); else p = p.replace(/^\//, ''); if (!p || p.endsWith('/')) p += 'index.html';
    const q = cleanQS(); return p + (q ? '?' + q : '') + (location.hash.startsWith('#/') ? location.hash : ''); }
  const pageURL = () => { const q = cleanQS(); return location.origin + location.pathname + (q ? '?' + q : '') + (location.hash.startsWith('#/') ? location.hash : ''); };
  const pageTitle = () => { const t = document.title || ''; return CFG.titleSuffix && t.endsWith(CFG.titleSuffix) ? t.slice(0, -CFG.titleSuffix.length) : t; };
  const linkOf = it => { const u = it.url || SITE + it.page; return u + (u.includes('?') ? '&' : '?') + 'fbid=' + it.id; };

  async function api(path, opt = {}) {
    const r = await fetch(API + path, { method: opt.body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-FB-Code': opt.code || LS.get('code'), 'X-FB-Name': encodeURIComponent(LS.get('name')), 'X-FB-Cid': LS.get('cid') }, body: opt.body ? JSON.stringify(opt.body) : undefined, cache: 'no-store' });
    const j = await r.json().catch(() => ({ error: '服务器没有响应（' + r.status + '）' })); if (!r.ok) throw Object.assign(new Error(j.error || '出错了'), { status: r.status }); return j; }

  // ---------- 外壳 ----------
  const host = document.createElement('div'); host.id = 'apf-host'; host.style.cssText = 'position:absolute;top:0;left:0;width:0;height:0;z-index:2147483600;';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
  :host { all: initial; }
  * { box-sizing: border-box; font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
  button { font: inherit; cursor: pointer; }
  .dock { position: fixed; left: 16px; bottom: 16px; display: flex; align-items: center; gap: 2px; padding: 4px; background: #1f2430; color: #fff; border-radius: 999px; box-shadow: 0 6px 24px rgba(15,20,35,.28); font-size: 13px; }
  .dock button { border: 0; background: transparent; color: inherit; padding: 7px 12px; border-radius: 999px; display: flex; align-items: center; gap: 6px; white-space: nowrap; }
  .dock button:hover { background: rgba(255,255,255,.12); }
  .dock .main { background: #F59E0B; color: #1f2430; font-weight: 600; }
  .dock .main:hover { background: #FBBF24; }
  .dock.picking .main { background: #fff; }
  .dock a.lkb { color: inherit; text-decoration: none; padding: 7px 12px; border-radius: 999px; display: flex; align-items: center; gap: 6px; white-space: nowrap; background: rgba(255,255,255,.1); }
  .dock a.lkb:hover { background: rgba(255,255,255,.2); }
  .dock .n { min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: rgba(255,255,255,.18); font-size: 11.5px; display: inline-grid; place-items: center; }
  .dock .more { position: relative; }
  .menu { position: absolute; bottom: calc(100% + 8px); left: 0; background: #fff; color: #1f2430; border-radius: 10px; box-shadow: 0 8px 28px rgba(15,20,35,.22); padding: 6px; min-width: 180px; display: none; }
  .menu.on { display: block; }
  .menu button, .menu a { display: block; width: 100%; text-align: left; padding: 8px 10px; border: 0; background: none; border-radius: 6px; color: inherit; text-decoration: none; font-size: 13px; }
  .menu button:hover, .menu a:hover { background: #f2f3f6; }
  .menu .who { padding: 6px 10px 8px; color: #6b7280; font-size: 12px; border-bottom: 1px solid #eee; margin-bottom: 4px; }
  .hl { position: fixed; pointer-events: none; border: 2px solid #F59E0B; background: rgba(245,158,11,.08); border-radius: 4px; display: none; transition: all .06s; }
  .hl b { position: absolute; left: -2px; top: -22px; background: #F59E0B; color: #1f2430; font: 600 11px/1 system-ui; padding: 4px 6px; border-radius: 4px 4px 4px 0; white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
  .pin { position: absolute; width: 24px; height: 24px; margin: -24px 0 0 -2px; border-radius: 12px 12px 12px 2px; color: #fff; font: 600 11.5px/24px system-ui; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,.25); cursor: pointer; border: 2px solid #fff; pointer-events: auto; }
  .pin:hover, .pin.flash { transform: scale(1.18); transform-origin: 0 100%; }
  .pin.flash { animation: fl 1s 2; }
  @keyframes fl { 50% { box-shadow: 0 0 0 10px rgba(245,158,11,.35); } }
  .pop { position: fixed; width: 340px; max-width: calc(100vw - 24px); background: #fff; color: #1f2430; border-radius: 12px; box-shadow: 0 12px 40px rgba(15,20,35,.28); padding: 14px; font-size: 13px; }
  .pop h4 { margin: 0 0 8px; font-size: 14px; }
  .quote { margin: 0 0 10px; padding: 6px 10px; border-left: 3px solid #F59E0B; background: #fffbeb; color: #57534e; font-size: 12.5px; max-height: 64px; overflow: auto; border-radius: 0 6px 6px 0; word-break: break-all; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 8px; align-items: center; }
  .chips span { color: #6b7280; font-size: 12px; margin-right: 2px; }
  .chip { border: 1px solid #d9dce3; background: #fff; color: #374151; border-radius: 999px; padding: 3px 10px; font-size: 12px; }
  .chip[aria-pressed="true"] { background: #1f2430; border-color: #1f2430; color: #fff; }
  textarea, input[type=text], input[type=password] { width: 100%; border: 1px solid #d9dce3; border-radius: 8px; padding: 8px 10px; font: 13px/1.6 system-ui, sans-serif; color: #1f2430; resize: vertical; outline: none; background: #fff; }
  textarea:focus, input:focus { border-color: #1D4ED8; box-shadow: 0 0 0 3px rgba(29,78,216,.12); }
  .row { display: flex; gap: 8px; justify-content: flex-end; align-items: center; margin-top: 10px; }
  .row .tip { margin-right: auto; color: #9ca3af; font-size: 11.5px; }
  .btn { border: 1px solid #d9dce3; background: #fff; color: #1f2430; border-radius: 8px; padding: 6px 14px; font-size: 13px; }
  .btn.pri { background: #1f2430; border-color: #1f2430; color: #fff; }
  .btn:disabled { opacity: .5; cursor: default; }
  .err { color: #B91C1C; font-size: 12px; margin-top: 6px; min-height: 1em; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 400px; max-width: 100vw; background: #f7f8fa; color: #1f2430; box-shadow: -8px 0 32px rgba(15,20,35,.18); display: none; flex-direction: column; font-size: 13px; }
  .drawer.on { display: flex; }
  .dh { padding: 14px 16px 10px; background: #fff; border-bottom: 1px solid #e5e7eb; }
  .dh .t { display: flex; align-items: center; gap: 8px; }
  .dh .t b { font-size: 15px; flex: 1; }
  .dh .x { border: 0; background: none; font-size: 20px; line-height: 1; color: #6b7280; padding: 4px 6px; }
  .tabs { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
  .list { flex: 1; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; }
  .card.cur { border-color: #F59E0B; box-shadow: 0 0 0 3px rgba(245,158,11,.18); }
  .card .h { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: 12px; color: #6b7280; }
  .card .id { font-weight: 700; color: #1f2430; cursor: pointer; }
  .tag { padding: 1px 7px; border-radius: 999px; background: #f2f3f6; color: #374151; font-size: 11.5px; }
  .tag.hi { background: #FEE2E2; color: #B91C1C; } .tag.asg { background: #E0F2FE; color: #075985; }
  .st { margin-left: auto; border: 1px solid; border-radius: 999px; padding: 1px 4px 1px 8px; font-size: 11.5px; background: #fff; }
  .card .b { margin: 8px 0 0; white-space: pre-wrap; word-break: break-word; line-height: 1.65; font-size: 13.5px; }
  .card .pg { display: block; margin-top: 6px; font-size: 12px; color: #1D4ED8; text-decoration: none; word-break: break-all; }
  .card .quote { margin: 8px 0 0; }
  .reps { margin-top: 8px; border-top: 1px dashed #e5e7eb; padding-top: 6px; display: flex; flex-direction: column; gap: 4px; }
  .rep { font-size: 12.5px; line-height: 1.6; color: #374151; white-space: pre-wrap; word-break: break-word; }
  .rep i { font-style: normal; color: #6b7280; }
  .rep.sys { color: #6b7280; }
  .ra { display: flex; gap: 6px; margin-top: 8px; }
  .ra input { flex: 1; padding: 5px 8px; font-size: 12.5px; }
  .ra .btn { padding: 4px 10px; font-size: 12px; }
  .lk { border: 0; background: none; color: #6b7280; font-size: 12px; padding: 0; text-decoration: underline; }
  .empty { color: #6b7280; text-align: center; padding: 40px 12px; line-height: 1.8; }
  .gone { font-size: 11.5px; color: #B45309; }
  .toast { position: fixed; left: 50%; bottom: 72px; transform: translateX(-50%); background: #1f2430; color: #fff; padding: 8px 14px; border-radius: 8px; font-size: 13px; display: none; }
  .mask { position: fixed; inset: 0; background: rgba(15,20,35,.35); display: grid; place-items: center; }
  .login { width: 360px; max-width: calc(100vw - 32px); background: #fff; border-radius: 14px; padding: 20px; color: #1f2430; font-size: 13px; box-shadow: 0 20px 60px rgba(15,20,35,.3); }
  .login h3 { margin: 0 0 6px; font-size: 17px; }
  .login p { margin: 0 0 14px; color: #6b7280; line-height: 1.7; }
  .login label { display: block; margin: 10px 0 4px; font-weight: 600; }
  .att { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0 0; }
  .th { position: relative; width: 92px; height: 64px; border-radius: 8px; overflow: hidden; border: 1px solid #e5e7eb; background: #f3f4f6 center/cover no-repeat; font-size: 11px; color: #6b7280; display: grid; place-items: center; text-align: center; line-height: 1.3; padding: 2px; }
  .th.shot::after { content: "位置截图"; position: absolute; left: 0; right: 0; bottom: 0; background: rgba(31,36,48,.72); color: #fff; font-size: 10.5px; text-align: center; padding: 1px 0; }
  .th button { position: absolute; top: 2px; right: 2px; width: 18px; height: 18px; border-radius: 9px; border: 0; background: rgba(0,0,0,.6); color: #fff; font-size: 12px; line-height: 18px; padding: 0; }
  .addimg { font-size: 12px; color: #1D4ED8; cursor: pointer; border: 1px dashed #93C5FD; border-radius: 8px; padding: 0 10px; height: 30px; display: inline-flex; align-items: center; }
  .addimg input { display: none; }
  .shots { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; } .shots img { height: 76px; max-width: 100%; border-radius: 8px; border: 1px solid #e5e7eb; cursor: zoom-in; object-fit: cover; background: #f3f4f6; }
  .shots img.big { height: auto; width: 100%; max-height: 180px; object-fit: cover; object-position: top; }
  .rep.ai { background: #EEF2FF; border-radius: 8px; padding: 6px 8px; color: #1E3A8A; } .rep.ai i::before { content: "AI"; font-style: normal; font-size: 10px; font-weight: 700; background: #4F46E5; color: #fff; border-radius: 4px; padding: 0 4px; margin-right: 4px; }
  .rep .shots img { height: 54px; }
  .ra .imgb { font-size: 12px; color: #1D4ED8; cursor: pointer; white-space: nowrap; align-self: center; } .ra .imgb input { display: none; }
  .pend { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px; } .pend span { font-size: 11px; color: #6b7280; background: #f3f4f6; border-radius: 6px; padding: 1px 6px; }
  .stag { margin-left: auto; border-radius: 999px; padding: 1px 8px; font-size: 11.5px; color: #fff; }
  .owner { font-size: 10.5px; font-weight: 700; color: #B91C1C; background: #FEE2E2; border-radius: 4px; padding: 0 4px; margin-left: 2px; }
  .dec { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; padding: 8px 10px; border-radius: 10px; background: #FFF7ED; border: 1px solid #FED7AA; }
  .dec > span { font-size: 12px; color: #9A3412; font-weight: 600; margin-right: 2px; }
  .dec button { border: 1px solid #FDBA74; background: #fff; color: #9A3412; border-radius: 999px; padding: 4px 12px; font-size: 12.5px; }
  .dec button.go { background: #EA580C; border-color: #EA580C; color: #fff; } .dec button:hover { filter: brightness(.96); }
  .rep.decision { background: #FEF2F2; border-radius: 8px; padding: 6px 8px; color: #7F1D1D; } .rep.decision i::before { content: "决定"; font-style: normal; font-size: 10px; font-weight: 700; background: #DC2626; color: #fff; border-radius: 4px; padding: 0 4px; margin-right: 4px; }
  .box { border: 1px solid #E0E7FF; background: #F8FAFF; border-radius: 10px; padding: 10px; margin: 4px 0; white-space: normal; }
  .box h5 { margin: 0 0 6px; font-size: 12px; color: #4338CA; } .box p { margin: 0 0 6px; white-space: pre-wrap; color: #1f2430; font-size: 13px; }
  .box ul { margin: 0 0 6px; padding-left: 18px; color: #374151; font-size: 12.5px; } .box a { color: #1D4ED8; }
  .box .opt { display: block; padding: 6px 8px; border-radius: 8px; background: #fff; border: 1px solid #E5E7EB; margin-top: 6px; font-size: 12.5px; color: #374151; }
  .box .opt b { color: #4338CA; margin-right: 4px; } .box .opt button { float: right; }
  .ba { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 6px 0; } .ba figure { margin: 0; } .ba figcaption { font-size: 11px; color: #6b7280; margin-bottom: 2px; }
  .ba img { width: 100%; height: 96px; object-fit: cover; object-position: top; border-radius: 8px; border: 1px solid #e5e7eb; cursor: zoom-in; background: #f3f4f6; }
  .box .rb { font-size: 11.5px; color: #6b7280; }
  .boxov { position: fixed; inset: 0; cursor: crosshair; touch-action: none; background: rgba(15,20,35,.06); }
  .boxov .r { position: fixed; border: 2px solid #F59E0B; background: rgba(245,158,11,.12); border-radius: 4px; display: none; pointer-events: none; }
  .boxov .h { position: fixed; top: 12px; left: 50%; transform: translateX(-50%); background: #1f2430; color: #fff; padding: 8px 14px; border-radius: 999px; font-size: 13px; pointer-events: none; white-space: nowrap; }
  .dock.boxing .boxb { background: #fff; color: #1f2430; }
  .mic { white-space: nowrap; height: 30px; border: 1px solid #d9dce3; background: #fff; color: #1f2430; border-radius: 999px; padding: 4px 12px; font-size: 12.5px; }
  .mic.rec { background: #DC2626; border-color: #DC2626; color: #fff; animation: rp 1.2s infinite; }
  @keyframes rp { 50% { box-shadow: 0 0 0 6px rgba(220,38,38,.18); } }
  .row .btn { white-space: nowrap; }
  .mk { white-space: nowrap; font-size: 12px; color: #B91C1C; cursor: pointer; border: 1px dashed #FCA5A5; border-radius: 8px; padding: 0 10px; height: 30px; display: inline-flex; align-items: center; background: #fff; }
  @media (max-width: 640px) { .dock { left: 8px; bottom: 8px; } .dock .lbl { display: none; } .drawer { width: 100vw; } }
  </style>
  <div class="pins"></div><div class="hl"><b></b></div>
  <div class="dock" hidden><button class="main" title="点选页面上要批注的位置"><span>✎</span><span class="lbl">批注</span></button><button class="boxb" title="拖一个框，框出要改的区域"><span>▭</span><span class="lbl">框选</span></button><button class="cnt" title="本页批注"><span class="lbl">本页</span><span class="n">0</span></button><span class="more"><button class="mb" title="更多">⋯</button><div class="menu"><div class="who"></div><button data-a="pins">隐藏页面上的标记</button><button data-a="all">全站批注</button><a data-a="sum" target="_blank">汇总页 / 导出给 agent ↗</a><span class="lks"></span><button data-a="name">改姓名</button><button data-a="out">退出批注</button></div></span></div>
  <div class="drawer"><div class="dh"><div class="t"><b>批注</b><button class="x" title="关闭">×</button></div><div class="tabs"></div></div><div class="list"></div></div>
  <div class="toast"></div>`;
  /* 严格 CSP（style-src 只放行 nonce 的页面）会拦住内联 <style> 和 style=""：样式改用可构造样式表，style 属性用 CSSOM 补上；没有 CSP 的页面不受影响。来自好设计站上的修复。 */
  const fixStyleAttrs = n => { if (!n || !n.querySelectorAll) return; const els = [...n.querySelectorAll('[style]')]; if (n.matches && n.matches('[style]')) els.push(n); for (const el of els) { const v = el.getAttribute('style'); if (v && !el.style.length) el.style.cssText = v; } };
  try { const st = root.querySelector('style'); if (st && !st.sheet && 'adoptedStyleSheets' in root) { const sh = new CSSStyleSheet(); sh.replaceSync(st.textContent); root.adoptedStyleSheets = [sh]; } } catch (e) {}
  fixStyleAttrs(root); new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(fixStyleAttrs))).observe(root, { childList: true, subtree: true });

  const media = new Promise(r => { if (window.APFMedia) return r(); const sc = document.createElement('script'); sc.src = new URL('fb-media.js', SRC).href; sc.onload = sc.onerror = () => r(); document.head.append(sc); });
  const M = () => window.APFMedia;
  const $ = s => root.querySelector(s), dock = $('.dock'), pinsEl = $('.pins'), hl = $('.hl'), drawer = $('.drawer'), list = $('.list');
  (document.body || document.documentElement).appendChild(host);
  $('[data-a="sum"]').href = SUMMARY;

  let items = [], allItems = null, picking = false, showPins = LS.get('pins') !== 'off', scope = 'page', filt = 'todo', cur = 0, role = '', page = pageKey();
  const toast = t => { const e = $('.toast'); e.textContent = t; e.style.display = 'block'; clearTimeout(toast.t); toast.t = setTimeout(() => e.style.display = 'none', 2200); };

  // ---------- 登录 ----------
  function login(msg) {
    const m = document.createElement('div'); m.className = 'mask';
    m.innerHTML = `<div class="login"><h3>加入页面批注</h3><p>${msg || '在网站任意位置点选、写修改意见，团队成员都能看到，交给 agent 修改。负责人用管理员通行码进入，可以拍板和验收。'}</p>
      <label>通行码</label><input type="password" class="c" autocomplete="off" value="${esc(fbParam && fbParam.length > 3 ? fbParam : LS.get('code'))}">
      <label>你的姓名</label><input type="text" class="nm" maxlength="20" placeholder="批注上会显示这个名字" value="${esc(LS.get('name'))}">
      <div class="err"></div><div class="row"><button class="btn cx">取消</button><button class="btn pri ok">进入</button></div></div>`;
    root.append(m); const c = m.querySelector('.c'), nm = m.querySelector('.nm'), er = m.querySelector('.err');
    (c.value ? nm : c).focus();
    const go = async () => { if (!c.value.trim() || !nm.value.trim()) { er.textContent = '口令和姓名都要填'; return; }
      try { const r = await api('ping', { code: c.value.trim() }); LS.set('code', c.value.trim()); LS.set('name', nm.value.trim()); role = r.role; m.remove(); stripParam(); start(); toast(role === 'admin' ? '已用负责人身份进入：你的「可以做 / 通过」就是 agent 的执行信号' : '已进入，可以开始批注'); }
      catch (e) { er.textContent = e.message; } };
    m.querySelector('.ok').onclick = go; m.addEventListener('keydown', e => { if (e.key === 'Enter') go(); if (e.key === 'Escape') { m.remove(); stripParam(); } });
    m.querySelector('.cx').onclick = () => { m.remove(); stripParam(); };
  }
  function stripParam() { const u = new URL(location.href); if (!u.searchParams.has('fb')) return; u.searchParams.delete('fb'); history.replaceState(history.state, '', u); }
  function askName() { const n = prompt('批注上显示的姓名', LS.get('name')); if (n && n.trim()) { LS.set('name', n.trim().slice(0, 20)); $('.who').textContent = '我是 ' + LS.get('name'); } }

  // ---------- 定位 ----------
  function selOf(el) { const parts = [];
    while (el && el.nodeType === 1 && el !== document.body && el !== document.documentElement) {
      if (el.id && !/^\d|:/.test(el.id) && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) { parts.unshift('#' + CSS.escape(el.id)); break; }
      let s = el.tagName.toLowerCase(); const p = el.parentElement;
      if (p) { const sib = [...p.children].filter(x => x.tagName === el.tagName); if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(el) + 1) + ')'; }
      parts.unshift(s); el = p; }
    return (parts[0] && parts[0][0] === '#' ? '' : 'body > ') + parts.join(' > '); }
  const textOf = el => (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('alt') || '').replace(/\s+/g, ' ').trim();
  function findByText(t) { if (!t || t.length < 4) return null; const key = t.slice(0, 24), w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n; while ((n = w.nextNode())) { if (n.parentElement && !host.contains(n.parentElement) && n.nodeValue.replace(/\s+/g, ' ').includes(key)) return n.parentElement; } return null; }
  function locate(it) { let el = null; try { el = it.selector ? document.querySelector(it.selector) : null; } catch (e) {}
    if (el && it.quote && !textOf(el).includes(it.quote.slice(0, 12)) && textOf(el).length) { const alt = findByText(it.quote); if (alt) el = alt; }
    if (!el && it.quote) el = findByText(it.quote);
    if (el) { const r = el.getBoundingClientRect(); if (r.width || r.height) return { x: r.left + scrollX + r.width * (it.rx || 0), y: r.top + scrollY + r.height * (it.ry || 0), ok: true }; }
    return it.px || it.py ? { x: it.px, y: it.py, ok: false } : null; }

  // ---------- 标记 ----------
  const open = it => it.status !== 'done' && it.status !== 'wontfix';
  function renderPins() { pinsEl.innerHTML = ''; if (!showPins) return;
    items.filter(it => open(it) || it.id === cur).forEach(it => { const p = locate(it); if (!p) return; const d = document.createElement('div'); d.className = 'pin' + (it.id === cur ? ' flash' : ''); d.style.left = p.x + 'px'; d.style.top = p.y + 'px'; d.style.background = SCOL[it.status] || '#C2410C'; d.textContent = it.id; d.dataset.id = it.id; d.title = it.author + '：' + it.body.slice(0, 60); d.onclick = () => openDrawer('page', it.id); pinsEl.append(d); }); }
  function movePins() { pinsEl.querySelectorAll('.pin').forEach(d => { const it = items.find(x => x.id === +d.dataset.id), p = it && locate(it); if (p) { d.style.left = p.x + 'px'; d.style.top = p.y + 'px'; } }); }
  function counts() { const n = items.filter(open).length; $('.cnt .n').textContent = n; }

  async function load() { page = pageKey(); try { const r = await api('items?page=' + encodeURIComponent(page)); role = r.role; items = r.items; links(r.links); counts(); renderPins(); if (drawer.classList.contains('on')) renderList(); }
    catch (e) { if (e.status === 401) { LS.set('code', ''); dock.hidden = true; login('口令已失效，请重新输入。'); } } }

  // ---------- 点选 ----------
  const skip = el => !el || el === host || host.contains(el) || el === document.documentElement || el === document.body;
  function onMove(e) { if (!picking) return; const el = document.elementFromPoint(e.clientX, e.clientY); if (skip(el)) { hl.style.display = 'none'; return; }
    const r = el.getBoundingClientRect(); Object.assign(hl.style, { display: 'block', left: r.left - 2 + 'px', top: r.top - 2 + 'px', width: r.width + 4 + 'px', height: r.height + 4 + 'px' });
    hl.querySelector('b').textContent = (textOf(el).slice(0, 28) || el.tagName.toLowerCase()); }
  function onClick(e) { if (!picking) return; if (e.composedPath().includes(host)) return; e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    const sel = getSelection(), st = sel && sel.toString().replace(/\s+/g, ' ').trim(); let el, quote;
    if (st) { const a = sel.getRangeAt(0).commonAncestorContainer; el = a.nodeType === 1 ? a : a.parentElement; quote = st.slice(0, 300); }
    else { el = document.elementFromPoint(e.clientX, e.clientY); if (skip(el)) return; quote = textOf(el).slice(0, 120); }
    const r = el.getBoundingClientRect();
    setPick(false); form({ el, quote, rx: r.width ? Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) : 0, ry: r.height ? Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) : 0, cx: e.clientX, cy: e.clientY }); }
  function onKey(e) { if (e.key === 'Escape' && picking) setPick(false); }
  function setPick(on) { if (on && boxing) setBox(false); picking = on; dock.classList.toggle('picking', on); $('.main .lbl').textContent = on ? '点选位置，可先划选文字 · Esc 取消' : '批注'; hl.style.display = 'none';
    document.documentElement.style.cursor = on ? 'crosshair' : ''; }
  // ---------- 框选：拖一个框（鼠标或手指） ----------
  let boxing = null;
  function setBox(on) {
    if (boxing) { boxing.remove(); boxing = null; } dock.classList.toggle('boxing', on); if (!on) return; setPick(false);
    const ov = document.createElement('div'); ov.className = 'boxov'; ov.innerHTML = '<div class="r"></div><div class="h">拖一个框，框出要改的区域 · Esc 取消</div>'; root.append(ov); boxing = ov;
    const rb = ov.querySelector('.r'); let a = null, b = null;
    const rect = () => ({ left: Math.min(a[0], b[0]), top: Math.min(a[1], b[1]), width: Math.abs(b[0] - a[0]), height: Math.abs(b[1] - a[1]) });
    ov.addEventListener('pointerdown', e => { e.preventDefault(); ov.setPointerCapture(e.pointerId); a = b = [e.clientX, e.clientY]; });
    ov.addEventListener('pointermove', e => { if (!a) return; b = [e.clientX, e.clientY]; const r = rect(); Object.assign(rb.style, { display: 'block', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' }); });
    const up = () => { if (!a) return; const r = rect(); a = null; if (r.width < 12 || r.height < 12) { rb.style.display = 'none'; return; }
      setBox(false); const el = boxEl(r); if (!el) return;
      const er = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      form({ el, quote: textOf(el).slice(0, 120), rect: r, rx: er.width ? Math.min(1, Math.max(0, (cx - er.left) / er.width)) : 0, ry: er.height ? Math.min(1, Math.max(0, (cy - er.top) / er.height)) : 0, cx: r.left + r.width, cy: r.top }); };
    ov.addEventListener('pointerup', up); ov.addEventListener('pointercancel', () => { a = null; rb.style.display = 'none'; });
  }
  // 框里的元素：取框的中心和四角所在元素的共同祖先
  function boxEl(r) {
    const pts = [[.5, .5], [.15, .15], [.85, .15], [.15, .85], [.85, .85]].map(([x, y]) => document.elementFromPoint(r.left + r.width * x, r.top + r.height * y)).filter(el => !skip(el));
    if (!pts.length) return null; let el = pts[0];
    while (el && !pts.every(x => el.contains(x))) el = el.parentElement;
    return !el || el === document.documentElement ? pts[0] : el === document.body ? pts[0] : el; }
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && boxing) setBox(false); }, true);
  document.addEventListener('mousemove', onMove, true); document.addEventListener('click', onClick, true); document.addEventListener('keydown', onKey, true);

  // ---------- 新建批注 ----------
  function chips(name, map, val) { return `<div class="chips" data-n="${name}"><span>${name === 'kind' ? '类型' : '优先级'}</span>${Object.entries(map).map(([k, v]) => `<button class="chip" data-v="${k}" aria-pressed="${k === val}">${v}</button>`).join('')}</div>`; }
  function form(t) { root.querySelectorAll('.pop').forEach(x => x.remove()); const p = document.createElement('div'); p.className = 'pop';
    p.innerHTML = `<h4>写批注</h4>${t.quote ? `<p class="quote">${esc(t.quote)}</p>` : ''}${chips('kind', KIND, 'suggest')}${chips('prio', PRIO, 'mid')}
      <textarea rows="4" placeholder="哪里不对、希望怎么改？写得越具体，agent 越好改（可直接粘贴图片，也可以点「说话」）"></textarea>
      <div class="att"><div class="th shot" data-shot>正在截取<br>${t.rect ? '框选区域' : '批注位置'}…</div><label class="addimg">＋ 添加图片<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label><button class="mic" type="button" title="用语音输入意见">🎤 说话</button></div><div class="err"></div>
      <div class="row"><span class="tip">${innerWidth < 640 ? '' : 'Ctrl / ⌘ + Enter 提交'}</span><button class="btn cx">取消</button><button class="btn pri ok">提交</button></div>`;
    root.append(p); const W = 340, H = p.offsetHeight || 300; p.style.left = Math.max(12, Math.min(innerWidth - W - 12, t.cx + 12)) + 'px'; p.style.top = Math.max(12, Math.min(innerHeight - H - 12, t.cy + 12)) + 'px';
    p.querySelectorAll('.chips').forEach(g => g.onclick = e => { const b = e.target.closest('.chip'); if (!b) return; g.querySelectorAll('.chip').forEach(x => x.setAttribute('aria-pressed', x === b)); });
    const ta = p.querySelector('textarea'), er = p.querySelector('.err'), ok = p.querySelector('.ok'), att = p.querySelector('.att'), shEl = p.querySelector('[data-shot]'); ta.focus();
    let shot = null, shotMarks = []; const imgs = [];
    const showShot = u => { shot = u; shEl.style.backgroundImage = `url(${u})`; shEl.textContent = ''; shEl.innerHTML = '<button title="不要截图">×</button>';
      shEl.querySelector('button').onclick = () => { shot = null; shotMarks = []; shEl.remove(); mk.remove(); }; shEl.onclick = e => { if (!e.target.closest('button')) M().lightbox(shot, '批注位置截图'); }; };
    // 在截图上圈出要改的地方（每次都在原始截图上画，所以可以重画）
    const mk = document.createElement('button'); mk.type = 'button'; mk.className = 'mk'; mk.textContent = '✎ 在截图上圈出'; let raw = null;
    mk.onclick = async () => { const r = await M().editImage(raw); if (!r) return; showShot(r.url); shotMarks = r.marks; mk.textContent = r.marks.length ? `✎ 已圈出 ${r.marks.length} 处 · 重画` : '✎ 在截图上圈出'; };
    const shotP = media.then(() => M() ? M().capture(t.el, t.rect ? { rect: t.rect } : { rx: t.rx, ry: t.ry }) : Promise.reject(new Error('截图组件没加载')))
      .then(u => { raw = u; showShot(u); if (M().editImage) shEl.after(mk); })
      .catch(() => { shEl.textContent = '没截到图，可手动添加'; shEl.style.fontSize = '10.5px'; });
    // 语音输入：浏览器自带的语音识别（Chrome、Edge、安卓、较新的 iPhone Safari）；不支持时提示用键盘上的话筒
    const mic = p.querySelector('.mic'), SR = window.SpeechRecognition || window.webkitSpeechRecognition; let rec = null;
    const stopMic = () => { if (rec) { try { rec.stop(); } catch (e) {} } };
    mic.onclick = () => {
      if (rec) return stopMic();
      if (!SR || /MicroMessenger/i.test(navigator.userAgent)) { ta.focus(); er.textContent = '这个浏览器不能直接识别语音：请点手机键盘上的话筒按钮说话（电脑上建议用 Chrome）'; return; }
      const base = ta.value; let fin = '', shown = base;
      try { rec = new SR(); } catch (e) { er.textContent = '语音识别启动失败，可以用键盘上的话筒说话'; return; }
      rec.lang = 'zh-CN'; rec.interimResults = true; rec.continuous = true;
      rec.onresult = e => { let mid = ''; for (let i = e.resultIndex; i < e.results.length; i++) { const x = e.results[i]; if (x.isFinal) fin += x[0].transcript; else mid += x[0].transcript; } if (ta.value !== shown) return stopMic(); ta.value = shown = base + fin + mid; };   // 用户自己改了文字就停，不覆盖
      rec.onerror = e => { er.textContent = e.error === 'not-allowed' || e.error === 'service-not-allowed' ? '没有麦克风权限：请在浏览器设置里允许，或用键盘上的话筒说话' : e.error === 'no-speech' ? '没听到声音，再试一次' : '语音识别出错了，可以用键盘上的话筒说话'; };
      rec.onend = () => { rec = null; mic.classList.remove('rec'); mic.textContent = '🎤 说话'; if (ta.value === shown) ta.value = base + fin; };
      er.textContent = ''; rec.start(); mic.classList.add('rec'); mic.textContent = '■ 说完了'; };
    const addImgs = async files => { for (const f of files) { try { const u = await M().readImage(f); imgs.push(u); const d = document.createElement('div'); d.className = 'th'; d.style.backgroundImage = `url(${u})`; d.innerHTML = '<button title="移除">×</button>'; d.querySelector('button').onclick = () => { imgs.splice(imgs.indexOf(u), 1); d.remove(); }; att.insertBefore(d, att.querySelector('.addimg')); } catch (e2) { er.textContent = e2.message; } } };
    att.querySelector('input').onchange = e => { const fs = [...e.target.files]; e.target.value = ''; media.then(() => addImgs(fs)); };
    ta.addEventListener('paste', e => { const fs = [...(e.clipboardData || {}).files || []].filter(f => f.type.startsWith('image/')); if (fs.length) { e.preventDefault(); media.then(() => addImgs(fs)); } });
    const val = n => p.querySelector(`[data-n="${n}"] [aria-pressed="true"]`).dataset.v;
    const submit = async () => { stopMic(); if (!ta.value.trim()) { er.textContent = '请写下意见'; return; } ok.disabled = true; ok.textContent = '提交中…';
      await Promise.race([shotP, new Promise(r2 => setTimeout(r2, 9500))]);
      const r = t.el.getBoundingClientRect();
      try { const j = await api('items', { body: { page, url: pageURL(), title: pageTitle(), selector: selOf(t.el), quote: t.quote, rx: t.rx, ry: t.ry, px: r.left + scrollX + r.width * t.rx, py: r.top + scrollY + r.height * t.ry, vw: innerWidth, vh: innerHeight, device: innerWidth < 640 ? '手机' : innerWidth < 1024 ? '平板' : '桌面', kind: val('kind'), priority: val('prio'), body: ta.value.trim(), shot: shot || undefined, images: imgs,
          marks: (t.rect ? [{ t: 'region', x: Math.round(t.rect.left + scrollX), y: Math.round(t.rect.top + scrollY), w: Math.round(t.rect.width), h: Math.round(t.rect.height) }] : []).concat(shot ? shotMarks : []) } });
        items.unshift(j.item); allItems = null; counts(); renderPins(); p.remove(); toast('已提交 #' + j.item.id); if (drawer.classList.contains('on')) renderList(); }
      catch (e) { er.textContent = e.message; ok.disabled = false; ok.textContent = '提交'; } };
    ok.onclick = submit; p.querySelector('.cx').onclick = () => { stopMic(); p.remove(); };
    ta.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit(); if (e.key === 'Escape') { stopMic(); p.remove(); } }); }

  // ---------- 列表 ----------
  async function openDrawer(sc, id) { scope = sc || scope; cur = id || 0; drawer.classList.add('on');
    if (scope === 'all' && !allItems) { list.innerHTML = '<div class="empty">加载中…</div>'; try { allItems = (await api('items?all=1')).items; } catch (e) { list.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; } }
    renderList(); if (id) { renderPins(); const c = list.querySelector(`[data-id="${id}"]`); if (c) c.scrollIntoView({ block: 'center' }); } }
  function renderList() {
    const src = scope === 'all' ? (allItems || []) : items, shown = src.filter(it => filt === 'all' || open(it));
    $('.dh b').textContent = scope === 'all' ? '全站批注' : '本页批注';
    $('.tabs').innerHTML = [['page', '本页 ' + items.filter(open).length], ['all', '全站']].map(([k, v]) => `<button class="chip" data-sc="${k}" aria-pressed="${scope === k}">${v}</button>`).join('') + '<span style="flex:1"></span>' +
      [['todo', '未完成'], ['all', '全部']].map(([k, v]) => `<button class="chip" data-f="${k}" aria-pressed="${filt === k}">${v}</button>`).join('');
    setTimeout(() => media.then(() => M() && M().hydrate(list)), 0);
    list.innerHTML = shown.length ? shown.map(card).join('') : `<div class="empty">${scope === 'page' ? '这一页还没有' + (filt === 'todo' ? '未完成的' : '') + '批注。<br>点左下角「批注」，再点页面上要改的地方。' : '还没有批注。'}</div>`; }
  function card(it) { const here = it.page === page, loc = here ? locate(it) : null;
    return `<div class="card${it.id === cur ? ' cur' : ''}" data-id="${it.id}"><div class="h"><span class="id" title="在页面上定位">#${it.id}</span><span class="tag${it.kind === 'bug' ? ' hi' : ''}">${KIND[it.kind] || ''}</span>${it.priority === 'high' ? '<span class="tag hi">优先级高</span>' : it.priority === 'low' ? '<span class="tag">优先级低</span>' : ''}<span>${esc(it.author)}${it.arole === 'admin' ? '<b class="owner">负责人</b>' : ''} · ${ago(it.created)}</span>${it.assignee ? `<span class="tag asg">负责方 ${esc(it.assignee)}</span>` : ''}
      <span class="stag" style="background:${SCOL[it.status] || '#6B7280'}">${STAT[it.status] || it.status}</span></div>
      ${scope === 'all' ? `<a class="pg" href="${esc(linkOf(it))}">${esc(it.title || it.page)}</a>` : ''}
      ${(it.files || []).length ? `<div class="shots">${it.files.map(f => `<img data-fid="${f.id}" class="${f.kind === 'shot' ? 'big' : ''}" alt="${f.kind === 'shot' ? '批注位置截图' : '附图'}" title="${f.kind === 'shot' ? '批注位置截图' : '附图'} · 点击放大">`).join('')}</div>` : ''}
      ${it.quote ? `<p class="quote">${esc(it.quote)}</p>` : ''}<p class="b">${esc(it.body)}</p>${here && loc && !loc.ok ? '<div class="gone">页面内容已变化，标记按原坐标显示</div>' : ''}
      ${it.replies.length ? `<div class="reps">${it.replies.map(x => repHTML(x, it)).join('')}</div>` : ''}
      ${role === 'admin' && (DECIDE[it.status] || []).length ? `<div class="dec"><span>${it.status === 'review' ? '验收' : it.status === 'question' ? '请决定' : '负责人'}</span>${DECIDE[it.status].map(([a, n], i) => `<button data-dec="${a}" class="${i === 0 && a !== 'wontfix' && a !== 'reopen' ? 'go' : ''}">${n}</button>`).join('')}</div>` : ''}
      <div class="ra"><input type="text" placeholder="回复…（可粘贴图片）" maxlength="1000"><label class="imgb" title="添加图片">图片<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label><button class="btn rp">回复</button>${it.mine || role === 'admin' ? '<button class="lk del">删除</button>' : ''}</div></div>`; }
  // 一条回复：普通回复 / 状态变化 / 负责人决定 / agent 方案 / agent 交付报告
  function repHTML(x, it) {
    const who = `<i>${esc(x.author)}${x.role === 'admin' ? '（负责人）' : ''}：</i>`, m = x.meta || {}, imgs = fs => fs.length ? `<div class="shots">${fs.map(f => `<img data-fid="${f.id}" alt="附图">`).join('')}</div>` : '';
    if (x.kind === 'check') return `<div class="rep ai">${who}<div class="box"><h5>交叉核对 · ${m.result === 'pass' ? '核对通过' : m.result === 'fail' ? '发现问题' : '意见'}（供负责人参考）</h5><p>${esc(x.body)}</p>${imgs(x.files || [])}</div></div>`;
    if (x.kind === 'proposal') return `<div class="rep ai">${who}<div class="box"><h5>方案 · 请负责人选择</h5><p>${esc(x.body)}</p>${(m.options || []).map(o => `<span class="opt"><b>方案 ${esc(o.key)}</b>${esc(o.title)}${o.desc ? '：' + esc(o.desc) : ''}${o.link ? ` <a href="${esc(o.link)}" target="_blank" rel="noopener">预览 ↗</a>` : ''}${role === 'admin' && it.status === 'question' ? `<button class="btn" data-dec="choose" data-opt="${esc(o.key)}">选 ${esc(o.key)}</button>` : ''}</span>`).join('')}${imgs(x.files || [])}</div></div>`;
    if (x.kind === 'report') { const fb = k => (x.files || []).find(f => f.kind === k), b = fb('before'), a = fb('after'), rest = (x.files || []).filter(f => f.kind === 'image');
      return `<div class="rep ai">${who}<div class="box"><h5>交付报告 · 待负责人验收</h5><p>${esc(x.body)}</p>${m.why ? `<p style="color:#6b7280">原因：${esc(m.why)}</p>` : ''}${(m.changes || []).length ? `<ul>${m.changes.map(c => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}
        ${b || a ? `<div class="ba">${b ? `<figure><figcaption>改前</figcaption><img data-fid="${b.id}" alt="改前"></figure>` : '<span></span>'}${a ? `<figure><figcaption>改后</figcaption><img data-fid="${a.id}" alt="改后"></figure>` : ''}</div>` : ''}${imgs(rest)}
        ${(m.links || []).length ? `<p>${m.links.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name || '查看')} ↗</a>`).join(' · ')}</p>` : ''}${m.rollback ? `<span class="rb">回滚编号：${esc(m.rollback)}</span>` : ''}</div></div>`; }
    return `<div class="rep${x.kind === 'status' ? ' sys' : ''}${x.kind === 'decision' ? ' decision' : ''}${x.ai ? ' ai' : ''}">${who}${esc(x.body)}${imgs(x.files || [])}</div>`; }
  function replace(it) { [items, allItems || []].forEach(a => { const i = a.findIndex(x => x.id === it.id); if (i >= 0) a[i] = Object.assign({}, a[i], it); }); counts(); renderPins(); renderList(); }
  drawer.addEventListener('click', async e => { const t = e.target;
    if (t.closest('.x')) { drawer.classList.remove('on'); cur = 0; renderPins(); return; }
    if (t.dataset.sc) { openDrawer(t.dataset.sc); return; } if (t.dataset.f) { filt = t.dataset.f; renderList(); return; }
    const c = t.closest('.card'); if (!c) return; const id = +c.dataset.id, it = (items.find(x => x.id === id) || (allItems || []).find(x => x.id === id));
    if (t.closest('.id')) { if (it.page !== page) { location.href = linkOf(it); return; } cur = id; renderPins(); renderList(); const p = locate(it); if (p) scrollTo({ top: Math.max(0, p.y - innerHeight / 3), behavior: 'smooth' }); return; }
    if ((t.matches('.shots img') || t.matches('.ba img')) && t.src) { M().lightbox(t.src, t.alt); return; }
    const dv = t.closest('[data-dec]');
    if (dv) { const a = dv.dataset.dec, body = { action: a, option: dv.dataset.opt || '' };
      if (a === 'rework') { const n = prompt('要怎么再改？（会作为下一轮修改的依据）', ''); if (!n || !n.trim()) return; body.note = n.trim(); }
      else if (a === 'revert') { if (!confirm('让 agent 把 #' + id + ' 的修改改回去？')) return; body.note = prompt('补一句原因（可不填）', '') || ''; }
      else if (a === 'wontfix') { body.note = prompt('不改的原因（可不填）', '') || ''; }
      else if (a === 'approve' && it.status === 'question') { body.note = prompt('按你的意见做：补充说明（可不填）', '') || ''; }
      dv.disabled = true; try { replace((await api(`items/${id}/decide`, { body })).item); toast('已记下：' + dv.textContent.replace('…', '')); } catch (er) { toast(er.message); dv.disabled = false; } return; }
    if (t.closest('.rp')) { const inp = c.querySelector('.ra input'), pend = PEND.get(id) || []; if (!inp.value.trim() && !pend.length) return; t.closest('.rp').disabled = true;
      try { replace((await api(`items/${id}/reply`, { body: { body: inp.value.trim(), images: pend } })).item); PEND.delete(id); } catch (er) { toast(er.message); t.closest('.rp').disabled = false; } return; }
    if (t.closest('.del')) { if (!confirm('删除批注 #' + id + '？')) return; try { await api(`items/${id}/delete`, { body: {} }); items = items.filter(x => x.id !== id); if (allItems) allItems = allItems.filter(x => x.id !== id); counts(); renderPins(); renderList(); } catch (er) { toast(er.message); } } });
  const PEND = new Map();   // 回复里待发送的图片：批注 id → [data URL]
  const addPend = async (card, files) => { await media; const id = +card.dataset.id, a = PEND.get(id) || []; for (const f of files) { try { a.push(await M().readImage(f)); } catch (e2) { toast(e2.message); } } PEND.set(id, a);
    let box = card.querySelector('.pend'); if (!box) { box = document.createElement('div'); box.className = 'pend'; card.querySelector('.ra').after(box); } box.innerHTML = a.map((_, i) => `<span>图片 ${i + 1}</span>`).join('') + '<span>（点「回复」一起发送）</span>'; };
  drawer.addEventListener('paste', e => { const inp = e.target.closest && e.target.closest('.ra input'); if (!inp) return; const fs = [...(e.clipboardData || {}).files || []].filter(f => f.type.startsWith('image/')); if (fs.length) { e.preventDefault(); addPend(inp.closest('.card'), fs); } });
  drawer.addEventListener('change', async e => { if (e.target.matches('.imgb input')) { addPend(e.target.closest('.card'), [...e.target.files]); e.target.value = ''; return; } });
  drawer.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('.ra input')) e.target.closest('.card').querySelector('.rp').click(); e.stopPropagation(); });

  // ---------- 批注条 ----------
  $('.main').onclick = () => setPick(!picking);
  $('.boxb').onclick = () => setBox(!boxing);
  $('.cnt').onclick = () => drawer.classList.contains('on') && scope === 'page' ? (drawer.classList.remove('on'), cur = 0, renderPins()) : openDrawer('page');
  const menu = $('.menu'); $('.mb').onclick = e => { e.stopPropagation(); menu.classList.toggle('on'); };
  document.addEventListener('click', () => menu.classList.remove('on'));
  menu.onclick = e => { const a = e.target.dataset.a; if (!a) return; menu.classList.remove('on');
    if (a === 'pins') { showPins = !showPins; LS.set('pins', showPins ? '' : 'off'); e.target.textContent = showPins ? '隐藏页面上的标记' : '显示页面上的标记'; renderPins(); }
    if (a === 'all') openDrawer('all'); if (a === 'name') askName();
    if (a === 'out') { if (!confirm('退出后左下角的批注条会隐藏。之后用带 ?fb=1 的链接可以重新进入。')) return; LS.set('code', ''); host.remove(); } };

  let linkKey = null;   // 站点配置的快捷入口（服务器 FB_LINKS，按身份过滤）：放进「⋯」菜单，dock 为 true 的也放到批注条上
  function links(ls) { ls = Array.isArray(ls) ? ls : []; const k = JSON.stringify(ls); if (k === linkKey) return; linkKey = k;
    $('.lks').innerHTML = ls.map(l => `<a href="${esc(l.url)}">${esc(l.name)} ↗</a>`).join('');
    dock.querySelectorAll('a.lkb').forEach(x => x.remove());
    ls.filter(l => l.dock).forEach(l => { const a = document.createElement('a'); a.className = 'lkb'; a.href = l.url; a.title = l.name; a.innerHTML = `<span>▦</span><span class="lbl">${esc(l.name)}</span>`; dock.insertBefore(a, $('.more')); }); }
  function start() { dock.hidden = false; $('.who').textContent = '我是 ' + LS.get('name') + (role === 'admin' ? ' · 负责人' : ''); $('[data-a="pins"]').textContent = showPins ? '隐藏页面上的标记' : '显示页面上的标记';
    load().then(() => { if (focusId) { const u = new URL(location.href); u.searchParams.delete('fbid'); history.replaceState(history.state, '', u); page = pageKey(); setTimeout(() => { openDrawer('page', focusId); const it = items.find(x => x.id === focusId), p = it && locate(it); if (p) scrollTo({ top: Math.max(0, p.y - innerHeight / 3) }); }, 600); } });
    setInterval(() => { if (!document.hidden) load(); }, 60000);
    setInterval(() => { if (items.length && showPins && !document.hidden) movePins(); }, 1500);   // 页面布局变化（图片加载、展开收起）后跟着挪
    addEventListener('resize', movePins); addEventListener('hashchange', () => { if (pageKey() !== page) load(); }); }

  if (fbParam != null && (!LS.get('code') || (fbParam.length > 3 && fbParam !== LS.get('code')))) login();
  else if (LS.get('code')) { stripParam(); if (!LS.get('name')) askName(); start(); }
  else login();
})();
