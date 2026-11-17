// 截一张页面（或页面上某个元素）的图，给交付报告用：改前截一张，改完再截一张。
// 用法：node tools/snap.js <页面地址> <输出.jpg> [CSS 选择器] [--w 1440] [--h 900] [--mobile]
// 需要 Playwright（npm i playwright，或环境里已有）。会先滚动到位、等 1.5 秒，避免淡入动画和懒加载截空。
const { chromium } = require('playwright');
const args = process.argv.slice(2), flag = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args.splice(i, 2)[1] : d; };
const mobile = args.includes('--mobile'); if (mobile) args.splice(args.indexOf('--mobile'), 1);
const W = +flag('--w', mobile ? 390 : 1440), H = +flag('--h', mobile ? 844 : 900);
const [url, out, sel] = args;
if (!url || !out) { console.error('用法：node tools/snap.js <页面地址> <输出.jpg> [CSS 选择器] [--w 1440] [--h 900] [--mobile]'); process.exit(1); }
(async () => {
  const br = await chromium.launch(); const pg = await br.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  await pg.goto(url, { waitUntil: 'load', timeout: 60000 }); await pg.waitForTimeout(1200);
  if (sel) { const el = pg.locator(sel).first(); await el.scrollIntoViewIfNeeded(); await pg.waitForTimeout(1500); await el.screenshot({ path: out, type: 'jpeg', quality: 82 }); }
  else await pg.screenshot({ path: out, type: 'jpeg', quality: 82 });
  console.log(out); await br.close();
})().catch(e => { console.error(e.message); process.exit(1); });
