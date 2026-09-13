/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const consoleErrors = [];
const pageErrors = [];
page.on('console', (msg) => {
  if (msg.type() === 'error' || msg.type() === 'warning') {
    consoleErrors.push(`[${msg.type()}] ${msg.text().slice(0, 200)}`);
  }
});
page.on('pageerror', (err) => pageErrors.push(String(err).slice(0, 300)));

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

async function dumpButtons(path, name) {
  log(`\n=== ${name} (${path}) 按钮清单 ===`);
  consoleErrors.length = 0;
  pageErrors.length = 0;
  await page.goto(`http://localhost:5999${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4500);
  const buttons = await page.evaluate(() => {
    return [...document.querySelectorAll('button')]
      .filter((b) => b.offsetParent !== null)
      .map((b) => ({
        text: (b.innerText || '').trim().slice(0, 20),
        disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
        cls: (b.className || '').toString().slice(0, 90),
        html: b.outerHTML.replace(/\s+/g, ' ').slice(0, 240),
      }))
      .filter((b) => b.text || b.cls.includes('actionbar') || b.cls.includes('fs-'));
  });
  log('按钮总数:', buttons.length);
  buttons.forEach((b, i) => {
    log(`[${i}] text="${b.text}" disabled=${b.disabled} cls=${b.cls}`);
  });
  return buttons;
}

async function clickAdd(path, name) {
  log(`\n=== ${name} 点击添加测试 ===`);
  const beforeHtml = await page.evaluate(() => document.body.innerHTML.length);
  const addBtn = page.locator('button:has-text("添加")').first();
  log('count 匹配:', await page.locator('button:has-text("添加")').count());
  const btn = await addBtn.evaluate((el) => ({
    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
    tag: el.tagName,
    cls: (el.className || '').toString(),
    parentCls: (el.parentElement?.className || '').toString().slice(0, 120),
    grandCls: (el.parentElement?.parentElement?.className || '').toString().slice(0, 120),
  })).catch((e) => ({ err: String(e) }));
  log('目标按钮:', JSON.stringify(btn));
  let clickErr = null;
  try {
    await addBtn.click({ timeout: 3000 });
    await page.waitForTimeout(2500);
  } catch (e) {
    clickErr = String(e).slice(0, 200);
  }
  log('click 异常:', clickErr || 'none');
  const afterHtml = await page.evaluate(() => document.body.innerHTML.length);
  const states = await page.evaluate(() => {
    const q = (sel) => [...document.querySelectorAll(sel)].filter((e) => e.offsetParent !== null).length;
    return {
      drawer: q('.ant-drawer'),
      modal: q('.ant-modal'),
      mask: document.querySelectorAll('.ant-drawer-mask').length,
      wrap: document.querySelectorAll('.ant-drawer-wrap').length,
      bodyLen: document.body.innerHTML.length,
      bodyTail: (document.body.innerText || '').slice(-120),
      fsContainer: document.querySelectorAll('.fs-drawer, .fs-modal').length,
    };
  });
  log('body innerHTML 变化:', beforeHtml, '->', afterHtml, 'diff=', afterHtml - beforeHtml);
  log('drawer=', states.drawer, 'modal=', states.modal, 'mask=', states.mask, 'wrap=', states.wrap,
      'fs-drawer/modal=', states.fsContainer);
  log('bodyTail:', JSON.stringify(states.bodyTail));
  log('console:', JSON.stringify(consoleErrors.slice(0, 8)));
  log('pageerror:', JSON.stringify(pageErrors.slice(0, 5)));
  await page.screenshot({ path: `${OUT}/${name}-add2.png` });
}

// 1. tool 页
await dumpButtons('/agent/tool', 'tool');
await clickAdd('/agent/tool', 'tool');

// 2. mcp 页
await dumpButtons('/agent/mcp', 'mcp');
await clickAdd('/agent/mcp', 'mcp');

// 3. role 页（对照组）
await dumpButtons('/system/auth/role', 'role');
const roleBtns = await page.evaluate(() => {
  return [...document.querySelectorAll('button')]
    .filter((b) => b.offsetParent !== null)
    .map((b) => (b.innerText || '').trim())
    .filter(Boolean);
});
log('\nrole 页所有按钮文案:', JSON.stringify(roleBtns.slice(0, 12)));
if (await page.locator('button:has-text("新增")').count()) {
  await page.locator('button:has-text("新增")').first().click();
  await page.waitForTimeout(2000);
  const s = await page.evaluate(() => ({
    drawer: [...document.querySelectorAll('.ant-drawer')].filter((e) => e.offsetParent !== null).length,
    modal: [...document.querySelectorAll('.ant-modal')].filter((e) => e.offsetParent !== null).length,
  }));
  log('role 点击「新增」后: drawer=', s.drawer, 'modal=', s.modal);
  await page.screenshot({ path: `${OUT}/role-add2.png` });
} else {
  log('role 页无「新增」按钮');
}

await browser.close();
fs.writeFileSync(`${OUT}/report4.txt`, report.join('\n'), 'utf-8');
console.log('DONE');