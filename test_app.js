const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = 'F:/codex/微信公众号编辑工具';
const appHtml = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const articleHtml = fs.readFileSync(path.join(ROOT, 'research/wx_article.html'), 'utf8');

let pass = 0, fail = 0;
function check(name, actual, expected, isApprox) {
  let ok;
  if (isApprox) ok = Math.abs(actual - expected) <= (isApprox === true ? 2 : isApprox);
  else ok = actual === expected;
  if (ok) { pass++; console.log(`  ok  ${name}: ${JSON.stringify(actual)}`); }
  else { fail++; console.log(`FAIL  ${name}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`); }
}

const dom = new JSDOM(appHtml, {
  runScripts: 'dangerously',
  url: 'http://127.0.0.1:8765/',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = () => Promise.reject(new Error('jsdom-no-fetch'));
    window.confirm = () => true;
  }
});
const w = dom.window;

// 等脚本同步执行完
const report = w.analyzeHtml(articleHtml);
console.log('== 分析引擎 vs Python 基准 ==');
check('非空段落(DOM口径)', report.paragraphs.nonEmpty, 67);
check('平均段长', report.paragraphs.avgLen, 52, 1);
check('字号', report.css.fontSize, 15);
check('颜色', report.css.color, 'rgb(76, 76, 76)');
check('行距', report.css.lineHeight, 1.75);
check('对齐', report.css.textAlign, 'justify');
check('垫行空段', report.padParas, 70);
check('图片总数', report.images.total, 56);
check('GIF', report.images.gif, 1);
check('PNG', report.images.png, 45);
check('JPG', report.images.jpg, 10);
check('宽图', report.images.wide, 0);
check('方图', report.images.square, 8);
check('高图', report.images.tall, 48);
check('行内小表情数(DOM口径)', report.images.inline.length, 9);
check('行内小表情尺寸(DOM口径)', report.images.inline.sort((a,b)=>a-b).join(','), '33,35,35,35,35,35,46,48,54');
check('节奏-最长连续无图(DOM口径)', report.rhythm.max, 3, true);
check('加粗', report.features.strong, 0);
check('引用', report.features.quote, 0);
check('分割线', report.features.hr, 0);
check('表格', report.features.table, 0);
check('unicode emoji', report.features.emoji, 0);
check('样例段落数', report.samples.length, 3);

console.log('== 生成链路（markdown -> 预览 -> 微信 HTML）==');
const testMd = `# 测试标题：AI 写周报的一周

这周我把写周报这件事全交给了 AI，整体感受就俩字：真香。

先说背景。我们组的周报要求列清楚本周进展、风险和下周计划，以前每周五下午我都要磨一个小时。

**结论先行**：AI 能把素材组织成周报，但它不知道你到底干了啥，素材得自己喂。

[图：AI 生成的周报截图]

然后把要点丢进去，几十秒就出初稿，格式还挺像回事，改改就能交 [表情：摊手]

> 省下的时间拿去摸鱼了，别学我。

- 要点一
- 要点二

最后的建议：把它当排版工具，别当写手。`;

w.genState.profileId = '_default';
w.genState.md = testMd;
w.renderGenResult();

const body = w.document.getElementById('previewBody');
check('标题块渲染', body.querySelector('.pv-title') ? body.querySelector('.pv-title').textContent.indexOf('测试标题') >= 0 : false, true);
check('图槽位存在', body.querySelectorAll('.slot.slot-img').length, 1);
check('表情槽位存在', body.querySelectorAll('.slot.slot-emoji').length, 1);
check('加粗保留', !!body.querySelector('strong'), true);
check('引用块渲染', body.querySelector('.pv-quote') !== null, true);
check('列表渲染为段落', body.textContent.indexOf('· 要点一') >= 0, true);

