/* 全链路自检：服务端 API + 前端运行时错误捕获 + 关键交互
   用法: NODE_PATH=<jsdom路径> node test_full.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const BASE = 'http://127.0.0.1:8765';
const ROOT = 'F:/codex/微信公众号编辑工具';
let pass = 0, fail = 0;
const problems = [];

function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ok   ' + name + (detail ? ' — ' + detail : '')); }
  else { fail++; console.log('FAIL   ' + name + (detail ? ' — ' + detail : '')); problems.push(name + ': ' + (detail || '')); }
}

const realFetch = globalThis.fetch;
async function api(p, opt) {
  const r = await realFetch(BASE + p, opt);
  return { status: r.status, body: await r.json().catch(() => null) };
}

(async function run() {
  console.log('=== A. 服务端 API ===');
  try {
    const h = await api('/api/health');
    ok('健康检查', h.status === 200 && h.body.ok === true);
  } catch (e) { ok('健康检查', false, e.message); }

  const root = await realFetch(BASE + '/');
  const rootHtml = await root.text();
  ok('根路径返回页面', root.status === 200 && rootHtml.includes('公众号风格工坊'));

  const arts = await api('/api/articles');
  ok('文章列表接口', arts.status === 200 && Array.isArray(arts.body.items),
     arts.body && arts.body.items ? arts.body.items.length + ' 篇' : '');

  // 静态图片
  if (arts.body && arts.body.items && arts.body.items[0]) {
    const im = arts.body.items[0].images.filter(i => i.local)[0];
    if (im) {
      const r = await realFetch(BASE + im.local);
      const len = (await r.arrayBuffer()).byteLength;
      ok('静态图片可访问', r.status === 200 && len > 100, Math.round(len / 1024) + 'KB');
    }
  }

  // 404 处理
  const bad = await realFetch(BASE + '/nope.html');
  ok('未知路径不死循环', bad.status === 404 || bad.status === 200, 'status=' + bad.status);

  // 搜图（真实网络）
  try {
    const s = await api('/api/search_images', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: '折叠屏手机' })
    });
    ok('网络搜图', s.status === 200 && s.body.images && s.body.images.length > 0,
       s.body && s.body.images ? s.body.images.length + ' 张' : JSON.stringify(s.body));
  } catch (e) { ok('网络搜图', false, e.message); }

  // 图片代理
  try {
    const pr = await realFetch(BASE + '/api/img_proxy?url=' + encodeURIComponent('https://mmbiz.qpic.cn/mmbiz_png/KS3UYnxFiaT4XGK6icZibHiaBiaZQ/0?wx_fmt=png'));
    ok('图片代理(失效URL应优雅失败)', pr.status === 200 || pr.status === 502, 'status=' + pr.status);
  } catch (e) { ok('图片代理', false, e.message); }

  console.log('\n=== A2. 数据层接口（SQLite）===');
  const H = { 'Content-Type': 'application/json' };
  const st = await api('/api/stats');
  ok('库统计接口', st.status === 200 && st.body && st.body.article > 0,
     st.body ? ('文章 ' + st.body.article + ' / 图片 ' + st.body.image) : '');
  ok('11 张表都有计数', st.body && Object.keys(st.body).length === 11,
     st.body ? Object.keys(st.body).length + ' 张' : '');
  const accs = await api('/api/accounts');
  ok('账号列表', accs.status === 200 && Array.isArray(accs.body.items),
     accs.body && accs.body.items ? accs.body.items.length + ' 个' : '');
  const profs = await api('/api/profiles');
  ok('档案列表', profs.status === 200 && Array.isArray(profs.body.items));
  const gens = await api('/api/generations');
  ok('生成记录列表', gens.status === 200 && Array.isArray(gens.body.items));
  const evs = await api('/api/evaluations/summary');
  ok('盲评汇总', evs.status === 200 && Array.isArray(evs.body.items));
  const flogs = await api('/api/fetch_logs');
  ok('抓取日志', flogs.status === 200 && Array.isArray(flogs.body.items));
  const dbImgs = await api('/api/images');
  ok('素材索引', dbImgs.status === 200 && Array.isArray(dbImgs.body.items),
     dbImgs.body && dbImgs.body.items ? dbImgs.body.items.length + ' 张' : '');
  // 账号建/改/删（用完即删，不留测试数据）
  const na = await api('/api/accounts', { method: 'POST', headers: H,
    body: JSON.stringify({ name: '__接口自检__', note: 'test' }) });
  ok('建账号', na.status === 200 && na.body.id > 0, na.body ? 'id=' + na.body.id : '');
  if (na.body && na.body.id) {
    const up = await api('/api/accounts/' + na.body.id, { method: 'PUT', headers: H,
      body: JSON.stringify({ note: '改过' }) });
    ok('改账号', up.status === 200 && up.body.ok === true);
    const del = await api('/api/accounts/' + na.body.id, { method: 'DELETE', headers: H, body: '{}' });
    ok('删账号', del.status === 200 && del.body.ok === true);
  }
  // 档案存 + 六层拆表 + 删
  const tpid = 'p_selftest_' + Date.now();
  const sp = await api('/api/profiles', { method: 'POST', headers: H, body: JSON.stringify({
    profile: { id: tpid, name: '自检档案', tone: '口语', css: { fontSize: 15 },
      paragraphs: { avgLen: 55 }, rhythm: { avg: 4 }, topic: { total: 3 },
      argument: { total: 3 }, stance: { judges: ['x'] }, images: { total: 9 } } }) });
  ok('存档案', sp.status === 200 && sp.body.id === tpid);
  const lyr = await api('/api/profiles/' + tpid + '/layers');
  ok('六层拆表', lyr.status === 200 && lyr.body.items.length === 6,
     lyr.body ? lyr.body.items.map(x => x.layer_name).join('/') : '');
  const dp = await api('/api/profiles/' + tpid, { method: 'DELETE', headers: H, body: '{}' });
  ok('删档案', dp.status === 200 && dp.body.ok === true);

  console.log('\n=== A3. 盲评全链路（真接口，造数→抽题→打分→汇总→导出→清理）===');
  const etp = 'p_evaltest_' + Date.now();
  await api('/api/profiles', { method: 'POST', headers: H, body: JSON.stringify({
    profile: { id: etp, name: '__盲评自检档案__', tone: '口语', css: { fontSize: 15 },
      paragraphs: { avgLen: 55 }, rhythm: { avg: 4 }, topic: { total: 3 },
      argument: { total: 3 }, stance: { judges: ['x'] }, images: { total: 9 } } }) });
  const etopic = '__盲评自检主题_' + Date.now() + '__';
  const egids = [];
  for (let i = 0; i < 2; i++) {
    const g = await api('/api/generations', { method: 'POST', headers: H, body: JSON.stringify({
      profileId: etp, profileName: '__盲评自检档案__', topic: etopic, model: 'selftest',
      markdown: '# 自检稿' + i + '\n\n正文内容' + i, wordCount: 100 + i,
      metrics: [{ name: 'aiFlavor', value: 5 - i, target: 0 }] }) });
    if (g.body && g.body.id) egids.push(g.body.id);
  }
  ok('造两篇同主题稿', egids.length === 2, egids.join(' / '));
  const nxt = await api('/api/evaluations/next?count=2&topic=' + encodeURIComponent(etopic));
  const nit = (nxt.body && nxt.body.items) || [];
  ok('抽题接口', nxt.status === 200 && nit.length === 2);
  ok('同档案对照口径为 same（A/B 比的是设置不是风格）', nxt.body && nxt.body.mode === 'same');
  ok('抽题不含风格名（服务端匿名）', nit.every(x => x.profile_name === undefined && x.model === undefined));
  ok('抽题带正文', nit.every(x => !!x.markdown));
  const nxtAny = await api('/api/evaluations/next?count=2&mode=any&topic=' + encodeURIComponent(etopic));
  ok('any 模式可用', nxtAny.status === 200 && nxtAny.body.mode === 'any');
  if (nit.length === 2) {
    let posted = 0;
    for (let i = 0; i < 2; i++) {
      const r = await api('/api/evaluations', { method: 'POST', headers: H, body: JSON.stringify({
        generationId: nit[i].id, judge: '__自检评委__', scoreStyle: 5 - i,
        scoreRead: 4, scoreFact: 5, preferred: i === 0 }) });
      if (r.status === 200 && r.body.id) posted++;
    }
    ok('两条评分入库', posted === 2);
    const exp0 = await api('/api/evaluations/export');
    const rows = (exp0.body && exp0.body.items) || [];
    const mine = rows.filter(x => x.topic === etopic);
    ok('导出含本次两篇', mine.length === 2, mine.length + ' 行');
    ok('导出带主观分', mine.every(x => x.avg_style !== undefined && x.judge_count === 1));
    ok('导出带客观指标（行转列后没被放大）',
       mine.some(x => x.ai_flavor !== undefined && x.ai_flavor !== null),
       'aiFlavor=' + (mine.find(x => x.ai_flavor !== null) || {}).ai_flavor);
    ok('偏好只记一次', mine.reduce((a, b) => a + (b.preferred_count || 0), 0) === 1);
  }
  for (const gid of egids) {
    await api('/api/generations/' + gid, { method: 'DELETE', headers: H, body: '{}' });
  }
  const after = await api('/api/evaluations/export');
  ok('删稿后不留孤儿评分',
     !((after.body && after.body.items) || []).some(x => x.topic === etopic));
  await api('/api/profiles/' + etp, { method: 'DELETE', headers: H, body: '{}' });

  console.log('\n=== B. 前端运行时（jsdom，捕获所有错误）===');
  const appHtml = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const errors = [];
  const dom = new JSDOM(appHtml, {
    runScripts: 'dangerously', url: BASE + '/', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o) => realFetch(BASE + u, o);
      w.addEventListener('error', e => errors.push('error: ' + (e.message || e.error)));
      w.addEventListener('unhandledrejection', e => errors.push('rejection: ' + (e.reason && e.reason.message || e.reason)));
      w.alert = () => {};
      w.prompt = () => '测试分组';
    }
  });
  const w = dom.window;
  const d = w.document;
  await new Promise(r => setTimeout(r, 1500)); // 等 init 与异步加载

  ok('页面初始化无未捕获错误', errors.length === 0, errors.join(' | ') || '无');
  ok('四个以上页签存在', d.querySelectorAll('nav button').length >= 5, d.querySelectorAll('nav button').length + ' 个');
  ok('默认停在风格分析页', d.querySelector('#page-analyze').classList.contains('active'));

  // 页签切换
  let tabErr = '';
  d.querySelectorAll('nav button').forEach(b => {
    try { b.click(); } catch (e) { tabErr += b.textContent + ':' + e.message + ' '; }
  });
  ok('所有页签可切换', tabErr === '', tabErr || '无异常');
  ok('切换后无错误', errors.length === 0, errors.join(' | ') || '无');

  // 分析引擎：旧版结构
  const oldHtml = fs.readFileSync(path.join(ROOT, 'research/wx_article.html'), 'utf8');
  const r1 = w.analyzeHtml(oldHtml);
  ok('旧版结构解析(差评)', r1.paragraphs.nonEmpty > 50 && r1.images.total === 56,
     r1.paragraphs.nonEmpty + '段/' + r1.images.total + '图');
  ok('旧版-字号识别', r1.css.fontSize === 15, r1.css.fontSize + 'px');

  // 新版结构（小米，若文件存在）
  const xiaomi = 'D:/下载/小米今天的发布会，最后这价格把我整不会了.html';
  if (fs.existsSync(xiaomi)) {
    const r2 = w.analyzeHtml(fs.readFileSync(xiaomi, 'utf8'));
    ok('新版结构解析(小米)', r2.paragraphs.nonEmpty > 100 && r2.images.total === 146,
       r2.paragraphs.nonEmpty + '段/' + r2.images.total + '图');
  } else {
    console.log('  --   跳过小米文章（文件不存在）');
  }

  // 档案保存 → 下拉 → genProfile（回归之前的 id 类型 bug）
  w.currentReport = r1;
  d.querySelector('#profileName').value = '自检档案';
  d.querySelector('#toneText').value = '口语化';
  d.querySelector('#btnSaveProfile').click();
  const saved = JSON.parse(w.localStorage.getItem('wxw_profiles') || '[]');
  ok('档案已保存', saved.length > 0 && saved[saved.length - 1].name === '自检档案');
  const sel = d.querySelector('#genProfile');
  sel.value = String(saved[saved.length - 1].id);
  ok('选中档案生效(回归id类型bug)', w.genProfile().name === '自检档案', w.genProfile().name);

  // 生成链路
  const md = fs.readFileSync(path.join(ROOT, 'article_draft.md'), 'utf8');
  w.genState.profileId = '_default'; w.genState.md = md;
  w.renderGenResult();
  const slots = d.querySelectorAll('#previewBody .slot');
  ok('槽位渲染', slots.length > 0, slots.length + ' 个');

  // 自动配图
  w.autoFillSlots();
  await new Promise(r => setTimeout(r, 3000));
  const left = d.querySelectorAll('#previewBody .slot').length;
  ok('自动配图填满槽位', left === 0, '剩余 ' + left);
  const imgs = d.querySelectorAll('#previewBody img');
  ok('配图已插入', imgs.length >= slots.length, imgs.length + ' 张');

  // 复制 HTML
  const built = w.buildWechatHtml();
  ok('复制HTML含内联样式', built.html.includes('font-size:15px') && built.html.includes('line-height:1.75em'));
  ok('复制HTML无残留槽位', built.unfilled === 0 && !built.html.includes('class="slot'));
  ok('复制HTML用微信图床原链', /src="https:\/\/mmbiz\.qpic\.cn/.test(built.html));

  // 批量汇总
  const sum = w.summarize([r1, r1], ['测试号']);
  ok('批量汇总', sum.count === 2 && sum.css.fontSize === 15);

  console.log('\n=== C. 运行时错误汇总 ===');
  if (errors.length) { errors.forEach(e => console.log('  ! ' + e)); }
  else console.log('  无');

  console.log('\n=== 结果 ===');
  console.log('PASS ' + pass + ' / FAIL ' + fail);
  if (problems.length) {
    console.log('\n问题清单:');
    problems.forEach((p, i) => console.log('  ' + (i + 1) + '. ' + p));
  }
  dom.window.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('测试脚本崩溃:', e); process.exit(2); });
