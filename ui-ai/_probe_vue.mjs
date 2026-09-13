/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on('console', (msg) => {
  log(`[${msg.type()}]`, msg.text().slice(0, 500));
});
page.on('pageerror', (err) => {
  const brief = {
    name: err?.name,
    message: err?.message,
    stack: (err?.stack || '').slice(0, 600),
  };
  log('[pageerror]', JSON.stringify(brief, null, 0).slice(0, 900));
});
page.on('requestfailed', (req) => {
  log('[reqfailed]', req.url().slice(0, 120), String(req.failure()).slice(0, 150));
});

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

// 安装 Vue warnHandler/errorHandler 到 window
await page.evaluate(() => {
  window.__v = [];
  const app = document.querySelector('#app').__vue_app__;
  app.config.warnHandler = (msg, inst, trace) => {
    window.__v.push('[warn] ' + String(msg).slice(0, 500));
  };
  app.config.errorHandler = (err, inst, info) => {
    window.__v.push(`[error:${info}] ` + (err?.stack || err).slice(0, 800));
  };
});

await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4500);
log('--- 点击添加 ---');
await page.locator('button:has-text("添加")').first().click();
await page.waitForTimeout(2500);

const d = await page.evaluate(() => {
  const drawer = [...document.querySelectorAll('.ant-drawer')].find((x) => x.getClientRects().length > 0);
  const fc = drawer ? drawer.querySelector('.fs-form-item:has([path="function_code"])') : null;
  const item = drawer ? [...drawer.querySelectorAll('.ant-form-item')].find((it) => (it.innerText || '').includes('函数源码')) : null;
  const compContent = item ? item.querySelector('.fs-form-item-component, .fs-form-item-render, .fs-form-item-content') : null;
  return {
    vueMsgs: window.__v || [],
    cmCount: drawer ? drawer.querySelectorAll('.cm-editor').length : -1,
    compHtml: compContent ? compContent.outerHTML.replace(/\s+/g, ' ').slice(0, 600) : 'NONE',
  };
});
log('--- Vue warn/error ---');
(d.vueMsgs || []).forEach((m) => log('  ', m.slice(0, 800)));
log('cm-editor 数量:', d.cmCount);
log('component 容器 HTML:', d.compHtml);

await browser.close();
fs.writeFileSync(`${OUT}/report9.txt`, report.join('\n'), 'utf-8');
console.log('DONE');