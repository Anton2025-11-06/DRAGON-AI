/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

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

// A. 侧边栏菜单 DOM 结构 dump
const menuDom = await page.evaluate(() => {
  const sidebar = document.querySelector('.vben-sidebar') || document.querySelector('[class*="sidebar"]');
  const text = sidebar ? sidebar.innerText.replace(/\n+/g, ' | ') : 'NO SIDEBAR';
  const cls = sidebar ? (sidebar.className || '').toString().slice(0, 80) : '';
  return { cls, text: text.slice(0, 200) };
});
log('=== 侧边栏结构 ===');
log('class:', menuDom.cls);
log('text:', menuDom.text);

// 展开「智能体」：遍历可点击元素（aria-expanded 或 title 含智能体）
await page.evaluate(() => {
  const nodes = [...document.querySelectorAll('*')];
  const hit = nodes.find((el) => {
    const t = (el.textContent || '').trim();
    return t === '智能体' && el.children.length <= 2;
  });
  if (hit) hit.click();
});
await page.waitForTimeout(1200);
const agentView = await page.evaluate(() => {
  const sidebar = document.querySelector('.vben-sidebar') || document.querySelector('[class*="sidebar"]');
  return sidebar ? sidebar.innerText.replace(/\n+/g, ' | ') : '';
});
log('智能体展开后侧边栏:');
log(agentView.slice(0, 300));

// 展开「模型广场」
await page.evaluate(() => {
  const nodes = [...document.querySelectorAll('*')];
  const hit = nodes.find((el) => {
    const t = (el.textContent || '').trim();
    return t === '模型广场' && el.children.length <= 2;
  });
  if (hit) hit.click();
});
await page.waitForTimeout(1200);
const plazaView = await page.evaluate(() => {
  const sidebar = document.querySelector('.vben-sidebar') || document.querySelector('[class*="sidebar"]');
  return sidebar ? sidebar.innerText.replace(/\n+/g, ' | ') : '';
});
log('模型广场展开后侧边栏:');
log(plazaView.slice(0, 300));

// B. 工具页「添加」按钮 → drawer 表单
await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4000);
const btnInfo = await page.evaluate(() => {
  const btns = [...document.querySelectorAll('button')]
    .map((el) => ({ text: (el.innerText || '').trim(), visible: el.offsetParent !== null }))
    .filter((b) => b.visible && b.text);
  return btns.map((b) => b.text).join(', ');
});
log('\n=== /agent/tool 按钮 ===');
log(btnInfo.slice(0, 200));

const addBtn = page.locator('button:has-text("添加")').first();
log('「添加」按钮可见:', await addBtn.isVisible().catch(() => false));
if (await addBtn.isVisible().catch(() => false)) {
  await addBtn.click();
  await page.waitForTimeout(1500);
  const formInfo = await page.evaluate(() => ({
    drawer: document.querySelectorAll('.ant-drawer-wrap, .ant-drawer').length,
    inputs: document.querySelectorAll('.ant-drawer input, .ant-drawer textarea').length,
    labels: [...document.querySelectorAll('.ant-drawer label, .ant-drawer .ant-form-item-label')].map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 10),
  }));
  log('drawer 出现:', formInfo.drawer > 0, '| 输入控件:', formInfo.inputs, '| label:', JSON.stringify(formInfo.labels));
  await page.screenshot({ path: `${OUT}/tool-add-drawer.png` });
}

await browser.close();
fs.writeFileSync(`${OUT}/report2.txt`, report.join('\n'), 'utf-8');
console.log('DONE');