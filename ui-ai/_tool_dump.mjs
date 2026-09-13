/* eslint-disable no-console */
import { chromium } from 'playwright';

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

async function dumpButtons(name) {
  const info = await page.evaluate(() => {
    const btns = [...document.querySelectorAll('button')].map((el) => ({
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 24),
      visible: el.offsetParent !== null,
      cls: (el.className || '').toString().slice(0, 40),
    })).filter((b) => b.visible);
    const tableRows = document.querySelectorAll('.ant-table-row').length;
    return { btns, tableRows, body: (document.body.innerText || '').replace(/\n+/g, ' | ').slice(0, 300) };
  });
  console.log(`\n=== ${name} ===`);
  console.log('buttons:', JSON.stringify(info.btns, null, 1));
  console.log('tableRows:', info.tableRows);
  console.log('body:', info.body);
}

await dumpButtons('tool 页原始状态');

// 展开侧边栏「智能体」看子菜单
await page.locator('text=智能体').first().click({ timeout: 5000 }).catch(() => {});
await page.waitForTimeout(1000);
const agentText = await page.evaluate(() => document.body.innerText);
for (const name of ['工作流编排', '技能管理', 'MCP连接管理', '工具管理', '工具目录']) {
  console.log(`智能体下含「${name}」:`, agentText.includes(name));
}
// 模型广场
await page.locator('text=模型广场').first().click({ timeout: 5000 }).catch(() => {});
await page.waitForTimeout(1000);
const plazaText = await page.evaluate(() => document.body.innerText);
for (const name of ['模型体验', '模型对话', '模型列表', '申请审批']) {
  console.log(`模型广场含「${name}」:`, plazaText.includes(name));
}

await browser.close();
console.log('\nDONE');