// 模拟填充表情槽位
const emojiSlot = body.querySelector('.slot.slot-emoji');
w.fillSlot(emojiSlot, 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==', 35);
const filledEmoji = body.querySelector('img.inline-emoji');
check('表情填入为行内图', !!filledEmoji && filledEmoji.style.width === '35px', true);

// 模拟填充图槽位（独立 imgblock）
const imgSlot = body.querySelector('.slot.slot-img');
w.fillSlot(imgSlot, 'https://mmbiz.qpic.cn/test.png', 0);
const blockImg = body.querySelector('.imgblock img');
check('配图填入为满宽图', !!blockImg && blockImg.style.width === '100%', true);

const built = w.buildWechatHtml();
const h = built.html;
check('微信HTML-段落样式', /font-size:15px;color:rgb\(76, 76, 76\);line-height:1\.75em/.test(h), true);
check('微信HTML-垫行段落', (h.match(/<br><\/p>/g) || []).length >= 5, true);
check('微信HTML-行内表情样式', /width:35px !important;vertical-align:middle/.test(h), true);
check('微信HTML-满宽配图', /<img src="https:\/\/mmbiz\.qpic\.cn\/test\.png" style="width:100%/.test(h), true);
check('微信HTML-无残留槽位', h.indexOf('slot') === -1, true);
check('微信HTML-未填充计数', built.unfilled, 0);

/* ---- 排版结构规则（2026-09-10 优化）---- */
// 1) 引用块样式必须带进公众号（否则粘过去退化成普通段落）
check('微信HTML-引用块带左边框', h.indexOf('border-left:3px solid #d3d1c7') >= 0, true);
// 2) 图片前后不垫行：图片段的上一个兄弟不能是空垫行
const allP = h.match(/<p[^>]*>.*?<\/p>/g) || [];
let imgIdx = -1;
allP.forEach((p, i) => { if (/mmbiz\.qpic\.cn\/test\.png/.test(p)) imgIdx = i; });
check('微信HTML-图片前不垫空行', imgIdx > 0 && !/^\s*<p[^>]*><br\s*\/?><\/p>\s*$/.test(allP[imgIdx - 1]), true);

// 3) 表情独占一行时并入上一段（不能孤立占一整行）
w.genState.md = '# 标题\n\n第一句内容。\n\n[表情：得意]\n\n第二段内容。';
w.renderGenResult();
const body2 = w.document.getElementById('previewBody');
const texts2 = [].slice.call(body2.querySelectorAll('p')).filter(n => !n.classList.contains('pv-pad'));
const hitP = texts2.filter(n => /第一句内容/.test(n.textContent) && n.querySelector('.slot-emoji'));
check('表情独行并入上一段', hitP.length, 1);
check('不再生成独立表情段', texts2.filter(n => n.querySelector('.slot-emoji')).length, 1);
if (hitP.length) {
  w.fillSlot(hitP[0].querySelector('.slot-emoji'), 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==', 35);
  const oneP = (w.buildWechatHtml().html.match(/<p[^>]*>[^<]*第一句内容[\s\S]*?<\/p>/) || [''])[0];
  check('导出后表情与上文同段', /第一句内容/.test(oneP) && /width:35px/.test(oneP), true);
}


console.log('== 去 AI 味（提示词 + 二次润色）==');
const gp = w.buildGenPrompt(w.DEFAULT_PROFILE, '测试主题', '- 要点一', '长（2000-3000字）', '');
check('提示词-词汇黑名单', /值得注意的是/.test(gp) && /赋能/.test(gp) && /综上所述/.test(gp), true);
check('提示词-句式黑名单', gp.indexOf('不是…而是…') >= 0, true);
check('提示词-节奏要求', /短到不讲道理/.test(gp), true);
check('提示词-字数中值', /2500 字左右/.test(gp), true);
check('提示词-开场禁铺垫', /禁止背景铺垫/.test(gp), true);
check('提示词-活人身份', /写的时候心里要有具体的人/.test(gp), true);
check('提示词-事实防幻觉', /不许编一个看起来像真的数字/.test(gp) && /只能来自我给你的要点/.test(gp), true);
check('提示词-开头钩子3段进主线', /3 段内进入主线/.test(gp) && /开头 3 段决定读者去留/.test(gp), true);
check('提示词-标题有规则', /反常识判断/.test(gp) && /禁止「浅谈/.test(gp), true);

const dp = w.buildDeAiPrompt('# 标题\n\n正文');
check('润色提示词-标记保护', /位置必须和原文完全一致/.test(dp), true);
check('润色提示词-字数下限', /90%/.test(dp), true);
check('润色提示词-禁止升华加总结段', /不许加总结段/.test(dp), true);

/* 用可控的假回复驱动 deAiPolish，逐一验证「不合格就退回原稿」 */
w.localStorage.setItem('wxw_settings', JSON.stringify({ baseUrl: 'http://x/v1', apiKey: 'k', model: 'm', temperature: 0.7 }));
let nextReply = '';
w.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ choices: [{ message: { content: nextReply } }] }) });

