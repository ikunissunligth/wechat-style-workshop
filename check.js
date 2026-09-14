const fs = require('fs');
const path = 'F:/codex/微信公众号编辑工具/index.html';
const html = fs.readFileSync(path, 'utf8');

// 1. 提取 script 并做语法检查
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('FAIL: no script block'); process.exit(1); }
try {
  new (require('vm').Script)(m[1], { filename: 'app.js' });
  console.log('OK: script syntax valid, length =', m[1].length);
} catch (e) {
  console.error('SYNTAX ERROR:', e.message);
  process.exit(1);
}

// 2. onclick 引用的函数是否定义
const defined = new Set();
const reDef = /\bfunction\s+([A-Za-z_$][\w$]*)/g;
let d; while ((d = reDef.exec(m[1]))) defined.add(d[1]);
// 变量形式的函数赋值
const reDef2 = /(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*function/g;
while ((d = reDef2.exec(m[1]))) defined.add(d[1]);

const onclickCalls = [...html.matchAll(/\bonclick="([^"]+)"/g)].map(x => x[1]);
const missing = [];
for (const code of onclickCalls) {
  const fn = code.match(/^\s*([A-Za-z_$][\w$]*)\s*\(/);
  if (fn && !defined.has(fn[1])) missing.push(fn[1]);
}
if (missing.length) { console.error('FAIL: undefined onclick handlers:', missing); process.exit(1); }
console.log('OK: all', onclickCalls.length, 'onclick handlers defined (JS-bound, none inline)');

// 3. JS 里 $('xxx') 引用的 ID 是否存在于 HTML
const idsInHtml = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(x => x[1]));
const refs = new Set();
const reRef = /\$\('([^']+)'\)/g;
while ((d = reRef.exec(m[1]))) refs.add(d[1]);
const dynamicPrefixes = ['eq']; // 动态拼接 ID 前缀（本应用无）
const missingIds = [...refs].filter(r => !idsInHtml.has(r) && !dynamicPrefixes.some(p => r.startsWith(p)));
if (missingIds.length) { console.error('FAIL: $() refs missing in HTML:', missingIds); process.exit(1); }
console.log('OK: all', refs.size, 'referenced IDs exist in HTML');

// 4. querySelector('#slotPickGrid') 等动态创建的 ID 一致性（手工清单）
const dynCreated = ['slotPickGrid', 'slotUrl', 'slotUrlOk', 'slotUpBtn', 'slotUp', 'slotRemove', 'slotUnfill', 'edName', 'edTags', 'edSave', 'edDel', 'imCopyUrl', 'imOpen', 'assetPickGrid', 'netSearchInput', 'netSearchGrid', 'netSearchBtn', 'tuneFontSize', 'tuneColor', 'tuneLineHeight', 'tuneReset', 'btnPurgePool'];
const dynUsed = [...m[1].matchAll(/querySelector\('#([A-Za-z]+)'\)/g)].map(x => x[1]);
const missDyn = dynUsed.filter(u => !dynCreated.includes(u));
if (missDyn.length) { console.error('FAIL: dynamically created IDs not declared:', missDyn); process.exit(1); }
console.log('OK: dynamic IDs consistent:', dynUsed.join(', '));
console.log('ALL CHECKS PASSED');
