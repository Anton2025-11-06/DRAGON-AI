/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const consoleErrors = [];
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 150));
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

async function testAdd(path, name) {
  log(`\n=== ${name} (${path}) ===`);
  consoleErrors.length = 0;
  await page.goto(`http://localhost:5999${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4500);

  const addBtn = page.locator('button:has-text("添加")').first();
  log('「添加」可见:', await addBtn.isVisible().catch(() => false));
  const before = await page.evaluate(() => ({
    drawer: document.querySelectorAll('.ant-drawer-wrap').length,
    modal: document.querySelectorAll('.ant-modal').length,
    body: (document.body.innerText || '').slice(-150),
  }));
  if (await addBtn.isVisible().catch(() => false)) {
    await addBtn.click();
    await page.waitForTimeout(2000);
  }
  const after = await page.evaluate(() => {
    const drawerEl = document.querySelector('.ant-drawer-wrap');
    const modalEl = [...document.querySelectorAll('.ant-modal')].find((m) => m.offsetParent !== null);
    const wrap = drawerEl || modalEl;
    const r = wrap ? wrap.getBoundingClientRect() : null;
    return {
      drawerCount: document.querySelectorAll('.ant-drawer-wrap').length,
      visibleDrawer: [...document.querySelectorAll('.ant-drawer-wrap')].filter((d) => d.offsetParent !== null).length,
      modalCount: document.querySelectorAll('.ant-modal').length,
      visibleModal: [...document.querySelectorAll('.ant-modal')].filter((m) => m.offsetParent !== null).length,
      labels: wrap ? [...wrap.querySelectorAll('label')].map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 8) : [],
      formInputs: wrap ? wrap.querySelectorAll('input, textarea').length : 0,
      rect: r ? { left: Math.round(r.left), top: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } : null,
      bodyTail: (document.body.innerText || '').slice(-120),
    };
  });
  log('before: drawer=', before.drawer, 'modal=', before.modal);
  log('after: visibleDrawer=', after.visibleDrawer, 'visibleModal=', after.visibleModal,
      'labels=', JSON.stringify(after.labels), 'inputs=', after.formInputs, 'rect=', JSON.stringify(after.rect));
  log('console errors:', JSON.stringify(consoleErrors.slice(0, 5)));
  await page.screenshot({ path: `${OUT}/${name}-add.png` });
}

await testAdd('/agent/tool', 'tool');
await testAdd('/system/auth/role', 'role');
await testAdd('/agent/mcp', 'mcp');

await browser.close();
fs.writeFileSync(`${OUT}/report3.txt`, report.join('\n'), 'utf-8');
console.log('DONE');