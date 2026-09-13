/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on('pageerror', (err) => log('[pageerror]', String(err).slice(0, 200)));

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

const paths = ['/system/role', '/system/auth/role', '/system/user', '/system/menu'];
for (const p of paths) {
  log(`\n=== ${p} ===`);
  await page.goto(`http://localhost:5999${p}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3500);
  const st = await page.evaluate(() => {
    const fsPage = document.querySelector('.fs-page');
    const backBtn = [...document.querySelectorAll('button')].some((b) => (b.innerText || '').trim() === '返回首页');
    const table = document.querySelector('.ant-table');
    return {
      fsPage: !!fsPage,
      back404: backBtn,
      table: !!table,
      title: (document.title || ''),
      text: (document.body.innerText || '').slice(0, 120).replace(/\n+/g, ' | '),
      url: location.pathname,
    };
  });
  log(JSON.stringify(st));
  await page.screenshot({ path: `${OUT}/role-${p.replace(/\//g, '_')}.png` });
}

await browser.close();
fs.writeFileSync(`${OUT}/report11.txt`, report.join('\n'), 'utf-8');
console.log('DONE');