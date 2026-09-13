/* 用当前版本代码渲染一篇样例文章 → 输出可离线打开的排版预览
   用途：不开本地服务也能肉眼检查导出到公众号的排版效果 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const ROOT = 'F:/codex/微信公众号编辑工具';
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

/* 从 articles/ 里挑真实图片：第一张正文宽图当满宽配图，体积最小的 PNG 当行内表情 */
const artDir = path.join(ROOT, 'articles');
let wideImg = null, emojiImg = null, emojiSize = Infinity;
(function walk(dir, depth) {
  if (depth > 2) return;
  let ents = [];
  try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
  for (const e of ents) {
    if (e.isDirectory()) { walk(path.join(dir, e.name), depth + 1); continue; }
    const f = path.join(dir, e.name);
    const ext = path.extname(e.name).toLowerCase();
    if (!['.png', '.jpg', '.jpeg', '.gif'].includes(ext)) continue;
    if (!wideImg && /img_00/.test(e.name)) wideImg = f;
    const sz = fs.statSync(f).size;
    if (ext === '.png' && sz > 500 && sz < emojiSize) { emojiSize = sz; emojiImg = f; }
  }
})(artDir, 0);
const rel = (p) => p ? 'articles/' + path.relative(artDir, p).split(path.sep).join('/') : null;
console.log('满宽配图:', rel(wideImg) || '(没找到，用占位)');
console.log('行内表情:', rel(emojiImg) || '(没找到，用占位)');

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://127.0.0.1:8765/', pretendToBeVisual: true,
  beforeParse(w) { w.fetch = () => Promise.reject(new Error('no-net')); w.confirm = () => true; }
});
const w = dom.window;
const md = fs.readFileSync(path.join(ROOT, 'article_draft.md'), 'utf8');
w.genState.md = md;
w.renderGenResult();

const body = w.document.getElementById('previewBody');
body.querySelectorAll('.slot').forEach((s, i) => {
  const kind = s.getAttribute('data-kind');
  if (kind === '图') w.fillSlot(s, rel(wideImg) || 'https://placehold.co/800x450?text=IMAGE', 0, rel(wideImg));
  else w.fillSlot(s, rel(emojiImg) || 'https://placehold.co/48x48?text=E', 35, rel(emojiImg));
});
void w.buildWechatHtml();   /* 触发一次导出，确认无异常 */

const inner = body.innerHTML;
const doc = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>公众号排版预览（当前版本）</title>
<style>
  body{margin:0;padding:28px 16px 60px;background:#eef1f5;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:520px;margin:0 auto}
  h1.t{font-size:15px;color:#475569;font-weight:600;margin:0 0 4px}
  p.sub{font-size:12px;color:#94a3b8;margin:0 0 16px}
  .phone{background:#fff;border-radius:16px;padding:18px 0 26px;box-shadow:0 4px 18px rgba(15,23,42,.10)}
  .preview-body{font-size:15px;color:rgb(76,76,76);line-height:1.75em;text-align:justify;padding:0 8px}
  .pv-pad{height:auto}
  .pv-title{font-weight:600;margin-left:8px;margin-right:8px;line-height:1.5;font-size:17px}
  .preview-body img{max-width:100%}
  .imgblock{text-align:center;margin:0}
  .imgblock img{width:100%;display:block}
  .pv-quote{border-left:3px solid #d3d1c7;padding-left:10px;color:#8c8c8c}
  .pv-code{font-family:Consolas,Menlo,monospace;font-size:12px;line-height:1.7;background:#f7f8fa;color:#334155;padding:10px 12px;border-radius:6px;white-space:pre-wrap;margin:0 8px}
  .inline-emoji{vertical-align:middle;border:0}
  .slot-filled{position:relative;display:inline-block;cursor:pointer;vertical-align:middle}
  .slot-filled.block{display:block;width:100%}
</style></head><body><div class="wrap">
<h1 class="t">公众号排版预览（当前版本 · 已配图）</h1>
<p class="sub">这就是「复制到公众号」会粘贴过去的排版结构：标题→正文紧跟；图片紧贴上下文、上下不垫空行；行内表情嵌在句尾不独占一行。手机上宽度约 375-414px。</p>
<div class="phone"><div class="preview-body" id="previewBody">${inner}</div></div>
</div></body></html>`;
fs.writeFileSync(path.join(ROOT, '排版预览.html'), doc);
console.log('已输出: 排版预览.html');
