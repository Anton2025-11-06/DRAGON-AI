/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const pageErrors = [];
page.on('pageerror', (err) => pageErrors.push(String(err).slice(0, 200)));

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
  pageErrors.length = 0;
  await page.goto(`http://localhost:5999${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4500);
  const addBtn = page.locator('button:has-text("添加")').first();
  const visible = await addBtn.isVisible().catch(() => false);
  log('「添加」可见:', visible);
  if (visible) {
    await addBtn.click();
    await page.waitForTimeout(2500);
  }
  const s = await page.evaluate(() => {
    const all = [...document.querySelectorAll('.ant-drawer')];
    const visibleDrawers = all.filter((d) => d.getClientRects().length > 0);
    const wrap = visibleDrawers[0] || null;
    return {
      drawerTotal: all.length,
      drawerVisible: visibleDrawers.length,
      mask: document.querySelectorAll('.ant-drawer-mask').length,
      labels: wrap ? [...wrap.querySelectorAll('label')].map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 10) : [],
      inputs: wrap ? wrap.querySelectorAll('input, textarea, .ant-select').length : 0,
      rect: wrap ? (() => { const r = wrap.getBoundingClientRect(); return { left: Math.round(r.left), top: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }; })() : null,
    };
  });
  log('drawerTotal=', s.drawerTotal, 'drawerVisible=', s.drawerVisible, 'mask=', s.mask);
  log('labels=', JSON.stringify(s.labels), 'inputs=', s.inputs, 'rect=', JSON.stringify(s.rect));
  log('pageerror=', JSON.stringify(pageErrors.slice(0, 3)));
  await page.screenshot({ path: `${OUT}/${name}-add3.png` });
}

await testAdd('/agent/tool', 'tool');
await testAdd('/agent/mcp', 'mcp');
await testAdd('/agent/skill', 'skill');

await browser.close();
fs.writeFileSync(`${OUT}/report6.txt`, report.join('\n'), 'utf-8');
console.log('DONE');