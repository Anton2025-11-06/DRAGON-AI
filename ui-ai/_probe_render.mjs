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

await page.goto('http://localhost:5999/agent/tool', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(4500);
await page.locator('button:has-text("添加")').first().click();
await page.waitForTimeout(2500);

// 1. 已加载的 deps chunk 列表
const chunks = await page.evaluate(() =>
  performance.getEntriesByType('resource')
    .map((r) => r.name)
    .filter((n) => n.includes('/deps/'))
    .map((n) => n.split('/').pop())
    .filter((n, i, arr) => arr.indexOf(n) === i)
);
log('已加载 deps:', JSON.stringify(chunks));

// 2. 从 DOM 找 fs-component-render 实例并取 render 源码
const src = await page.evaluate(() => {
  const input = document.querySelector('.fs-form-item-component input');
  if (!input) return { found: false };
  let inst = input.__vueParentComponent;
  const chain = [];
  while (inst) {
    const t = inst.type;
    const name = t?.name || t?.__name || t?.displayName || '';
    chain.push(name);
    if (name === 'FsComponentRender' || (String(t?.render || '').includes('isAsyncComponent'))) {
      const renderSrc = String(t.render || t.setup || '').slice(0, 3000);
      return { found: true, name, chain, renderSrc };
    }
    inst = inst.parent;
  }
  return { found: false, chain };
});
log('组件链:', JSON.stringify(src.chain || []));
log('fs-component-render 源码:', src.renderSrc || '未找到');

await browser.close();
fs.writeFileSync(`${OUT}/report10.txt`, report.join('\n'), 'utf-8');
console.log('DONE');