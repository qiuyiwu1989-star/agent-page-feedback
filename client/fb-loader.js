/* agent-page-feedback 加载器：在项目的每个页面加一行
     <script src="/api/fb/client/fb-loader.js" defer></script>
   普通访客什么都不加载；只有登录过批注（本机存了口令）或打开带 ?fb=1 / ?fbid=编号 的链接时，才加载完整的批注组件。
   可选属性：
     data-api           接口地址，默认为本文件所在目录的上一级（即 /api/fb/）
     data-base          网站根路径，用来生成「页面标识」，默认 /（例如站点在 /docs/ 下就写 /docs/）
     data-summary       汇总页地址，默认与本文件同目录的 feedback.html
     data-title-suffix  页面标题里统一的后缀，批注里记录标题时去掉，例如 " · 我的网站" */
(function () {
  var me = document.currentScript; if (!me || window.APF_CONFIG) return;
  var at = function (k, d) { return me.getAttribute(k) || d; };
  window.APF_CONFIG = { api: at('data-api', new URL('../', me.src).href), base: at('data-base', '/'), summary: at('data-summary', new URL('feedback.html', me.src).href), titleSuffix: at('data-title-suffix', '') };
  var has = false; try { has = !!localStorage.getItem('apf:code'); } catch (e) {}
  if (!has && !/[?&](fb|fbid)=/.test(location.search)) return;
  var s = document.createElement('script'); s.src = new URL('feedback.js', me.src).href; s.defer = true; document.head.appendChild(s);
})();
