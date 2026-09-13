/* eslint-disable no-console */
import { chromium } from 'playwright';
import fs from 'node:fs';

const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
const report = [];
const log = (...a) => report.push(a.join(' '));

const TEST_NAME = `e2e_tool_${Date.now()}`;

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const apiCalls = [];
page.on('response', (resp) => {
  const u = resp.url();
  if (u.includes('/api/workflow/tools/')) {
    apiCalls.push(`${resp.request().method()} ${u.replace('http://localhost:5999', '')} -> ${resp.status()}`);
  }
});
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

// 进入工具页
await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4500);

// 1. 打开添加表单
log('=== 添加表单 ===');
await page.locator('button:has-text("添加")').first().click();
await page.waitForTimeout(2000);

// 2. 填写表单
const nameInput = page.locator('.ant-drawer input[placeholder="请输入工具名称"]').first();
const descInput = page.locator('.ant-drawer textarea[placeholder*="工具用途说明"]').first();
if (!(await nameInput.count())) {
  log('!! 未找到名称输入框');
} else {
  await nameInput.fill(TEST_NAME);
  if (await descInput.count()) {
    await descInput.fill('端到端验证临时数据');
  }
  log('表单填写: filled');
}

// 3. 用键盘输入函数源码（codemirror contenteditable）
const cm = page.locator('.ant-drawer .cm-content').first();
if (await cm.count()) {
  await cm.click();
  await page.keyboard.type('def run(a, b):\n    return a + b');
  log('函数源码已输入');
} else {
  log('!! 未找到 cm-content');
}

// 4. 提交
await page.screenshot({ path: `${OUT}/e2e-tool-filled.png` });
log('=== 提交表单 ===');
const okBtn = page.locator('.ant-drawer button').filter({ hasText: /确.*定/ }).first();
log('确定按钮可见:', await okBtn.isVisible().catch(() => false));
await okBtn.click();
await page.waitForTimeout(1500);
// 等待 drawer 完全关闭（antd 关闭动画后移除 DOM）
await page.waitForSelector('.ant-drawer', { state: 'detached', timeout: 15000 }).catch(() => log('!! drawer 未关闭'));
await page.waitForTimeout(2000);

log('工具接口调用:');
apiCalls.forEach((c) => log('  ', c));
log('最后一条消息:', JSON.stringify((await page.evaluate(() => (document.body.innerText || '').slice(-100)))));

// 5. 搜索该工具确认出现在列表
log('=== 搜索新工具 ===');
const searchInput = page.locator('input[placeholder*="工具名称"]').first();
if (await searchInput.count()) {
  await searchInput.fill(TEST_NAME);
  // 直接派发 click 事件：搜索区 label 与按钮重叠会拦截命中测试（不影响事件处理）
  await page.locator('button:has-text("查询")').first().dispatchEvent('click');
  await page.waitForTimeout(2500);
}
const rows = await page.evaluate((name) => {
  const tds = [...document.querySelectorAll('.ant-table-tbody td')];
  const hit = tds.find((td) => (td.innerText || '').includes(name));
  const row = hit?.closest('tr');
  return {
    found: !!hit,
    rowText: row ? (row.innerText || '').replace(/\n+/g, ' | ').slice(0, 180) : '',
    rowCount: document.querySelectorAll('.ant-table-tbody tr').length,
  };
}, TEST_NAME);
log('搜索结果:', JSON.stringify(rows));
await page.screenshot({ path: `${OUT}/e2e-tool-list.png` });

// 6. 删除该行（若找到）
if (rows.found) {
  log('=== 删除工具 ===');
  const delBtn = page.locator(`tr:has-text("${TEST_NAME}") button:has-text("删除")`).first();
  log('删除按钮可见:', await delBtn.isVisible().catch(() => false));
  await delBtn.click();
  await page.waitForTimeout(800);
  const confirmBtn = page.locator('.ant-popconfirm button:has-text("确认删除")').first();
  log('确认删除可见:', await confirmBtn.isVisible().catch(() => false));
  if (await confirmBtn.isVisible().catch(() => false)) {
    await confirmBtn.click();
    await page.waitForTimeout(2500);
  }
  const afterDel = await page.evaluate((name) => ({
    found: [...document.querySelectorAll('.ant-table-tbody td')].some((td) => (td.innerText || '').includes(name)),
  }), TEST_NAME);
  log('删除后仍存在:', afterDel.found);
  log('接口调用（含删除）:');
  apiCalls.forEach((c) => log('  ', c));
}

await browser.close();
fs.writeFileSync(`${OUT}/report7.txt`, report.join('\n'), 'utf-8');
console.log('DONE');