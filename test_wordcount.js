/* 验证：字数统计口径 + 生成后自动续写补齐链路 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = 'F:/codex/微信公众号编辑工具';
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
let aiCalls = 0;

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/',
  pretendToBeVisual: true,
  beforeParse(w) {
    w.fetch = (u) => {
      if (String(u).includes('/api/ai/chat')) {
        aiCalls++;
        /* 本测试直接调 ensureWordCount（初稿已给定 750 字 < 1700），唯一一次调用就是续写，返回 1375 字 */
        const para = '这是一段测试内容，用来验证字数统计的口径是否正确。'; /* 25字/段 */
        const content = Array(55).fill(para).join('\n\n'); /* 续写 1375 字 → 合计 2125 */
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ choices: [{ message: { content } }] }) });
      }
      return Promise.reject(new Error('no-net'));
    };
    w.confirm = () => true;
    w.localStorage.setItem('wxw_settings', JSON.stringify({ apiKey: 'test-key' }));
  }
});
const w = dom.window;

/* 1) 口径测试 */
const cnt = w.eval(`(function(){
  var md = '# 标题行不计入\\n\\n第一段，内容。\\n\\n[图：某图]\\n\\n[表情：开心]\\n\\n第二段：更多内容…';
  return countCnWords(md);
})()`);
console.log('口径测试: 实际=' + cnt + ' 期望=16（标题/槽位标记不计入，"…"算1个，仅正文汉字+全角标点）');

/* 2) ensureWordCount：初稿 750 字 + 选"长(2000-3000)" → 应触发一次续写（750+1375=2125 ≥1700） */
const p = w.DEFAULT_PROFILE || (w.findProfile && w.findProfile('x')) || {};
const res = w.ensureWordCount('# 标题\n\n' + Array(30).fill('这是一段测试内容，用来验证字数统计的口径是否正确。').join('\n\n'), p, '长（2000-3000字）');
res.then((merged) => {
  const n = w.countCnWords(merged);
  console.log('自动续写链路: AI 调用次数=' + aiCalls + '（期望 1 次续写）');
  console.log('续写后总字数=' + n + '（期望 ≥1700）');
  console.log('原文保留: ' + (merged.includes('# 标题') ? '是' : '否'));
  console.log('结果: ' + (aiCalls === 1 && n >= 1700 && merged.includes('# 标题') ? 'PASS' : 'FAIL'));
  process.exit(0);
}).catch((e) => { console.log('FAIL: ' + e.message); process.exit(1); });