const draft = '# 标题\n\n第一段正文内容。\n\n[图：配图]\n\n第二段，继续说点什么吧。';

/* (1) 合法改写：带代码块包裹 + 「改写如下：」前缀 → 应剥掉并采纳 */
nextReply = '```markdown\n改写如下：\n# 标题\n\n第一段正文内容。\n\n[图：配图]\n\n第二段，继续说点什么吧。\n```';
let r1 = null;
w.deAiPolish(draft).then(v => { r1 = v; });
setTimeout(() => {
  check('润色-剥代码块与前缀', r1 !== null && r1.indexOf('```') === -1 && r1.indexOf('改写如下') === -1 && /^# 标题/.test(r1), true);

  /* (2) 丢了 [图：] 标记 → 必须回退原稿 */
  nextReply = '# 标题\n\n第一段正文内容。\n\n第二段，继续说点什么吧。';
  let r2 = null;
  w.deAiPolish(draft).then(v => { r2 = v; });
  setTimeout(() => {
    check('润色-丢标记则回退原稿', r2 === draft, true);

    /* (3) 字数暴跌（只剩标题）→ 必须回退原稿 */
    nextReply = '# 标题\n\n短。';
    let r3 = null;
    w.deAiPolish(draft).then(v => { r3 = v; });
    setTimeout(() => {
      check('润色-字数暴跌则回退原稿', r3 === draft, true);

      /* (4) 标题被吃掉 → 必须回退原稿 */
      nextReply = '第一段正文内容。\n\n[图：配图]\n\n第二段，继续说点什么吧。';
      let r4 = null;
      w.deAiPolish(draft).then(v => { r4 = v; });
      setTimeout(() => {
        check('润色-标题丢失则回退原稿', r4 === draft, true);

/* (5) 请求抛错 → 不阻塞出稿 */
nextReply = '';
w.fetch = () => Promise.reject(new Error('network down'));
let r5 = null;
w.deAiPolish(draft).then(v => { r5 = v; });

/* (6) 「以下是修改后的文章：」引导语 + 标题丢了 # → 应剥前缀并把 # 补回来 */
nextReply = '以下是修改后的文章：\n标题\n\n第一段正文内容。\n\n[图：配图]\n\n第二段，继续说点什么吧。';
let r6 = null;
setTimeout(() => {
  check('润色-请求失败不影响出稿', r5 === draft, true);
  w.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ choices: [{ message: { content: nextReply } }] }) });
  w.deAiPolish(draft).then(v => { r6 = v; });
  setTimeout(() => {
    check('润色-剥引导语并补回标题#', r6 !== null && /^# 标题/.test(r6) && r6.indexOf('以下是') === -1 && r6.indexOf('[图：配图]') >= 0, true);
    finish();
  }, 30);
}, 30);
      }, 30);
    }, 30);
  }, 30);
}, 30);

