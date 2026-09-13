/* eslint-disable no-console */
import { chromium } from 'playwright';

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto('http://localhost:5999', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(5000);

const before = await page.evaluate(() => ({
  submitCount: document.querySelectorAll('button[type="submit"]').length,
  visibleSubmit: [...document.querySelectorAll('button[type="submit"]')].filter((b) => b.offsetParent !== null).length,
  text: document.body.innerText.slice(0, 80),
}));
console.log('BEFORE fill:', JSON.stringify(before));

await page.locator('input[name="username"]').fill('admin');
await page.locator('input[name="password"]').fill('Admin@123');
await page.waitForTimeout(1500);

const after = await page.evaluate(() => ({
  submitCount: document.querySelectorAll('button[type="submit"]').length,
  visibleSubmit: [...document.querySelectorAll('button[type="submit"]')].filter((b) => b.offsetParent !== null).length,
  text: document.body.innerText.slice(0, 80),
}));
console.log('AFTER fill:', JSON.stringify(after));
await page.screenshot({ path: 'D:/ai大模型/DRAGON-AI-master/site/_shots/login-filled.png' });
await browser.close();
console.log('DONE');