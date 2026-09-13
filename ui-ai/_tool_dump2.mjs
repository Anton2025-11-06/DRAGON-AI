/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

// 登录（token 注入）
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

// 1. 导航到工具管理
await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(5000);
let info = await page.evaluate(() => {
  const btns = [...document.querySelectorAll('button')].map((el) => ({
    text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 24),
    visible: el.offsetParent !== null,
  })).filter((b) => b.visible);
  return {
    btns,
    tableRows: document.querySelectorAll('.ant-table-row').length,
    hasFsPage: document.querySelectorAll('.fs-page').length,
    body: (document.body.innerText || '').replace(/\n+/g, ' | ').slice(0, 400),
  };
});
log('=== /agent/tool ===');
log('按钮:', JSON.stringify(info.btns));
log('表格行:', info.tableRows, '| fs-page:', info.hasFsPage);
log('body:', info.body.slice(0, 300));
await page.screenshot({ path: `${OUT}/tool-real.png` });
// 点击「上传」类新增按钮（先找 ant-btn-primary）
const primaryBtn = page.locator('button.ant-btn-primary').first();
const primaryVisible = await primaryBtn.isVisible().catch(() => false);
log('primary 按钮可见:', primaryVisible, '文本:', await primaryBtn.innerText().catch(() => ''));
if (primaryVisible) {
  await primaryBtn.click();
  await page.waitForTimeout(1500);
  const drawerInputs = await page.evaluate(() => ({
    drawer: document.querySelectorAll('.ant-drawer').length,
    inputs: document.querySelectorAll('.ant-drawer input, .ant-drawer textarea').length,
  }));
  log('点击后 drawer 出现:', drawerInputs);
}

// 2. 智能体子菜单
await page.goto('http://localhost:5999/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(3000);
await page.locator('.ant-menu-submenu-title:has-text("智能体"), .ant-menu-item:has-text("智能体")').first().click({ timeout: 5000 }).catch(() => {});
await page.waitForTimeout(1200);
const agentText = await page.evaluate(() => document.body.innerText);
log('\n=== 智能体菜单 ===');
for (const name of ['工作流编排', '技能管理', 'MCP连接管理', '工具管理', '工具目录']) {
  log(`含「${name}」:`, agentText.includes(name));
}
// 模型广场
await page.locator('.ant-menu-submenu-title:has-text("模型广场"), .ant-menu-item:has-text("模型广场")').first().click({ timeout: 5000 }).catch(() => {});
await page.waitForTimeout(1200);
const plazaText = await page.evaluate(() => document.body.innerText);
log('\n=== 模型广场菜单 ===');
for (const name of ['模型体验', '模型对话', '模型列表', '申请审批']) {
  log(`含「${name}」:`, plazaText.includes(name));
}

// 3. skill 页面布局（vben Page 容器）
await page.goto('http://localhost:5999/agent/skill', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(5000);
const skillGeo = await page.evaluate(() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { cls: (el.className || '').toString().slice(0, 50), left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
  };
  return { page: pick('.vben-page, .skill-page-content, .skill-list-card'), cards: document.querySelectorAll('.skill-folder-card').length, body: (document.body.innerText || '').replace(/\n+/g, ' | ').slice(0, 200) };
});
log('\n=== /agent/skill ===');
log(JSON.stringify(skillGeo));
await page.screenshot({ path: `${OUT}/skill-real.png` });

await browser.close();
fs.writeFileSync(`${OUT}/report.txt`, report.join('\n'), 'utf-8');
console.log('DONE');