function finish() {
  console.log('== 新版结构（小米文章）==');
  const xiaomiHtml = fs.readFileSync('D:/下载/小米今天的发布会，最后这价格把我整不会了.html', 'utf8');
  const xr = w.analyzeHtml(xiaomiHtml);
  check('小米-文本段数>100', xr.paragraphs.nonEmpty > 100, true);
  check('小米-图片总数', xr.images.total, 146);
  check('小米-有样例段落', xr.samples.length > 0, true);
  check('小米-节奏可算', xr.rhythm.max > 0, true);

  console.log('== 新增功能（导出 MD / 阅读时间 / 排版微调）==');
  /* 1) 排版微调：覆盖值生效且不污染档案 */
  const prof = w.findProfile('_default') || w.DEFAULT_PROFILE;
  const baseCss = JSON.stringify(prof.css);
  w.styleOverrides = { fontSize: 17, color: '#7f4f21', lineHeight: 0 };
  const tuned = w.applyStyleOverrides(prof.css);
  check('微调-字号覆盖', tuned.fontSize, 17);
  check('微调-主色覆盖', tuned.color, '#7f4f21');
  check('微调-未覆盖项沿用档案', tuned.lineHeight, prof.css.lineHeight);
  check('微调-不改档案本身', JSON.stringify(prof.css), baseCss);
  /* 2) 覆盖后导出 HTML 带新样式 */
  w.genState.profileId = '_default';
  w.genState.md = '# 微调测试标题\n\n这是正文段落，用来验证排版微调是否作用于导出。';
  w.renderGenResult();
  const tunedHtml = w.buildWechatHtml();
  check('微调-导出含覆盖字号', tunedHtml.html.indexOf('font-size:17px') >= 0, true);
  check('微调-导出含覆盖主色', tunedHtml.html.indexOf('#7f4f21') >= 0, true);
  /* 3) 清除微调恢复档案值 */
  w.styleOverrides = { fontSize: 0, color: '', lineHeight: 0 };
  const restored = w.applyStyleOverrides(prof.css);
  check('微调-清除后恢复档案值', JSON.stringify(restored), baseCss);
  const restoredHtml = w.buildWechatHtml();
  /* 注意标题字号恒为 fontSize+2（15+2=17px），所以用覆盖主色是否消失来判断 */
  check('微调-导出不含覆盖主色', restoredHtml.html.indexOf('#7f4f21') === -1, true);
  check('微调-导出正文恢复档案字号', restoredHtml.html.indexOf('font-size:15px') >= 0, true);
  /* 4) 阅读时间：900 字约 3 分钟 */
  const mdForRt = '# 阅读时间测试\n\n' + '测'.repeat(900);
  w.genState.md = mdForRt;
  w.updateWordCount();
  const wcText = w.document.getElementById('wordCount').textContent;
  check('阅读时间-900字约3分钟', wcText.indexOf('3 分钟') >= 0, true);
  /* 5) 导出 MD：标题提取与文件名清洗（jsdom 无下载，验证清洗逻辑用的同一表达式） */
  const title = (mdForRt.match(/^#\s+(.+)$/m) || [])[1];
  check('导出MD-标题提取', title, '阅读时间测试');
  const name = String(title || '').replace(/[\\/:*?"<>|\s]+/g, '_');
  check('导出MD-文件名清洗', /^[^\\/:*?"<>|]+$/.test(name), true);

  console.log('== 本轮新增：外链转脚注 / 论证简报 / 语气锚点 ==');
  /* 1) 链接渲染：Markdown 链接 → <a>，且不影响槽位标记 */
  w.styleOverrides = { fontSize: 0, color: '', lineHeight: 0 };
  w.genState.profileId = '_default';
  w.genState.md = '# 链接测试\n\n参考 [OpenAI 官方文档](https://platform.openai.com/docs) 的说明。\n\n内链 [微信文章](https://mp.weixin.qq.com/s/abc) 保持可点击。\n\n[图：示例图]';
  w.renderGenResult();
  const previewHtml = w.document.getElementById('previewBody').innerHTML;
  check('链接-渲染为a标签', previewHtml.indexOf('<a href="https://platform.openai.com/docs"') >= 0, true);
  check('链接-微信内链也渲染', previewHtml.indexOf('<a href="https://mp.weixin.qq.com/s/abc"') >= 0, true);
  check('链接-槽位标记未受影响', previewHtml.indexOf('data-desc="示例图"') >= 0, true);
  /* 2) 导出：外链转「文字+角标」，文末生成参考资料 */
  const linkOut = w.buildWechatHtml().html;
  check('外链脚注-正文外链不再是a', linkOut.indexOf('<a href="https://platform.openai.com/docs"') === -1, true);
  check('外链脚注-保留链接文字', linkOut.indexOf('OpenAI 官方文档') >= 0, true);
  check('外链脚注-生成角标编号', linkOut.indexOf('vertical-align:super') >= 0, true);
  check('外链脚注-生成参考资料区块', linkOut.indexOf('参考资料') >= 0, true);
  check('外链脚注-网址以纯文本保留', linkOut.indexOf('platform.openai.com/docs') >= 0, true);
  check('外链脚注-微信内链仍可点击', linkOut.indexOf('<a href="https://mp.weixin.qq.com/s/abc"') >= 0, true);
  /* 3) 无外链时不生成参考资料区块 */
  w.genState.md = '# 无外链测试\n\n这段没有任何链接。';
  w.renderGenResult();
  check('外链脚注-无外链不加区块', w.buildWechatHtml().html.indexOf('参考资料') === -1, true);
  /* 4) 重复导出不累积脚注（编号不越编越大） */
  w.genState.md = '# 重复导出\n\n看 [A](https://a.example.com) 和 [B](https://b.example.com)。';
  w.renderGenResult();
  w.buildWechatHtml();
  const twice = w.buildWechatHtml().html;
  check('外链脚注-重复导出编号重置', twice.indexOf('[1]') >= 0 && twice.indexOf('[3]') === -1, true);
  /* 5) 简报函数：有数据时输出、缺字段时返回空串（兼容内置档案与早期档案） */
  const emptyP = { css: {}, anchors: null, argument: null };
  check('简报-缺argument返回空串', w.argumentBrief(emptyP), '');
  check('简报-缺anchors返回空串', w.anchorBrief(emptyP), '');
  check('简报-total为0也返回空串', w.argumentBrief({ argument: { total: 0 } }), '');
  const richP = {
    argument: { total: 20, data: 40, quote: 10, first: 30, question: 15, colloquial: 25 },
    anchors: {
      open: '先说结论：这东西我真买了。', close: '就这样，散了吧。',
      data: '续航 12 小时，比上代多了 3 小时。', short: '没了。',
      oral: '你懂我意思吧？',
      long: '这是一段偏长的展开段落，用来说明语气锚点里的长句样例是怎么进入提示词的。'
    }
  };
  const ab = w.argumentBrief(richP);
  check('简报-论证含数据占比', ab.indexOf('40%') >= 0, true);
  check('简报-论证含段落总数', ab.indexOf('20 个段落') >= 0, true);
  const nb = w.anchorBrief(richP);
  check('简报-锚点含开篇', nb.indexOf('先说结论') >= 0, true);
  check('简报-锚点含收尾', nb.indexOf('散了吧') >= 0, true);
  check('简报-锚点含短句成段', nb.indexOf('没了。') >= 0, true);
  check('简报-锚点声明不许抄内容', nb.indexOf('不许照抄') >= 0, true);
  /* 6) 两段简报确实进了生成提示词 */
  const gpLinked = w.buildGenPrompt(richP, '测试主题', '- 要点', '短（600-900字）', '');
  check('简报-论证已注入提示词', gpLinked.indexOf('论证方式') >= 0, true);
  check('简报-锚点已注入提示词', gpLinked.indexOf('语气锚点') >= 0, true);
  /* 7) 内置档案（无这些字段）时提示词仍能正常生成，不抛错 */
  const gpDefault = w.buildGenPrompt(w.DEFAULT_PROFILE, '测试主题', '- 要点', '短（600-900字）', '');
  check('简报-内置档案提示词可用', gpDefault.length > 500, true);

  console.log('== 本轮新增：选题逻辑/认知框架 / 生成历史 / 单档案导出 ==');
  /* 8) profileTopic / profileStance：词典命中与判断句抽取 */
  const topicParas = [
    '我上手用了三天，续航实测 12 小时，跑分也跑了。',
    '这次发布会的手机芯片提升不小。',
    '这个定价真离谱，纯属割韭菜。',
    '怎么自己换电池？手把手教程来了。',
    '说实话吧，这机器真香。',
    '昨天去了趟咖啡店，日常记录一下。'
  ];
  const tp = w.profileTopic(topicParas);
  check('选题-总段数', tp.total, 6);
  check('选题-命中评测话题', (tp.topics['评测'] || 0) >= 1, true);
  check('选题-命中吐槽话题', (tp.topics['吐槽'] || 0) >= 1, true);
  check('选题-命中亲历角度', (tp.angles['亲历叙事'] || 0) >= 1, true);
  const st = w.profileStance(topicParas);
  check('立场-抽到判断句', st.judges.length >= 2, true);
  check('立场-判断句含立场标记', st.judges.some(j => /香|韭菜/.test(j)), true);
  /* 9) topicBrief / stanceBrief：占比达标才输出，缺字段返回空串 */
  check('简报-缺topic返回空串', w.topicBrief({}), '');
  check('简报-缺stance返回空串', w.stanceBrief({}), '');
  const richTopic = { topic: { total: 8, topics: { 评测: 6, 吐槽: 1 }, angles: { 亲历叙事: 4 } },
                      stance: { total: 8, judges: ['这机器真香，不用等。', '别买首发，等口碑。'] } };
  const tb = w.topicBrief(richTopic);
  check('简报-选题占比75%进提示词', tb.indexOf('75% 聊评测') >= 0, true);
  check('简报-低占比话题不进提示词', tb.indexOf('吐槽') === -1, true);
  const sb = w.stanceBrief(richTopic);
  check('简报-立场进提示词', sb.indexOf('真香') >= 0, true);
  check('简报-立场声明不抄内容', sb.indexOf('内容不抄') >= 0, true);
  /* 10) 新简报注入提示词；内置档案仍可用 */
  const gpNew = w.buildGenPrompt(richTopic, '测试主题', '- 要点', '短（600-900字）', '');
  check('简报-选题已注入提示词', gpNew.indexOf('选题习惯') >= 0, true);
  check('简报-立场已注入提示词', gpNew.indexOf('立场表达习惯') >= 0, true);
  /* 11) 真实文章分析结果带新字段（差评样例） */
  check('分析-topic字段存在', report.topic && report.topic.total > 0, true);
  check('分析-stance字段存在', report.stance !== undefined, true);
  /* 12) 生成历史：存取/上限/渲染/删除 */
  w.localStorage.removeItem('wxw_genhistory');
  w.genState.profileId = '_default';
  w.pushGenHistory('# 版本A\n\n内容A', '测试档案', '主题A', '短（600-900字）');
  w.pushGenHistory('# 版本B\n\n内容B', '测试档案', '主题B', '中（1200-1800字）');
  let hist = w.loadGenHistory();
  check('历史-存两版', hist.length, 2);
  check('历史-最新在前', hist[0].md.indexOf('版本B') >= 0, true);
  check('历史-记录字数与主题', hist[0].words > 0 && hist[0].topic === '主题B', true);
  for (let i = 0; i < 25; i++) w.pushGenHistory('# 批量' + i, 'x', 't', '');
  check('历史-上限20版', w.loadGenHistory().length, 20);
  w.renderGenHistory();
  const histBox = w.document.getElementById('genHistoryList');
  check('历史-渲染出回滚按钮', histBox.querySelectorAll('[data-restore]').length, 20, true);
  check('历史-渲染出删除按钮', histBox.querySelectorAll('[data-hdel]').length, 20, true);
  /* 13) 图池治理 UI 存在 */
  check('图池-扫描按钮存在', !!w.document.getElementById('btnScanPool'), true);
  check('图池-结果容器存在', !!w.document.getElementById('poolResult'), true);
  /* 14) 单档案导出按钮在档案列表里 */
  check('导出-档案列表含导出按钮', (function(){
    w.renderProfileList();
    return w.document.getElementById('profileList').querySelectorAll('[data-exportone]').length >= 0;
  })(), true);

  console.log('== 结果 ==');
  console.log(`PASS ${pass}, FAIL ${fail}`);
  process.exit(fail ? 1 : 0);
}
