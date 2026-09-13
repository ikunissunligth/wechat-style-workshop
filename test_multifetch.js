/* 验证：图片素材页批量多链接抓取 + 风格分析页补充图源 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = 'F:/codex/微信公众号编辑工具';
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const fetchCalls = [];

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/',
  pretendToBeVisual: true,
  beforeParse(w) {
    w.fetch = (u, opt) => {
      u = String(u);
      if (u.includes('/api/fetch_article')) {
        fetchCalls.push({ url: u, body: JSON.parse(opt.body) });
        const body = JSON.parse(opt.body);
        /* 第 2 篇模拟失败（链接失效） */
        if (body.url.includes('bad')) {
          return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ error: '文章不存在' }) });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({
          author: '测试号', title: '测试文章', okCount: 10, imageCount: 12, group: body.group || '默认分组'
        }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ items: [] }) });
    };
    w.confirm = () => true;
  }
});
const w = dom.window;

/* 1) 图片素材页批量抓取：3 条链接，1 条失败 */
w.eval(`document.getElementById('assetUrls').value =
  'https://mp.weixin.qq.com/s/a\\nhttps://mp.weixin.qq.com/s/bad\\nhttps://mp.weixin.qq.com/s/c';
document.getElementById('assetGroup').value = '测试分组';`);
w.fetchArticle();

setTimeout(() => {
  const status = w.eval(`document.getElementById('fetchStatus').textContent`);
  console.log('批量抓取调用次数:', fetchCalls.filter(c => c.url.includes('fetch_article')).length, '（期望 3）');
  console.log('分组传递:', fetchCalls.every(c => c.body.group === '测试分组') ? '正确' : '错误');
  console.log('最终状态:', status);
  const btn = w.eval(`document.getElementById('btnFetchArticle').textContent`);
  console.log('按钮恢复:', btn === '抓取文章' ? '是' : '否(' + btn + ')');

  /* 2) 补充图源链路 */
  fetchCalls.length = 0;
  w.eval(`document.getElementById('analyzeExtraUrls').value =
    'https://mp.weixin.qq.com/s/x\\nhttps://mp.weixin.qq.com/s/y';
  fetchExtraImageSources('差评');`);
  setTimeout(() => {
    const calls = fetchCalls.filter(c => c.url.includes('fetch_article'));
    const st = w.eval(`document.getElementById('analyzeExtraStatus').textContent`);
    console.log('--- 补充图源 ---');
    console.log('调用次数:', calls.length, '（期望 2）');
    console.log('归组正确:', calls.every(c => c.body.group === '差评') ? '是' : '否');
    console.log('状态提示:', st);
    process.exit(0);
  }, 400);
}, 600);
