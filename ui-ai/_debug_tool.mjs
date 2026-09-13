/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

// 捕获完整堆栈
page.on('pageerror', (err) => {
  log('\n[pageerror]', String(err.stack || err).slice(0, 1500));
});
page.on('console', (msg) => {
  if (msg.type() === 'error' || msg.type() === 'warning') {
    log(`[console.${msg.type()}]`, msg.text().slice(0, 400));
  }
});
page.on('requestfailed', (req) => log('[reqfailed]', req.url().slice(0, 120), String(req.failure()).slice(0, 120)));

await page.goto('http://localhost:5999', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(3000);
const jwt = await page.evaluate(async () => {
  const resp = await fetch('/api/login/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'Admin@123' }),
  });
  const data = await resp.json();
  return (data?.data || {}).token;
});
await page.evaluate((tok) => {
  localStorage.setItem('pmf-web-antd-5.5.9-dev-core-access', JSON.stringify({
    accessToken: tok, refreshToken: tok, accessCodes: [], isLockScreen: false, lockScreenPassword: null,
  }));
}, jwt);
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(8000);

// 安装 Vue errorHandler / unhandledrejection 捕获
await page.evaluate(() => {
  window.__errs = [];
  window.addEventListener('error', (e) => window.__errs.push('[error] ' + (e.error?.stack || e.message)));
  window.addEventListener('unhandledrejection', (e) => {
    window.__errs.push('[rejection] ' + (e.reason?.stack || e.reason));
  });
  const app = document.querySelector('#app').__vue_app__;
  if (app && !app.config.errorHandler) {
    app.config.errorHandler = (err, instance, info) => {
      window.__errs.push(`[vue:${info}] ` + (err?.stack || err));
    };
  }
});

await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4500);

log('=== 点击添加 ===');
await page.locator('button:has-text("添加")').first().click();
for (const t of [300, 800, 1500]) {
  await page.waitForTimeout(t);
  const s = await page.evaluate(() => ({
    mask: document.querySelectorAll('.ant-drawer-mask').length,
    drawer: document.querySelectorAll('.ant-drawer').length,
  }));
  log(`+${t}ms mask=${s.mask} drawer=${s.drawer}`);
}

const dump = await page.evaluate(() => ({
  errs: window.__errs || [],
  crudState: (() => {
    const app = document.querySelector('#app').__vue_app__;
    return 'app-ok';
  })(),
}));
log('\n=== 捕获的错误 ===');
(dump.errs || []).forEach((e) => log(String(e).slice(0, 1200)));

// 尝试寻找 crudBinding 对象：从 DOM 组件实例
const bindingInfo = await page.evaluate(() => {
  const fsCrud = document.querySelector('.fs-crud');
  if (!fsCrud) return { found: false };
  const inst = fsCrud.__vueParentComponent || fsCrud.__vueParentInstance;
  const walk = (obj, depth) => {
    if (!obj || depth > 6) return null;
    for (const k of ['crudBinding', 'crudOptions', 'crudExpose']) {
      for (let i = 0; i < (obj.$ || []).length; i++) {
        const inst2 = (obj.$ || [])[i];
        if (inst2 && inst2.setupState && inst2.exposed) {
          try {
            if (inst2.exposed.crudBinding) return JSON.stringify({ k, keys: Object.keys(inst2.exposed) });
          } catch { /* ignore */ }
        }
      }
    }
    return null;
  };
  return { found: true, html: (fsCrud.outerHTML || '').slice(0, 120) };
});
log('fs-crud 元素:', JSON.stringify(bindingInfo));

await page.screenshot({ path: `${OUT}/tool-debug.png` });
await browser.close();
fs.writeFileSync(`${OUT}/report5.txt`, report.join('\n'), 'utf-8');
console.log('DONE');