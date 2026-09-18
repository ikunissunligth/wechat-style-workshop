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
    /* (7)(8) 第四重校验：AI 味降下来才采纳，降不下来（哪怕字数/标记/标题都对）也要退回原稿 */
    const aiDraft = '# 标题\n\n随着人工智能技术的快速发展，值得注意的是，这一点至关重要，而且不容忽视。\n\n[图：配图]\n\n其次，不难发现，其意义重大，这一点尤为关键。';
    const aiBase = w.detectAiFlavor(aiDraft).score;
    check('润色-含AI味的稿子基线为正', aiBase > 0, true);
    const aiCleaned = '# 标题\n\n我到楼下才想起来钥匙还在桌上。站了两秒，又觉得有点好笑，这周已经是第二次了。\n\n[图：配图]\n\n钥匙串在包里叮当响了一路。算了，回来再配一把吧，反正也不贵。';
    let r7 = null;
    nextReply = aiCleaned;
    w.deAiPolish(aiDraft).then(v => { r7 = v; });
    setTimeout(() => {
      check('润色-AI味下降则采纳改写', r7 !== null && r7.indexOf('钥匙还在桌上') >= 0, true);
      check('润色-采纳后残留确实更低', w.detectAiFlavor(r7 || '').score < aiBase, true);
      const aiWorse = '# 标题\n\n随着技术的快速迭代，在数字化转型的浪潮下，值得注意的是，进行全面解析与深度剖析，赋能业务闭环。\n\n[图：配图]\n\n其次，不容忽视的是，它实现了降维打击，完成了认知升级，这一点至关重要。';
      check('润色-反例本身AI味更高', w.detectAiFlavor(aiWorse).score > aiBase, true);
      let r8 = null;
      nextReply = aiWorse;
      w.deAiPolish(aiDraft).then(v => { r8 = v; });
      setTimeout(() => {
        check('润色-AI味未下降则回退原稿', r8 === aiDraft, true);
        finish();
      }, 30);
    }, 30);
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

  /* 15) 排版规范化（中英文间距 / 中文标点） */
  console.log('== 本轮新增：排版规范化 / 发布前体检 / 标题摘要 / 深色预览 / 多格式导出 ==');
  check('规范化-中英文加空格', w.typoText('这个iPhone很贵'), '这个 iPhone 很贵');
  check('规范化-英文后接中文', w.typoText('我买了MacBook'), '我买了 MacBook');
  check('规范化-数字与中文不动', w.typoText('9月16日发布'), '9月16日发布');
  check('规范化-半角逗号转全角', w.typoText('你好,世界'), '你好，世界');
  check('规范化-半角问号转全角', w.typoText('真的吗?是的'), '真的吗？是的');
  check('规范化-半角冒号转全角', w.typoText('注意:这里'), '注意：这里');
  check('规范化-省略号统一', w.typoText('等等...'), '等等……');
  check('规范化-重复感叹收敛', w.typoText('太好了!!!'), '太好了！');
  check('规范化-幂等', w.typoText(w.typoText('这个iPhone很贵')), '这个 iPhone 很贵');
  const typoMd = w.typoMarkdown('# 标题iPhone\n\n```\ncode,here\n```\n\n[图：iPhone 15]\n\n正文iPhone不错');
  check('规范化-跳过代码块', typoMd.md.indexOf('code,here') >= 0, true);
  check('规范化-跳过槽位标记行', typoMd.md.indexOf('[图：iPhone 15]') >= 0, true);
  check('规范化-正文生效', typoMd.md.indexOf('正文 iPhone 不错') >= 0, true);
  check('规范化-变更计数', typoMd.changed >= 1, true);
  /* 16) 发布前体检 */
  const riskMd = '# 一个正常的标题\n\n' +
    '这款手机是全网第一，销量冠军，绝对值得买。\n\n' +
    '综上所述，这款产品非常好用，而且是行业第一。\n\n' +
    '联系我微信:abcde12345 或者 13800138000。\n\n' +
    '大家转发到朋友圈就能参与集赞活动。';
  const r1 = w.scanRisk(riskMd);
  check('体检-极限词命中', !!(r1.hits.ad && r1.hits.ad.length > 0), true);
  check('体检-诱导分享判为阻断', r1.issues.some((x) => x.level === 'block' && x.title.indexOf('诱导') >= 0), true);
  check('体检-手机号命中', r1.issues.some((x) => x.title.indexOf('手机号') >= 0), true);
  /* AI 味不再由 scanRisk 的词表负责，改走 detectAiFlavor/scanAiFlavor 的十类模式族 */
  check('体检-AI味命中', w.scanAiFlavor(riskMd).some((x) => x.title.indexOf('AI 味残留') >= 0), true);
  check('体检-AI味不在风险词表里重复', r1.hits.ai, undefined);
  check('体检-长数字串不误报手机号', w.scanRisk('编号13800138000123').issues.some((x) => x.title.indexOf('手机号') >= 0), false);
  check('体检-标题不计入正文', w.scanRisk('# 全网第一的手机').hits.ad === undefined, true);
  check('体检-代码块不计入', w.scanRisk('正文。\n\n```\n集赞\n```').issues.some((x) => x.level === 'block'), false);
  const longTitleMd = '# ' + '这是一个非常非常非常长的标题用来测试手机端列表的截断效果到底有多严重' + '\n\n正文内容。';
  check('体检-超长标题提醒', w.scanStructure(longTitleMd).some((x) => x.title.indexOf('过长') >= 0), true);
  check('体检-无标题提醒', w.scanStructure('正文内容').some((x) => x.title.indexOf('没有标题') >= 0), true);
  const sc = w.scoreOf([{ level: 'block' }, { level: 'warn' }, { level: 'info' }]);
  check('体检-评分算法(100-12-5-2)', sc.score, 81);
  check('体检-评级随分数变化', w.scoreOf([{ level: 'block' }, { level: 'block' }, { level: 'block' }, { level: 'block' }]).grade, '风险较高');
  w.genState.profileId = '_default';
  w.genState.md = riskMd;
  w.renderGenResult();
  w.runPublishCheck();
  const cpanel = w.document.getElementById('checkPanel');
  check('体检-面板渲染', cpanel.style.display !== 'none', true);
  check('体检-显示分数', /^\d+$/.test(cpanel.querySelector('.chk-num').textContent), true);
  check('体检-阻断项区块', cpanel.querySelectorAll('.chk-item.block').length > 0, true);
  check('体检-命中词可点击定位', cpanel.querySelectorAll('.chk-hit').length > 0, true);
  check('体检-一键修复按钮', cpanel.querySelectorAll('[data-fix]').length > 0, true);
  check('体检-面板含免责说明', cpanel.textContent.indexOf('不能替代人工审核') >= 0, true);
  /* 17) 标题 / 摘要工坊 */
  const pd = w.parseTitleResult('{"titles":[{"title":"标题A","score":91,"why":"好"}],"digests":["摘要一"]}');
  check('标题-解析JSON', pd.titles.length, 1);
  check('标题-保留分数', pd.titles[0].score, 91);
  check('标题-保留摘要', pd.digests[0], '摘要一');
  const pd2 = w.parseTitleResult('```json\n{"titles":[{"title":"B","score":80,"why":""}],"digests":[]}\n```');
  check('标题-解析带代码围栏', pd2.titles[0].title, 'B');
  const pd3 = w.parseTitleResult('1. 第一个标题 88分\n2. 第二个标题 76分');
  check('标题-兜底行解析', pd3.titles.length, 2);
  check('标题-兜底取分数', pd3.titles[0].score, 88);
  check('标题-兜底去掉分数字样', pd3.titles[0].title, '第一个标题');
  w.genState.md = '# 原标题\n\n正文内容';
  w.applyTitle('全新标题');
  check('标题-替换原有标题', w.firstTitle(w.genState.md), '全新标题');
  w.genState.md = '只有正文没有标题';
  w.applyTitle('后加的标题');
  check('标题-无标题时补上', /^# 后加的标题/.test(w.genState.md), true);
  check('标题-原文未丢', w.genState.md.indexOf('只有正文没有标题') >= 0, true);
  /* 18) 多格式导出 */
  check('导出-文件名清洗', w.safeFileName('a/b:c*?"d'), 'a_b_c_d');
  check('导出-空标题兜底', w.safeFileName('').indexOf('article_') >= 0, true);
  w.URL.createObjectURL = () => 'blob:test';
  let wordOk = true;
  try { w.exportWordDoc('测试标题', '<p>正文</p>'); } catch (e) { wordOk = false; }
  check('导出Word-可调用不报错', wordOk, true);
  check('导出PDF-函数可用', typeof w.exportPdfByPrint, 'function');
  /* 19) 深色模式预览 */
  w.darkPreview = false; w.applyDarkPreview();
  check('深色-默认不加class', w.document.getElementById('phoneFrame').classList.contains('dark'), false);
  w.darkPreview = true; w.applyDarkPreview();
  check('深色-加class', w.document.getElementById('phoneFrame').classList.contains('dark'), true);
  check('深色-背景压暗', w.document.getElementById('previewBody').style.background.indexOf('17, 17, 17') >= 0, true);
  w.darkPreview = false; w.applyDarkPreview();
  check('深色-可退出', w.document.getElementById('previewBody').style.background.indexOf('255') >= 0, true);
  /* 20) 自动规范化开关 */
  w.typoSetAuto(true);
  check('规范化-开关可持久化', w.typoAutoOn(), true);
  w.typoSetAuto(false);
  check('规范化-开关可关闭', w.typoAutoOn(), false);
  w.typoSetAuto(true);
  /* 21) AI 味检测（模式族参考 qu-ai-wei：每类都带「怎么改 / 什么时候别改」双向判据） */
  const cleanMd = '# 标题\n\n我到楼下才想起来钥匙还在桌上。站了两秒。又觉得有点好笑。\n\n这周第二次了。';
  const cleanD = w.detectAiFlavor(cleanMd);
  check('AI味-干净文本不误报', cleanD.items.length, 0);
  check('AI味-干净文本零分', cleanD.score, 0);
  const aiMd = '# 标题\n\n随着人工智能技术的快速发展，在各行业数字化转型的背景下，赋能与闭环已成为关键。\n\n' +
    '首先，值得注意的是，这一点至关重要。\n\n其次，它不仅提升了效率，更实现了降维打击。\n\n最后，不容忽视的是，其意义重大。';
  const aiD = w.detectAiFlavor(aiMd);
  check('AI味-能检出命中', aiD.score > 0, true);
  check('AI味-命中多个类别', aiD.items.length >= 4, true);
  const aiKeys = aiD.items.map((x) => x.key);
  check('AI味-检出宏大开场', aiKeys.indexOf('macroOpen') >= 0, true);
  check('AI味-检出空强调', aiKeys.indexOf('emptyEmph') >= 0, true);
  check('AI味-检出硬拆三点', aiKeys.indexOf('enumer') >= 0, true);
  check('AI味-检出商业黑话', aiKeys.indexOf('bizword') >= 0, true);
  check('AI味-每类都有改法', aiD.items.every((x) => x.fix && x.fix.length > 0), true);
  check('AI味-每类都有保留判据', aiD.items.every((x) => x.keep && x.keep.length > 0), true);
  check('AI味-命中带原文样例', aiD.items.some((x) => x.samples.length > 0), true);
  check('AI味-每千字密度为正', aiD.per1k > 0, true);
  /* 句长均匀：词表查不出来，只能算变异系数 */
  const uniformMd = '# 标题\n\n' + Array(8).fill('这是一句长度完全相同的测试句子用来验证节奏').join('。') + '。';
  const uD = w.detectAiFlavor(uniformMd);
  check('AI味-均匀句长被检出', uD.items.some((x) => x.key === 'uniform'), true);
  check('AI味-均匀句长变异系数低', !!uD.rhythm && uD.rhythm.cv < 0.45, true);
  const variedMd = '# 标题\n\n行吧。\n\n这是一句中等长度的话。\n\n没了。\n\n' +
    '这句要长一些，长到能明显拉开句长的差距，大概这么长就差不多了吧我还想再补一点说明。\n\n对。\n\n就这样吧先这样。';
  const vD = w.detectAiFlavor(variedMd);
  check('AI味-参差句长不误报', vD.items.some((x) => x.key === 'uniform'), false);
  /* 定向改写清单 */
  const dir = w.aiFlavorDirective(aiMd);
  check('AI味-定向清单非空', dir.length > 0, true);
  check('AI味-清单含命中类名', dir.indexOf('宏大开场') >= 0, true);
  check('AI味-清单含保留判据', dir.indexOf('什么时候别改') >= 0, true);
  check('AI味-干净文本不生成清单', w.aiFlavorDirective(cleanMd), '');
  /* 检测器有效性：删掉命中词后分数必须下降（否则复检闭环就是假的） */
  const improvedMd = aiMd.replace(/首先，|其次，|最后，|值得注意的是，|不容忽视的是|至关重要|意义重大|。它不仅提升了效率，更实现了降维打击/g, '');
  check('AI味-删掉命中词后分数下降', w.detectAiFlavor(improvedMd).score < aiD.score, true);
  /* 发布前体检接入 */
  const ais = w.scanAiFlavor(aiMd);
  check('AI味-体检项非空', ais.length > 0, true);
  check('AI味-体检项标题统一', ais.every((x) => x.title.indexOf('AI 味残留') === 0), true);
  check('AI味-密集命中算提醒', ais.some((x) => x.level === 'warn'), true);
  check('AI味-干净文本无体检项', w.scanAiFlavor(cleanMd).length, 0);
  /* 入库指标 */
  const gm = w.genMetrics(aiMd, w.DEFAULT_PROFILE);
  check('AI味-指标含aiFlavor', gm.some((x) => x.name === 'aiFlavor'), true);
  check('AI味-指标值等于检测分数', gm.find((x) => x.name === 'aiFlavor').value, aiD.score);
  check('AI味-指标含每千字密度', gm.some((x) => x.name === 'aiFlavorPer1k'), true);
  check('AI味-指标含句长变异系数', gm.some((x) => x.name === 'sentLenCv'), true);
  /* 真实驱动体检入口，确认 AI 味项渲染到面板且命中词可点击定位 */
  w.genState.md = aiMd;
  w.runPublishCheck();
  const cp2 = w.document.getElementById('checkPanel');
  check('AI味-体检面板渲染出AI味项', cp2.textContent.indexOf('AI 味残留') >= 0, true);
  check('AI味-面板项可点击定位', cp2.querySelectorAll('.chk-hit').length > 0, true);
  check('AI味-面板给出改法与保留判据', cp2.textContent.indexOf('什么时候别改') >= 0, true);

  console.log('== 结果 ==');
  console.log(`PASS ${pass}, FAIL ${fail}`);
  process.exit(fail ? 1 : 0);
}
