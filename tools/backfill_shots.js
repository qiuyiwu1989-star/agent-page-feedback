/* 给没有位置截图的批注补拍截图（例如接入本系统之前就有的批注，或截图失败的批注）。
   用法：
     npm i playwright   # 或使用已安装的 Playwright
     FB_API=https://example.com/api/fb/ FB_CODE=团队口令 node tools/backfill_shots.js
   可选：FB_IDS=3,5,8 只处理这些编号；FB_NOTE=0 不在批注下留说明。
   做法：按批注当时的屏幕尺寸打开页面 → 找到批注的元素（先按选择器，选择器失效时按引用文字，再不行按原坐标）
        → 滚动到位、等页面稳定 → 画橙色框 → 截图上传到 POST items/<id>/shot。 */
const { chromium } = require('playwright');
const API = (process.env.FB_API || '').replace(/\/?$/, '/'), CODE = process.env.FB_CODE || '';
if (!API || !CODE) { console.error('需要 FB_API 和 FB_CODE'); process.exit(1); }
const ONLY = (process.env.FB_IDS || '').split(',').filter(Boolean).map(Number);
const H = { 'Content-Type': 'application/json', 'X-FB-Code': CODE, 'X-FB-Name': 'Claude', 'X-FB-Cid': 'backfill' };

(async () => {
  const items = (await (await fetch(API + 'items?all=1', { headers: H })).json()).items;
  const b = await chromium.launch();
  for (const it of items.sort((a, c) => a.id - c.id)) {
    if (ONLY.length ? !ONLY.includes(it.id) : (it.files || []).some(f => f.kind === 'shot')) continue;
    const url = it.url; if (!url) { console.log('#' + it.id, '没有页面地址，跳过'); continue; }
    const ctx = await b.newContext({ viewport: { width: Math.max(1024, it.vw || 1440), height: Math.max(700, it.vh || 900) } });
    const p = await ctx.newPage(); await p.goto(url, { waitUntil: 'load' }); await p.waitForTimeout(2500);
    // 先滚动到位并等待：很多页面有滚动进入时的淡入动画、懒加载图片，立刻量位置会偏
    await p.evaluate(({ sel, quote }) => { let el = null; try { el = sel ? document.querySelector(sel) : null; } catch (e) {}
      if (!el && quote) { const key = quote.slice(0, 14), w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) if (n.nodeValue.replace(/\s+/g, ' ').includes(key)) { el = n.parentElement; break; } }
      if (el) el.scrollIntoView({ block: 'center' }); }, { sel: it.selector, quote: it.quote });
    await p.waitForTimeout(1800);
    const box = await p.evaluate(({ sel, quote, px, py, rx, ry }) => {
      const host = document.getElementById('apf-host'); if (host) host.style.display = 'none';
      const txt = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ');
      let el = null; try { el = sel ? document.querySelector(sel) : null; } catch (e) {}
      if (el && quote && txt(el).length && !txt(el).includes(quote.slice(0, 12))) el = null;   // 选择器指到了别的内容
      if (!el && quote) { const key = quote.slice(0, 14), w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) if (n.nodeValue.replace(/\s+/g, ' ').includes(key)) { el = n.parentElement; break; } }
      let r, how;
      if (el) { r = el.getBoundingClientRect(); how = 'element'; }
      else if (px || py) { scrollTo(0, Math.max(0, py - innerHeight / 2)); r = { left: px - scrollX - 60, top: py - scrollY - 30, width: 120, height: 60 }; how = 'coords'; }
      else return null;
      const o = document.createElement('div'); o.style.cssText = `position:fixed;z-index:2147483646;pointer-events:none;left:${r.left - 5}px;top:${r.top - 5}px;width:${r.width + 10}px;height:${r.height + 10}px;border:3px solid #F59E0B;background:rgba(245,158,11,.10);border-radius:6px`;
      document.body.append(o);
      if (how === 'element' && rx != null) { const d = document.createElement('div'); d.style.cssText = `position:fixed;z-index:2147483647;pointer-events:none;left:${r.left + r.width * rx - 7}px;top:${r.top + r.height * ry - 7}px;width:14px;height:14px;border-radius:50%;background:#F59E0B;border:2px solid #fff`; document.body.append(d); }
      return { x: r.left, y: r.top, w: r.width, h: r.height, how };
    }, { sel: it.selector, quote: it.quote, px: it.px, py: it.py, rx: it.rx, ry: it.ry });
    if (!box) { console.log('#' + it.id, '找不到位置，跳过'); await ctx.close(); continue; }
    await p.waitForTimeout(300);
    const vw = p.viewportSize(), padX = 220, padY = 160;
    let x = Math.min(Math.max(0, box.x - padX), vw.width - 320), y = Math.min(Math.max(0, box.y - padY), vw.height - 200);
    let w = Math.min(vw.width - x, Math.max(320, box.w + padX * 2)), h = Math.min(vw.height - y, Math.max(260, box.h + padY * 2));
    if (box.w + 10 >= vw.width || box.h + 10 >= vw.height) { x = 0; y = 0; w = vw.width; h = vw.height; }
    const buf = await p.screenshot({ type: 'jpeg', quality: 82, clip: { x, y, width: w, height: h } });
    const res = await fetch(API + 'items/' + it.id + '/shot', { method: 'POST', headers: H, body: JSON.stringify({ shot: 'data:image/jpeg;base64,' + buf.toString('base64') }) });
    if (process.env.FB_NOTE !== '0' && res.ok) await fetch(API + 'items/' + it.id + '/reply', { method: 'POST', headers: H, body: JSON.stringify({ body: '补拍了批注位置截图' + (it.status === 'done' ? '（这条已经改好，截图是修改后的样子）' : '') + (box.how === 'coords' ? '；原来的元素已不在页面上，按原坐标框出位置' : '') + '。' }) });
    console.log('#' + it.id, box.how, Math.round(buf.length / 1024) + 'KB', 'upload', res.status);
    await ctx.close();
  }
  await b.close();
})();
