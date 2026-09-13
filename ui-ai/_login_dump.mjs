/* eslint-disable no-console */
import { chromium } from 'playwright';

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto('http://localhost:5999', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4000);
await page.screenshot({ path: 'D:/ai大模型/DRAGON-AI-master/site/_shots/login.png' });

const info = await page.evaluate(() => {
  const inputs = [...document.querySelectorAll('input')].map((el) => ({
    type: el.type,
    placeholder: el.placeholder,
    name: el.name,
    cls: (el.className || '').toString().slice(0, 50),
  }));
  const buttons = [...document.querySelectorAll('button')].map((el) => ({
    text: (el.innerText || '').trim().slice(0, 20),
    type: el.type,
    cls: (el.className || '').toString().slice(0, 60),
  }));
  return { inputs, buttons, bodyText: (document.body.innerText || '').slice(0, 120) };
});
console.log(JSON.stringify(info, null, 1));
await browser.close();
console.log('DONE');