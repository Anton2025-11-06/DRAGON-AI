/* eslint-disable no-console */
/**
 * 端到端实证：登录 → 菜单结构 → 三页面布局 → 工具新增按钮 → 模型体验路由
 * 运行：node site/_ui_verify.mjs
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'http://localhost:5999';
const OUT = 'D:/ai大模型/DRAGON-AI-master/site/_shots';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

// 捕获所有 /api/ 请求
const apiCalls = [];
page.on('request', (req) => {
  if (req.url().includes('/api/')) {
    apiCalls.push(`${req.method()} ${new URL(req.url()).pathname}`);
  }
});

const log = (...a) => console.log(...a);

// ---------- 1. 登录（先 UI 登录获取 token，失败则直接注入） ----------
log('== 1. 登录 ==');
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(3000);

let injected = false;
const inputCount = await page.locator('input').count();
log('input count:', inputCount);
if (inputCount >= 2) {
  try {
    await page.locator('input[name="username"]').fill('admin');
    await page.locator('input[name="password"]').fill('Admin@123');
    const btn = page.locator('button[type="submit"]').first();
    await btn.click({ timeout: 10000 });
    await page.waitForTimeout(9000);
    injected = true;
    log('UI 登录已提交');
  } catch (e) {
    log('UI 点击异常，转 token 注入:', String(e).split('\n')[0]);
  }
}
if (!injected) {
  // 通过后端登录接口拿 token 后写入 pinia persist（dev 模式明文 localStorage）
  const jwt = await fetchLoginToken();
  log('token 获取:', jwt ? jwt.slice(0, 30) + '...' : 'FAILED');
  await page.evaluate((tok) => {
    const key = 'pmf-web-antd-5.5.9-dev-core-access';
    localStorage.setItem(key, JSON.stringify({
      accessToken: tok,
      refreshToken: tok,
      accessCodes: [],
      isLockScreen: false,
      lockScreenPassword: null,
    }));
  }, jwt);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(8000);
}

async function fetchLoginToken() {
  return await page.evaluate(async () => {
    const resp = await fetch('/api/login/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password: 'Admin@123' }),
    });
    const data = await resp.json();
    return (data?.data || {}).token || null;
  });
}

// 登录后应出现侧边栏菜单
const menuTexts = await page.locator('.vben-sidebar, .ant-menu, [class*="sidebar"] span').allInnerTexts();
log('sidebar texts:', JSON.stringify(menuTexts.slice(0, 40)));
await page.screenshot({ path: `${OUT}/01-after-login.png`, fullPage: false });

// ---------- 2. 菜单结构断言（问题4/5） ----------
log('\n== 2. 菜单结构 ==');
const navText = (await page.locator('body').innerText()).includes('技能管理');
log('页面包含"技能管理":', navText);

// 展开智能体分组看子菜单
async function clickIfVisible(text) {
  const loc = page.locator(`text=${text}`).first();
  if (await loc.count()) {
    try {
      await loc.click({ timeout: 3000 });
      await page.waitForTimeout(1200);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

await clickIfVisible('智能体');
await page.waitForTimeout(800);
const agentSub = await page.locator('body').innerText();
for (const name of ['工作流编排', '技能管理', 'MCP连接管理', '工具管理']) {
  log(`智能体下含「${name}」:`, agentSub.includes(name));
}
log('页面出现「工具目录」:', agentSub.includes('工具目录'));

// 模型广场下应有「模型体验」而非「模型对话」
await clickIfVisible('模型广场');
await page.waitForTimeout(800);
const plazaSub = await page.locator('body').innerText();
log('模型广场含「模型体验」:', plazaSub.includes('模型体验'));
log('模型广场含「模型对话」:', plazaSub.includes('模型对话'));
// 收起菜单，避免遮挡
await clickIfVisible('模型广场');

// ---------- 3. 页面布局实证（问题3） ----------
log('\n== 3. 页面布局 ==');
async function checkLayout(path, name) {
  log(`--- ${name} (${path}) ---`);
  await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(5000);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });

  // 页面根容器几何信息
  const geo = await page.evaluate(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const roots = [
      ...document.querySelectorAll('.fs-page'),
      ...document.querySelectorAll('.vben-page'),
      ...document.querySelectorAll('[data-testid="skill-folder-card"]'),
    ];
    const boxes = roots.map((el) => {
      const r = el.getBoundingClientRect();
      return {
        cls: (el.className || '').toString().slice(0, 60),
        left: Math.round(r.left),
        top: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
      };
    });
    // 页面大容器（首个明显大块）
    const candidates = [...document.querySelectorAll('main, .vben-layout-content, .vben-page-wrapper')]
      .map((el) => {
        const r = el.getBoundingClientRect();
        return { cls: (el.className || '').toString().slice(0, 60), left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
      })
      .filter((b) => b.width > 100);
    return { vw, vh, boxes, candidates: candidates.slice(0, 6) };
  });
  log('viewport:', geo.vw, 'x', geo.vh);
  log('roots:', JSON.stringify(geo.boxes, null, 1));
  log('containers:', JSON.stringify(geo.candidates, null, 1));
  const abnormal = geo.boxes.filter((b) => b.width < geo.vw - 300 || b.left > 100 || b.top < 0 || b.height < 100);
  log('异常判定(内容宽度不足/偏移):', abnormal.length > 0 ? JSON.stringify(abnormal) : '无');
  return geo;
}

await checkLayout('/agent/tool', 'tool');
await checkLayout('/agent/mcp', 'mcp');
await checkLayout('/agent/skill', 'skill');
await checkLayout('/model-plaza/experience', 'experience');

// ---------- 4. 工具管理新增按钮（问题2） ----------
log('\n== 4. 工具管理新增按钮 ==');
await page.goto(`${BASE}/agent/tool`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4000);
const addBtn = page.getByRole('button', { name: /新\s*增/ }).first();
log('新增按钮可见:', await addBtn.isVisible().catch(() => false));
if (await addBtn.isVisible().catch(() => false)) {
  await addBtn.click();
  await page.waitForTimeout(1500);
  log('点击新增后页面/弹窗出现输入框:', (await page.locator('.a-drawer input, .ant-drawer input').count()) > 0);
}
log('工具页 API 请求:', JSON.stringify(apiCalls.filter((c) => c.includes('/workflow/tools')), null, 1));

await browser.close();
console.log('\nDONE');