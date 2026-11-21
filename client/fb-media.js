/* agent-page-feedback · 图片能力（feedback.js 与 feedback.html 共用）：
   - capture(el)：截取批注位置（选中元素及周围一块区域），用橙色框标出选中的元素；用 vendor/html-to-image（MIT）
   - readImage(file)：读入用户添加 / 粘贴的图片，过大的缩到 1800 px 以内转 JPEG
   - hydrate(root)：把 <img data-fid> 换成真实图片（附件读取要带口令，所以先 fetch 再生成 blob 地址）
   - lightbox(src)：点击看大图
   - editImage(src)：在图片上用手指 / 鼠标圈画或画框，返回 {url, marks}；marks 是每一笔的外框（按图片宽高的比例） */
(function () {
  if (window.APFMedia) return;
  const SRC = (document.currentScript && document.currentScript.src) || location.href;
  const API = (window.APF_CONFIG && window.APF_CONFIG.api) || new URL('../', SRC).href, VENDOR = new URL('vendor/', SRC).href;
  const code = () => { try { return localStorage.getItem('apf:code') || ''; } catch (e) { return ''; } };
  const cache = new Map();
  function fileURL(fid) {
    if (!cache.has(fid)) cache.set(fid, fetch(API + 'files/' + fid, { headers: { 'X-FB-Code': code() } }).then(r => { if (!r.ok) throw new Error(r.status); return r.blob(); }).then(b => URL.createObjectURL(b)));
    return cache.get(fid);
  }
  function hydrate(root) {
    root.querySelectorAll('img[data-fid]:not([data-ok])').forEach(img => { img.dataset.ok = 1; fileURL(img.dataset.fid).then(u => { img.src = u; }).catch(() => { img.alt = '图片加载失败'; img.classList.add('broken'); }); });
  }
  function readImage(file) {
    return new Promise((res, rej) => {
      if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) return rej(new Error('只支持 PNG / JPEG / WebP / GIF 图片'));
      const fr = new FileReader();
      fr.onload = () => { const u = fr.result; if (file.type === 'image/gif' || file.size < 700000) return res(u);
        const im = new Image(); im.onload = () => { const k = Math.min(1, 1800 / Math.max(im.width, im.height)), c = document.createElement('canvas'); c.width = Math.round(im.width * k); c.height = Math.round(im.height * k);
          const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.drawImage(im, 0, 0, c.width, c.height); res(c.toDataURL('image/jpeg', .85)); };
        im.onerror = () => res(u); im.src = u; };
      fr.onerror = () => rej(new Error('图片读取失败')); fr.readAsDataURL(file);
    });
  }
  let lib = null;
  const loadLib = () => lib || (lib = new Promise((res, rej) => { if (window.htmlToImage) return res(); const s = document.createElement('script'); s.src = VENDOR + 'html-to-image.min.js'; s.onload = res; s.onerror = () => { lib = null; rej(new Error('截图组件加载失败')); }; document.head.append(s); }));
  const bgOf = el => { for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor; if (c && !/rgba?\(0, 0, 0, 0\)|transparent/.test(c)) return c; } return '#ffffff'; };
  // 截图：从选中元素往上找一块大小合适的区域（至少约 520×260，宽不超过一屏、高不超过 1100），截下来再框出选中元素
  // opt.rect（视口坐标）：用户在页面上拖出的框，框出的是这个区域而不是元素
  async function capture(el, opt = {}) {
    await loadLib();
    const er = opt.rect || el.getBoundingClientRect(); let box = el;
    const holds = r => r.left <= er.left + 1 && r.top <= er.top + 1 && r.right >= er.left + er.width - 1 && r.bottom >= er.top + er.height - 1;
    for (let n = el.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      const r = n.getBoundingClientRect(); if (r.width > Math.max(innerWidth, 1440) + 2 || r.height > 1100) break; box = n;
      if (holds(r) && r.width >= Math.max(520, er.width * 1.8) && r.height >= Math.max(260, er.height * 1.8)) break; }
    // 截图时把被截元素自己的外边距清零：通栏色带（负外边距）和居中容器（自动外边距）原样带进截图会让内容整体左右偏，框就对不上
    const cr = box.getBoundingClientRect(), scale = Math.max(.4, Math.min(1.5, 1400 / Math.max(1, cr.width)));
    const job = window.htmlToImage.toCanvas(box, { pixelRatio: scale, skipFonts: true, style: { margin: "0" }, cacheBust: false, backgroundColor: bgOf(box),
      filter: n => !(n && n.id === 'apf-host'), imagePlaceholder: 'data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==' });
    const cv = await Promise.race([job, new Promise((_, rej) => setTimeout(() => rej(new Error('截图超时')), opt.timeout || 9000))]);
    const k = cv.width / cr.width, x = (er.left - cr.left) * k, y = (er.top - cr.top) * k, w = er.width * k, h = er.height * k;
    // 区域太高时只保留选中元素上下各约 300 px
    const top = Math.max(0, Math.min(y - 300 * k, cv.height - 1100 * k)), hh = Math.min(cv.height - top, Math.max(1100 * k, h + 600 * k));
    const out = document.createElement('canvas'); out.width = cv.width; out.height = Math.round(hh);
    const g = out.getContext('2d'); g.drawImage(cv, 0, top, cv.width, hh, 0, 0, cv.width, hh);
    g.lineWidth = Math.max(3, 3 * k); g.strokeStyle = '#F59E0B'; g.setLineDash([]); g.strokeRect(x - 4 * k, y - top - 4 * k, w + 8 * k, h + 8 * k);
    g.fillStyle = 'rgba(245,158,11,.10)'; g.fillRect(x - 4 * k, y - top - 4 * k, w + 8 * k, h + 8 * k);
    if (opt.rx != null) { g.fillStyle = '#F59E0B'; g.beginPath(); g.arc(x + w * opt.rx, y - top + h * opt.ry, 7 * k, 0, 7); g.fill(); g.lineWidth = 2 * k; g.strokeStyle = '#fff'; g.stroke(); }
    return out.toDataURL('image/jpeg', .82);
  }
  function lightbox(src, caption) {
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(10,12,20,.82);display:grid;place-items:center;padding:24px;cursor:zoom-out';
    d.innerHTML = `<figure style="margin:0;max-width:96vw;max-height:92vh;display:grid;gap:8px;justify-items:center"><img src="${src}" style="max-width:96vw;max-height:86vh;border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.5);background:#fff"><figcaption style="color:#e5e7eb;font:13px system-ui">${caption ? String(caption).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]) + ' · ' : ''}点击任意处关闭</figcaption></figure>`;
    const close = () => { d.remove(); removeEventListener('keydown', esc, true); }, esc = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    d.querySelectorAll('[style]').forEach(el => { const v = el.getAttribute('style'); if (v && !el.style.length) el.style.cssText = v; });   // 严格 CSP 的页面
    d.onclick = close; addEventListener('keydown', esc, true); document.body.append(d);
  }
  // 在图片上圈画：画笔 / 方框，撤销、清除；手指和鼠标都能用（Pointer Events）。界面放在 Shadow DOM 里，不受网站样式影响
  function editImage(src) {
    return new Promise(done => {
      const host = document.createElement('div'); host.id = 'apf-edit'; host.style.cssText = 'position:fixed;inset:0;z-index:2147483647';
      const sr = host.attachShadow({ mode: 'open' });
      sr.innerHTML = `<style>
        * { box-sizing: border-box; font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
        .w { position: fixed; inset: 0; background: rgba(10,12,20,.88); display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 12px; }
        .bar { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; align-items: center; color: #e5e7eb; font-size: 13px; }
        button { font: inherit; font-size: 14px; border: 1px solid #4b5563; background: #1f2430; color: #fff; border-radius: 999px; padding: 7px 14px; cursor: pointer; min-height: 36px; }
        button[aria-pressed="true"] { background: #EF4444; border-color: #EF4444; }
        button.ok { background: #F59E0B; border-color: #F59E0B; color: #1f2430; font-weight: 600; }
        .sp { flex: 0 0 10px; }
        .st { flex: 1; min-height: 0; width: 100%; display: grid; place-items: center; }
        canvas { max-width: 100%; max-height: 100%; background: #fff; border-radius: 8px; touch-action: none; cursor: crosshair; box-shadow: 0 20px 60px rgba(0,0,0,.5); }
        .tip { color: #9ca3af; font-size: 12px; }
      </style><div class="w"><div class="bar"><button data-t="pen" aria-pressed="true">画笔</button><button data-t="rect" aria-pressed="false">方框</button><span class="sp"></span>
        <button data-a="undo">撤销</button><button data-a="clear">清除</button><span class="sp"></span><button data-a="cancel">取消</button><button data-a="ok" class="ok">完成</button></div>
        <div class="tip">用手指或鼠标圈出要改的地方</div><div class="st"><canvas></canvas></div></div>`;
      const cv = sr.querySelector('canvas'), g = cv.getContext('2d'), im = new Image(); let tool = 'pen', strokes = [], cur = null;
      const lw = () => Math.max(4, Math.round(cv.width / 220));
      function draw() { g.drawImage(im, 0, 0); g.strokeStyle = '#EF4444'; g.lineWidth = lw(); g.lineCap = g.lineJoin = 'round';
        for (const s of strokes.concat(cur ? [cur] : [])) { g.beginPath();
          if (s.t === 'rect') g.rect(Math.min(s.a[0], s.b[0]), Math.min(s.a[1], s.b[1]), Math.abs(s.b[0] - s.a[0]), Math.abs(s.b[1] - s.a[1]));
          else s.p.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y));
          g.stroke(); } }
      const at = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
      cv.addEventListener('pointerdown', e => { e.preventDefault(); cv.setPointerCapture(e.pointerId); const p = at(e); cur = tool === 'rect' ? { t: 'rect', a: p, b: p } : { t: 'pen', p: [p] }; draw(); });
      cv.addEventListener('pointermove', e => { if (!cur) return; const p = at(e); if (cur.t === 'rect') cur.b = p; else cur.p.push(p); draw(); });
      const end = () => { if (!cur) return; const big = cur.t === 'rect' ? Math.abs(cur.b[0] - cur.a[0]) + Math.abs(cur.b[1] - cur.a[1]) > 8 : cur.p.length > 1; if (big) strokes.push(cur); cur = null; draw(); };
      cv.addEventListener('pointerup', end); cv.addEventListener('pointercancel', end);
      const box = s => { const xs = s.t === 'rect' ? [s.a[0], s.b[0]] : s.p.map(p => p[0]), ys = s.t === 'rect' ? [s.a[1], s.b[1]] : s.p.map(p => p[1]);
        const x = Math.min(...xs), y = Math.min(...ys), r3 = v => Math.round(v * 1000) / 1000;
        return { t: s.t, x: r3(x / cv.width), y: r3(y / cv.height), w: r3((Math.max(...xs) - x) / cv.width), h: r3((Math.max(...ys) - y) / cv.height) }; };
      const close = out => { host.remove(); removeEventListener('keydown', key, true); done(out); };
      const key = e => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); strokes.pop(); draw(); } };
      sr.querySelector('.bar').onclick = e => { const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.t) { tool = b.dataset.t; sr.querySelectorAll('[data-t]').forEach(x => x.setAttribute('aria-pressed', x === b)); }
        if (b.dataset.a === 'undo') { strokes.pop(); draw(); } if (b.dataset.a === 'clear') { strokes = []; draw(); }
        if (b.dataset.a === 'cancel') close(null);
        if (b.dataset.a === 'ok') close(strokes.length ? { url: cv.toDataURL('image/jpeg', .85), marks: strokes.map(box) } : { url: src, marks: [] }); };
      im.onload = () => { cv.width = im.naturalWidth; cv.height = im.naturalHeight; draw(); addEventListener('keydown', key, true); document.body.append(host); };
      im.onerror = () => done(null); im.src = src;
    });
  }
  window.APFMedia = { fileURL, hydrate, readImage, capture, lightbox, editImage };
})();
