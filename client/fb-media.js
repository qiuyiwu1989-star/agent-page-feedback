/* agent-page-feedback · 图片能力（feedback.js 与 feedback.html 共用）：
   - capture(el)：截取批注位置（选中元素及周围一块区域），用橙色框标出选中的元素；用 vendor/html-to-image（MIT）
   - readImage(file)：读入用户添加 / 粘贴的图片，过大的缩到 1800 px 以内转 JPEG
   - hydrate(root)：把 <img data-fid> 换成真实图片（附件读取要带口令，所以先 fetch 再生成 blob 地址）
   - lightbox(src)：点击看大图 */
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
  async function capture(el, opt = {}) {
    await loadLib();
    const er = el.getBoundingClientRect(); let box = el;
    for (let n = el.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      const r = n.getBoundingClientRect(); if (r.width > Math.max(innerWidth, 1440) + 2 || r.height > 1100) break; box = n;
      if (r.width >= Math.max(520, er.width * 1.8) && r.height >= Math.max(260, er.height * 1.8)) break; }
    const cr = box.getBoundingClientRect(), scale = Math.max(.4, Math.min(1.5, 1400 / Math.max(1, cr.width)));
    const job = window.htmlToImage.toCanvas(box, { pixelRatio: scale, skipFonts: true, cacheBust: false, backgroundColor: bgOf(box),
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
    d.onclick = close; addEventListener('keydown', esc, true); document.body.append(d);
  }
  window.APFMedia = { fileURL, hydrate, readImage, capture, lightbox };
})();
