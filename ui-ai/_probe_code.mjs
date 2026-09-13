/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on('pageerror', (err) => {
  log('[pageerror]', String(err?.stack || err).slice(0, 900));
});
page.on('console', (msg) => {
  if (msg.type() === 'error') log('[console.error]', msg.text().slice(0, 400));
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

await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4500);
await page.locator('button:has-text("添加")').first().click();
await page.waitForTimeout(2500);

const dump = await page.evaluate(() => {
  const drawer = [...document.querySelectorAll('.ant-drawer')].find((d) => d.getClientRects().length > 0);
  if (!drawer) return { ok: false };
  // 找「函数源码」label 对应 form-item
  const items = [...drawer.querySelectorAll('.ant-form-item')];
  const target = items.find((it) => (it.innerText || '').includes('函数源码'));
  const comp = target ? target.querySelector('.fs-form-item-component') : null;
  return {
    ok: true,
    cmEditor: drawer.querySelectorAll('.cm-editor').length,
    cmContent: drawer.querySelectorAll('.cm-content').length,
    cmWrap: drawer.querySelectorAll('.code-editor').length,
    textarea: drawer.querySelectorAll('.ant-drawer textarea, .ant-drawer textarea').length,
    targetHtml: target ? target.outerHTML.replace(/\s+/g, ' ').slice(0, 900) : 'NOT FOUND',
    compInner: comp ? comp.innerHTML.replace(/\s+/g, ' ').slice(0, 1600) : 'NO COMPONENT',
    drawerText: (drawer.innerText || '').slice(0, 300),
  };
});
log('drawer 存在:', dump.ok);
log('cm-editor:', dump.cmEditor, 'cm-content:', dump.cmContent, 'code-editor:', dump.cmWrap, 'textarea:', dump.textarea);
log('函数源码 form-item HTML:', dump.targetHtml);
log('component 容器 innerHTML:', dump.compInner);
log('drawer 文本:', JSON.stringify(dump.drawerText));

await page.screenshot({ path: `${OUT}/probe-code.png` });
await browser.close();
fs.writeFileSync(`${OUT}/report8.txt`, report.join('\n'), 'utf-8');
console.log('DONE');