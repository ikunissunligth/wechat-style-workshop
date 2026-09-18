#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公众号风格工坊 - 本地服务
静态托管 index.html，并把 /api/ai/chat 代理到用户配置的大模型 API（OpenAI 兼容格式）。
业务数据落 SQLite（见 db.py，同为 Python 标准库），仅用标准库，无需安装任何依赖。
"""
import base64
import hashlib
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import db

ROOT = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(ROOT, "articles")
APP_REL = "index.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 出站请求一律直连，不读 HTTP_PROXY/HTTPS_PROXY 环境变量。
# 原因：沙箱/代理环境下代理会掐长连接（生成长文 60s+ 被 502），且本工具
# 的目标（dashscope 等 API 与微信 CDN）均无需代理即可直连。
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_sse(resp):
    """读取 OpenAI 兼容流式响应（text/event-stream），拼回完整 content。
    返回 (content, finish_reason, usage)。流式请求连接上持续有数据流动，
    不会被网关按空闲连接掐掉——这是修"生成长文必 502"的关键。"""
    content = []
    finish = None
    usage = None
    buf = b""
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line_b, buf = buf.split(b"\n", 1)
            line = line_b.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return "".join(content), finish, usage
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices", []):
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    return "".join(content), finish, usage


def _ark_hint(base, code_hint):
    """火山方舟专属排查提示。
    方舟有两种接入点：预置的直接传 Model ID（如 doubao-xxx / ark-code-latest），
    自定义的才要 Endpoint ID（ep- 开头）；Agent Plan 走前者。
    所以这里只提示「二选一填对」，不武断要求 ep-。"""
    if not re.search(r"volces\.com|volcengine", base or ""):
        return code_hint
    return code_hint + "。火山方舟分两种：预置接入点填 Model ID（如 doubao-…、" \
        "ark-code-latest，Agent Plan 属此类），自定义接入点才填 ep- 开头的" \
        "接入点 ID；两者混用会报此错"


def _safe_read(e):
    """读 HTTPError 的响应体（可能已消费），失败返回空串。"""
    try:
        return e.read().decode("utf-8", "ignore")[:300]
    except Exception:
        return ""


def 请求过频提示(e):
    """429 时尝试读上游返回的重置时间，给出可操作的提示。"""
    hint = "请求过于频繁（额度或频率限制）"
    try:
        detail = e.read().decode("utf-8", "ignore")
        m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", detail)
        if m:
            hint += "，将于 %s 左右重置" % m.group(1)
    except Exception:
        pass
    return hint


def pick_port(start=8765):
    import socket
    for p in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("8765-8884 端口都被占用，无法启动")


def http_get(url, timeout=30, ua=UA):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
        return resp.read()


def guess_image_type(url, data):
    """按文件头魔数推断图片 MIME 类型。

    原先一律返回 image/jpeg，遇到 PNG/GIF/WebP 时浏览器按 JPEG 解码会失败或显示异常。
    先看魔数（可靠，不依赖 URL），认不出再退回扩展名，最后兜底 image/jpeg。
    纯函数、无副作用，便于单测覆盖各种格式。
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    head = data[:300].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    path = url.split("?")[0].split("#")[0].lower()
    for ext, mime in ((".png", "image/png"), (".gif", "image/gif"), (".webp", "image/webp"),
                      (".svg", "image/svg+xml"), (".bmp", "image/bmp"),
                      (".jpg", "image/jpeg"), (".jpeg", "image/jpeg")):
        if path.endswith(ext):
            return mime
    return "image/jpeg"


# ---------------- 图池治理：pHash 去重与小图过滤 ----------------
# 规划 P1-5。抓取的图片全部入池，重复图（同图不同链接）与小图标会稀释
# 自动配图的命中质量。方案：
#   - aHash（64 位均值哈希）做感知指纹：纯标准库实现——PNG/JPEG 解码借
#     turtle/ tkinter 不可靠，改用最简方案：对图片字节做 8x8 的粗粒度
#     采样不现实，所以指纹基于「字节内容 + 尺寸头」两级：
#       1) 完全相同的字节 → MD5 精确去重（微信 CDN 同一图不同参数链接很常见）
#       2) 不同字节但同格式 → 解析 PNG/JPEG/GIF 的宽高，同尺寸 + 相近体积
#          视为疑似重复
#   实践中微信图片 99% 的重复是「同字节不同 URL」，MD5 一级已覆盖大头；
#   aHash 级别的模糊去重收益低、误杀高（首图 vs 同图不同分辨率），
#   这里只提示不自动删。
# 小图过滤：宽或高 < 200px 的非行内图（配图）标记 tiny，行内表情豁免。

def image_dimensions(data, mime):
    """从二进制头解析图片宽高，认不出返回 (0, 0)。只支持常见三种格式。"""
    try:
        if mime == "image/png" and len(data) > 24:
            import struct
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if mime == "image/gif" and len(data) > 10:
            import struct
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if mime == "image/jpeg":
            # 扫 JPEG 段找 SOF0/SOF2，段结构简单可靠
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC2):
                    import struct
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return int(w), int(h)
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                if seg_len < 2:
                    break
                i += 2 + seg_len
    except Exception:
        pass
    return 0, 0


def scan_image_pool(group=None, tiny_limit=200):
    """扫描 articles/ 下所有本地图片，返回逐文件的指纹与尺寸信息。

    输出: {files: [{file, group, bytes, md5, w, h, inline, tiny}],
           dupGroups: [[file, file, ...], ...],   # 同字节重复组（保留每组第一个）
           tiny: [file, ...],                     # 配图但宽高 < tiny_limit
           summary: {total, dup, tiny, ...}}
    """
    import hashlib
    files = []
    if os.path.isdir(ARTICLES_DIR):
        for d in os.listdir(ARTICLES_DIR):
            idx = os.path.join(ARTICLES_DIR, d, "index.json")
            if not os.path.isfile(idx):
                continue
            try:
                with open(idx, encoding="utf-8") as f:
                    info = json.load(f)
            except Exception:
                continue
            g = info.get("group") or "默认分组"
            if group and g != group:
                continue
            job_dir = os.path.join(ARTICLES_DIR, d)
            for im in info.get("images", []):
                rel = (im.get("file") or "").replace("\\", "/")
                if not rel or not im.get("local"):
                    continue
                fp = os.path.join(ROOT, rel.replace("articles/", "", 1)) \
                    if not rel.startswith("articles/") else os.path.join(ROOT, rel)
                if not os.path.isfile(fp):
                    continue
                with open(fp, "rb") as f:
                    data = f.read()
                mime = guess_image_type(fp, data)
                w, h = image_dimensions(data, mime)
                inline = 0 < im.get("width", 0) <= 64 or (0 < w <= 64)
                files.append({
                    "file": rel,
                    "jobId": d,
                    "group": g,
                    "bytes": len(data),
                    "md5": hashlib.md5(data).hexdigest(),
                    "w": w, "h": h,
                    "inline": bool(inline),
                    "tiny": (not inline) and (0 < w < tiny_limit or 0 < h < tiny_limit),
                })
    # 同字节分组
    by_md5 = {}
    for f in files:
        by_md5.setdefault(f["md5"], []).append(f["file"])
    dup_groups = [v for v in by_md5.values() if len(v) > 1]
    dup_count = sum(len(v) - 1 for v in dup_groups)
    tiny = [f["file"] for f in files if f["tiny"]]
    return {
        "files": files,
        "dupGroups": dup_groups,
        "tiny": tiny,
        "summary": {
            "total": len(files),
            "dupGroups": len(dup_groups),
            "dupCount": dup_count,
            "tinyCount": len(tiny),
            "inlineCount": sum(1 for f in files if f["inline"]),
        },
    }


def purge_images(files):
    """删除指定的一批本地图片文件，并同步更新对应 index.json 的 images 列表。
    返回 {deleted, missing}。按 job 归组重写 index.json，只删 local/file 字段对应的项。"""
    deleted = 0
    missing = 0
    by_job = {}
    for rel in files:
        rel = str(rel).replace("\\", "/")
        parts = rel.split("/")
        if len(parts) < 3 or parts[0] != "articles":
            missing += 1
            continue
        job = parts[1]
        by_job.setdefault(job, []).append("/".join(parts[2:]))
    for job, names in by_job.items():
        idx = os.path.join(ARTICLES_DIR, job, "index.json")
        if not os.path.isfile(idx):
            missing += len(names)
            continue
        try:
            with open(idx, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            missing += len(names)
            continue
        kept = []
        removed = set(names)
        for im in info.get("images", []):
            base = (im.get("file") or "").replace("\\", "/").split("/")[-1]
            if base in removed and im.get("file"):
                fp = os.path.join(ARTICLES_DIR, job, base)
                if os.path.isfile(fp):
                    os.remove(fp)
                    deleted += 1
                else:
                    missing += 1
            else:
                kept.append(im)
        info["images"] = kept
        info["okCount"] = sum(1 for i in kept if i.get("local"))
        info["imageCount"] = len(kept)
        with open(idx, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
    return {"deleted": deleted, "missing": missing}


class ImgCollector(HTMLParser):
    """按文档顺序收集文本块与 <img>（含懒加载 data-src）。
    兼容两代微信编辑器结构：
    - 旧版：<p>text</p> 段落
    - 新版：<section><span leaf="">text</span></section>（无 <p>）"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []   # [{type:'text'|'img', ...}]
        self._p_depth = 0
        self._buf = []
        self._leaf_depth = 0
        self._leaf_buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "p":
            self._p_depth += 1
            if self._p_depth == 1:
                self._buf = []
        elif tag == "span" and "leaf" in a and self._p_depth == 0:
            # 独立 span leaf（新版编辑器段落）；
            # 在 <p> 内的 span leaf 由 <p> 统一收集，避免双重计数
            self._leaf_depth += 1
            if self._leaf_depth == 1:
                self._leaf_buf = []
        elif tag == "img":
            src = a.get("src") or ""
            if not src.startswith(("http://", "https://")):
                src = a.get("data-src") or ""
            if not src:
                return
            st = a.get("style") or ""
            wm = re.search(r"width:\s*(\d+)\s*(?:px)?", st)
            width = int(wm.group(1)) if wm else int(a.get("data-w") or 0)
            ratio = float(a.get("data-ratio") or 0)
            self.blocks.append({
                "type": "img", "src": src, "ratio": ratio, "width": width,
            })

    def handle_endtag(self, tag):
        if tag == "p" and self._p_depth >= 1:
            self._p_depth -= 1
            if self._p_depth == 0:
                txt = "".join(self._buf).strip()
                txt = re.sub(r"\s+", " ", txt)
                if txt:
                    self.blocks.append({"type": "text", "text": txt})
                self._buf = []
        elif tag == "span" and self._leaf_depth >= 1:
            self._leaf_depth -= 1
            if self._leaf_depth == 0:
                txt = "".join(self._leaf_buf).strip()
                txt = re.sub(r"\s+", " ", txt)
                if txt:
                    self.blocks.append({"type": "text", "text": txt})
                self._leaf_buf = []

    def handle_data(self, data):
        if self._p_depth >= 1:
            self._buf.append(data)
        if self._leaf_depth >= 1:
            self._leaf_buf.append(data)

def extract_js_content(html):
    """定位 id=js_content 的 div，按 div 配对深度截取完整正文。
    不能用 .*?<script 截断——微信正文里就嵌着 <script> 标签（如阅读原文组件）。"""
    m = re.search(r'<div[^>]*id="js_content"[^>]*>', html)
    if not m:
        return ""
    start = m.end()
    depth = 1
    pos = start
    tag_re = re.compile(r'<(/?)div\b[^>]*>', re.I)
    for t in tag_re.finditer(html, start):
        if t.group(1):  # </div>
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            return html[start:t.start()]
    return html[start:]  # 没配平就取到底


def fetch_article(url):
    """抓取公众号文章，返回 {title, author, html, images:[...]}"""
    raw = http_get(url, timeout=30)
    html = raw.decode("utf-8", "ignore")

    def _sel(pattern):
        mm = re.search(pattern, html, re.S)
        if not mm:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", mm.group(1))).strip()

    title = _sel(r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>') or _sel(r"<title>(.*?)</title>")
    author = _sel(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>')

    body = extract_js_content(html)
    if not body:
        raise ValueError("没找到正文（js_content），可能不是公众号文章页或页面结构已变化")

    parser = ImgCollector()
    parser.feed(body)

    images = []
    for b in parser.blocks:
        if b["type"] != "img":
            continue
        if "mmbiz.qpic.cn" not in b["src"] and "mmbiz.qlogo.cn" not in b["src"]:
            continue  # 过滤统计打点等杂图
        if "wx_fmt=svg" in b["src"] or "/svg" in urllib.parse.urlparse(b["src"]).path:
            continue
        w = b["width"]
        inline = 0 < w <= 64
        images.append({
            "src": b["src"],
            "ratio": round(b["ratio"], 3),
            "width": w,
            "inline": inline,       # 行内小表情
        })

    return {"title": title, "author": author, "html": html, "images": images,
            "blocks": parser.blocks, "fromSnapshot": from_snapshot}


def context_for(images, blocks):
    """给每张图找原文上下文（图前面的最后一段文字）。"""
    last_text = ""
    idx = 0
    out = []
    for b in blocks:
        if b["type"] == "text":
            last_text = b["text"]
        elif b["type"] == "img":
            while idx < len(images) and images[idx]["src"] != b["src"]:
                idx += 1
            if idx < len(images):
                ctx = last_text if last_text else ""
                out.append(ctx[:60])
                idx += 1
                last_text = ""  # 配图后重新积累
    while len(out) < len(images):
        out.append("")
    return out


def search_images(query, count=30):
    """Bing 图片搜索（免 key），返回 [{src, thumb, title, w, h}]"""
    q = urllib.parse.quote(query)
    url = ("https://www.bing.com/images/async?q=%s&first=0&count=%d&mkt=zh-CN"
           % (q, count))
    html = http_get(url, timeout=20).decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r'class="iusc"[^>]*\sm="([^"]+)"', html):
        try:
            meta = json.loads(m.group(1).replace("&quot;", '"'))
        except Exception:
            continue
        murl = meta.get("murl") or ""
        if not murl.startswith(("http://", "https://")):
            continue
        out.append({
            "src": murl,
            "thumb": meta.get("turl") or murl,
            "title": (meta.get("t") or "")[:60],
            "w": meta.get("mw") or 0,
            "h": meta.get("mh") or 0,
        })
        if len(out) >= count:
            break
    return out


def find_existing(url):
    """同一链接已抓过则直接复用，避免重复下载（一篇文章几十 MB）。"""
    if not os.path.isdir(ARTICLES_DIR):
        return None
    for d in os.listdir(ARTICLES_DIR):
        p = os.path.join(ARTICLES_DIR, d, "index.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                info = json.load(f)
            if info.get("url") == url:
                info.setdefault("jobId", d)
                return info
        except Exception:
            continue
    return None


def find_snapshot(url):
    """按 URL 找已落盘的原文 HTML 快照（规划 P3：文章被删后仍能离线复用）。
    快照统一存 articles/_snapshots/<md5(url)>.html，与抓取缓存分开。"""
    import hashlib
    if not url:
        return None
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    fp = os.path.join(ARTICLES_DIR, "_snapshots", h + ".html")
    if os.path.isfile(fp):
        with open(fp, "rb") as f:
            return f.read()
    return None


def save_snapshot(url, raw):
    """抓取成功后落盘原文快照。失败静默（快照是增强能力，不影响主流程）。"""
    import hashlib
    try:
        os.makedirs(os.path.join(ARTICLES_DIR, "_snapshots"), exist_ok=True)
        h = hashlib.md5(url.encode("utf-8")).hexdigest()
        with open(os.path.join(ARTICLES_DIR, "_snapshots", h + ".html"), "wb") as f:
            f.write(raw)
    except Exception:
        pass


def fetch_article_retry(url, attempts=2):
    """抓取失败自动重试一次（规划 P3：微信 CDN 偶发抖动，重试一次成功率显著提高）。
    全部失败时，若有过往快照则直接用快照（文章被删的场景），并在结果里标记 fromSnapshot。"""
    raw = None
    last_err = None
    for i in range(attempts):
        try:
            raw = http_get(url, timeout=30)
            break
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                import time as _t
                _t.sleep(1.2)
    if raw is None:
        snap = find_snapshot(url)
        if snap:
            return snap, True
        raise last_err
    save_snapshot(url, raw)
    return raw, False


def fetch_article(url):
    """抓取公众号文章，返回 {title, author, html, images:[...]}"""
    raw, from_snapshot = fetch_article_retry(url)
    html = raw.decode("utf-8", "ignore")


def download_images(job_id, images):
    """并发下载图片到 articles/<job_id>/，返回 [{...本地路径, 下载状态}]"""
    out_dir = os.path.join(ARTICLES_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)

    def _dl(i, im):
        url = im["src"]
        ext = ".gif" if "mmbiz_gif" in url or "wx_fmt=gif" in url else (
            ".jpg" if ("mmbiz_jpg" in url or "wx_fmt=jpeg" in url) else ".png")
        fname = "img_%03d%s" % (i, ext)
        rel = "articles/%s/%s" % (job_id, fname)
        try:
            data = http_get(url, timeout=30)
            with open(os.path.join(out_dir, fname), "wb") as f:
                f.write(data)
            local = "/" + rel
        except Exception:
            local = ""
        r = dict(im)
        r["local"] = local
        r["file"] = rel.replace("\\", "/")
        return r

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda t: _dl(t[0], t[1]), enumerate(images)))
    return results


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # 业务 API 路由（数据全部走 db.py）。返回 None 表示不是这类接口，交回旧逻辑。
    # 统一放这里，GET/POST/PUT/DELETE 共用一份，接口清单才能一眼看全。
    # ------------------------------------------------------------------
    def _api_router(self, method, body=None):
        p = self.path.split("?")[0]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        body = body or {}
        try:
            # 概览
            if p == "/api/stats" and method == "GET":
                return 200, db.stats()
            # 账号
            if p == "/api/accounts" and method == "GET":
                return 200, {"items": db.list_accounts()}
            if p == "/api/accounts" and method == "POST":
                return 200, {"id": db.create_account(body)}
            m = re.match(r"^/api/accounts/(\d+)$", p)
            if m:
                if method == "PUT":
                    return 200, {"ok": db.update_account(int(m.group(1)), body)}
                if method == "DELETE":
                    return 200, {"ok": db.delete_account(int(m.group(1)))}
            # 风格档案（sync 必须排在前，否则被下面的 id 规则吃掉）
            if p == "/api/profiles" and method == "GET":
                return 200, {"items": db.list_profiles()}
            if p == "/api/profiles" and method == "POST":
                return 200, {"id": db.save_profile(body.get("profile") or body)}
            if p == "/api/profiles/sync" and method == "POST":
                return 200, {"ok": True, "count": db.replace_profiles(body.get("profiles") or [])}
            m = re.match(r"^/api/profiles/([^/]+)/layers$", p)
            if m and method == "GET":
                return 200, {"items": db.get_layers(urllib.parse.unquote(m.group(1)))}
            m = re.match(r"^/api/profiles/([^/]+)$", p)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                if method == "GET":
                    item = db.get_profile(pid)
                    return (200, {"item": item}) if item else (404, {"error": "档案不存在"})
                if method == "DELETE":
                    return 200, {"ok": db.delete_profile(pid)}
            # 分析结果（画像 + 六层）
            if p == "/api/analyze" and method == "POST":
                return 200, {"id": db.save_profile(body.get("profile") or {})}
            # 生成记录
            if p == "/api/generations" and method == "GET":
                return 200, {"items": db.list_generations(
                    int((q.get("limit") or ["50"])[0]),
                    (q.get("profileId") or [None])[0])}
            if p == "/api/generations" and method == "POST":
                gid = db.add_generation(body)
                if body.get("metrics"):
                    db.add_metrics(gid, body["metrics"])
                return 200, {"id": gid}
            m = re.match(r"^/api/generations/(\d+)/metrics$", p)
            if m and method == "POST":
                return 200, {"count": db.add_metrics(int(m.group(1)), body.get("metrics") or [])}
            m = re.match(r"^/api/generations/(\d+)$", p)
            if m and method == "GET":
                item = db.get_generation(int(m.group(1)))
                return (200, {"item": item}) if item else (404, {"error": "记录不存在"})
            # 指标（实验数据）
            if p == "/api/metrics" and method == "GET":
                return 200, {"items": db.list_metrics()}
            # 盲评（实验）
            if p == "/api/evaluations" and method == "GET":
                return 200, {"items": db.list_evaluations()}
            if p == "/api/evaluations" and method == "POST":
                return 200, {"id": db.add_evaluation(body)}
            if p == "/api/evaluations/summary" and method == "GET":
                return 200, {"items": db.eval_summary()}
            # 抓取日志
            if p == "/api/fetch_logs" and method == "GET":
                return 200, {"items": db.list_fetch_logs(int((q.get("limit") or ["100"])[0]))}
            # 素材（图片索引）
            if p == "/api/images" and method == "GET":
                return 200, {"items": db.list_images((q.get("group") or [None])[0])}
        except Exception as e:
            return 500, {"error": "数据层错误：%s" % e}
        return None

    def do_PUT(self):
        r = self._api_router("PUT", self._read_body())
        self._json(*(r or (404, {"error": "未知接口"})))

    def do_DELETE(self):
        r = self._api_router("DELETE", self._read_body())
        self._json(*(r or (404, {"error": "未知接口"})))

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    NEW_API_POST = ("/api/accounts", "/api/profiles", "/api/profiles/sync", "/api/analyze",
                    "/api/generations", "/api/evaluations")

    def _is_new_api_post(self):
        p = self.path.split("?")[0]
        if p in self.NEW_API_POST:
            return True
        return bool(re.match(r"^/api/(accounts/\d+|profiles/[^/]+|generations/\d+(/metrics)?)$", p))

    def do_GET(self):
        r = self._api_router("GET")
        if r is not None:
            self._json(*r)
            return
        if self.path == "/api/health":
            self._json(200, {"ok": True})
            return
        if self.path.startswith("/api/img_proxy"):
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                target = (qs.get("url") or [""])[0]
                if not target.startswith(("http://", "https://")):
                    self._json(400, {"error": "bad url"})
                    return
                data = http_get(target, timeout=20)
                self.send_response(200)
                self.send_header("Content-Type", guess_image_type(target, data))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self._json(502, {"error": "图片拉取失败"})
            return
        if self.path == "/api/articles":
            try:
                self._json(200, {"items": db.list_articles()})
            except Exception:
                # 库不可用时回落到磁盘扫描，保证抓取成果不会因为数据库问题看不见
                try:
                    items = []
                    if os.path.isdir(ARTICLES_DIR):
                        for d in sorted(os.listdir(ARTICLES_DIR)):
                            idx_file = os.path.join(ARTICLES_DIR, d, "index.json")
                            if os.path.isfile(idx_file):
                                with open(idx_file, encoding="utf-8") as f:
                                    items.append(json.load(f))
                    items.sort(key=lambda x: x.get("fetchedAt", 0), reverse=True)
                    self._json(200, {"items": items})
                except Exception as e:
                    self._json(500, {"error": str(e)})
            return
        if self.path == "/":
            self._serve_file(APP_REL)
            return
        if self.path == "/api/version":
            try:
                mt = os.path.getmtime(os.path.join(ROOT, APP_REL))
                self._json(200, {"version": __import__("time").strftime(
                    "%m-%d %H:%M", __import__("time").localtime(mt))})
            except Exception:
                self._json(200, {"version": "未知"})
            return
        # html/js/css 一律走无缓存通道，避免浏览器拿旧版
        clean = self.path.split("?")[0]
        if clean.endswith((".html", ".js", ".css")) and not clean.startswith("/articles/"):
            self._serve_file(urllib.parse.unquote(clean.lstrip("/")))
            return
        super().do_GET()

    def _serve_file(self, rel):
        fp = os.path.join(ROOT, rel)
        if not os.path.isfile(fp):
            self._json(404, {"error": "文件不存在: " + rel})
            return
        ext = os.path.splitext(rel)[1].lower()
        ctype = {".html": "text/html; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
                 ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8"
                 }.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if ext in (".html", ".js", ".css"):
            # 关键：禁止缓存。否则浏览器一直用旧版页面，改了代码刷新也看不到
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        # POST 的请求体只能读一次，所以先判断是不是数据层接口，是就在这里读掉并分发，
        # 不是则原样交给下面的旧逻辑（旧逻辑各自读 body）。
        if self._is_new_api_post():
            r = self._api_router("POST", self._read_body())
            self._json(*(r or (404, {"error": "未知接口"})))
            return
        if self.path == "/api/fetch_article":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._json(400, {"error": "请求体不是合法 JSON"})
                return
            url = str(body.get("url", "")).strip()
            if not url.startswith(("http://", "https://")):
                self._json(400, {"error": "请输入 http(s) 文章链接"})
                return
            try:
                dup = find_existing(url)
                if dup and not body.get("force"):
                    if body.get("withHtml"):
                        dup = dict(dup)
                        dup["html"] = fetch_article(url)["html"]
                    dup["reused"] = True
                    self._json(200, dup)
                    return
                art = fetch_article(url)
                job_id = "%s_%d" % (re.sub(r"[^\w]+", "_", art["author"] or "文章")[:24],
                                    int(__import__("time").time()))
                images = download_images(job_id, art["images"])
                ctxs = context_for(art["images"], art["blocks"])
                for im, c in zip(images, ctxs):
                    im["context"] = c
                info = {
                    "jobId": job_id,
                    "title": art["title"],
                    "author": art["author"],
                    "url": url,
                    "group": str(body.get("group", "")).strip() or "默认分组",
                    "fetchedAt": __import__("time").time(),
                    "imageCount": len(images),
                    "okCount": sum(1 for i in images if i["local"]),
                    "images": images,
                }
                with open(os.path.join(ARTICLES_DIR, job_id, "index.json"),
                          "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=1)
                # 落库（库为真源；写库失败只记日志，不能让已经抓到的图白费）
                try:
                    meta = dict(info)
                    snap_rel = "articles/_snapshots/%s.html" % hashlib.md5(
                        url.encode("utf-8")).hexdigest()
                    if os.path.isfile(os.path.join(ROOT, snap_rel.replace("/", os.sep))):
                        meta["snapshot"] = snap_rel
                    db.upsert_article(meta)
                    db.add_fetch_log(url, "ok", 0, bool(art.get("fromSnapshot")),
                                     "快照复用" if art.get("fromSnapshot") else "")
                except Exception as e:
                    sys.stderr.write("[db] 写库失败：%s\n" % e)
                if body.get("withHtml"):
                    info = dict(info)
                    info["html"] = art["html"]
                self._json(200, info)
            except Exception as e:
                try:
                    db.add_fetch_log(url, "fail", 1, False, str(e))
                except Exception:
                    pass
                self._json(502, {"error": "抓取失败：%s" % e})
            return
        if self.path == "/api/set_group":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._json(400, {"error": "请求体不是合法 JSON"})
                return
            job = str(body.get("jobId", "")).strip()
            group = str(body.get("group", "")).strip() or "默认分组"
            idx = os.path.join(ARTICLES_DIR, job, "index.json")
            if not job or not os.path.isfile(idx):
                self._json(404, {"error": "找不到该文章"})
                return
            try:
                with open(idx, encoding="utf-8") as f:
                    info = json.load(f)
                info["group"] = group
                with open(idx, "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=1)
                self._json(200, {"ok": True, "group": group})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if self.path == "/api/search_images":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._json(400, {"error": "请求体不是合法 JSON"})
                return
            query = str(body.get("query", "")).strip()
            if not query:
                self._json(400, {"error": "请输入搜索关键词"})
                return
            try:
                self._json(200, {"images": search_images(query, 30)})
            except Exception as e:
                self._json(502, {"error": "搜索失败：%s" % e})
            return
        if self.path == "/api/scan_pool":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                body = {}
            try:
                self._json(200, scan_image_pool(str(body.get("group") or "").strip() or None))
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if self.path == "/api/purge_images":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._json(400, {"error": "请求体不是合法 JSON"})
                return
            files = body.get("files")
            if not isinstance(files, list) or not files:
                self._json(400, {"error": "请提供要删除的文件列表"})
                return
            if len(files) > 500:
                self._json(400, {"error": "单次最多删 500 个文件，请分批"})
                return
            try:
                res = purge_images(files)
                try:
                    db.delete_images(files)  # 文件已删，这里只清库里的悬空记录
                except Exception as e:
                    sys.stderr.write("[db] 清理库记录失败：%s\n" % e)
                self._json(200, res)
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if self.path != "/api/ai/chat":
            self._json(404, {"error": "未知接口"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "请求体不是合法 JSON"})
            return
        base = str(body.get("baseUrl", "")).rstrip("/")
        key = str(body.get("apiKey", ""))
        model = str(body.get("model", ""))
        messages = body.get("messages")
        ok, err = self._validate(base, key, model, messages)
        if not ok:
            self._json(400, {"error": err})
            return

        # 记录配置（绝不记密钥），便于排查"到底连的是哪家、用的哪个接入点"
        sys.stderr.write("[ai-chat] -> %s  model=%s  stream=尝试流式\n"
                         % (base, model))
        sys.stderr.flush()
        url = base + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        }
        # 先走流式：连接上持续有数据流动，不会被网关按空闲连接掐掉（生成长文
        # 60s+ 的关键）。部分网关不支持流式 → 失败后自动回退非流式。
        try:
            content, finish, usage = self._call_upstream(
                url, headers, model, messages, body.get("temperature", 0.7), True)
            if not (content or "").strip():
                # 上游表面接受流式却给了空内容（部分网关会这样），转走非流式
                raise ValueError("流式返回内容为空")
            self._json(200, {
                "choices": [{"message": {"content": content},
                             "finish_reason": finish or "stop"}],
                "usage": usage or {},
            })
            return
        except urllib.error.HTTPError as e:
            # 401/403/404/429 是配置问题，回退也没用，直接报
            if e.code in (401, 403, 404, 429):
                hint = {
                    401: "API Key 无效或未授权",
                    403: "无权限访问该模型",
                    404: _ark_hint(base, "接口地址或模型名可能有误"),
                    429: 请求过频提示(e),
                }.get(e.code, "上游服务错误")
                detail = _safe_read(e)
                self._log_ai_error(e, extra=detail)
                self._json(502, {"error": "%s（HTTP %d）" % (hint, e.code), "detail": detail})
                return
            self._log_ai_error(e, extra=_safe_read(e))
        except Exception as e:
            self._log_ai_error(e)

        # ---- 回退：非流式（兼容不支持 stream 的网关）----
        try:
            with _DIRECT_OPENER.open(self._mk_req(url, headers, model, messages,
                                                  body.get("temperature", 0.7), False),
                                     timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._json(200, data)
        except urllib.error.HTTPError as e:
            hint = {
                401: "API Key 无效或未授权",
                403: "无权限访问该模型",
                404: _ark_hint(base, "接口地址或模型名可能有误"),
                429: 请求过频提示(e),
            }.get(e.code, "上游服务错误")
            detail = _safe_read(e)
            self._log_ai_error(e, extra="非流式回退也失败：" + detail)
            self._json(502, {"error": "%s（HTTP %d）" % (hint, e.code), "detail": detail})
        except Exception as e:
            self._log_ai_error(e, extra="非流式回退也失败")
            self._json(502, {"error": "无法连接上游 API：" + str(e)})

    @staticmethod
    def _mk_req(url, headers, model, messages, temperature, stream):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": bool(stream),
        }
        h = dict(headers)
        if stream:
            h["Accept"] = "text/event-stream"
        return urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=h, method="POST")

    @staticmethod
    def _call_upstream(url, headers, model, messages, temperature, stream):
        with _DIRECT_OPENER.open(Handler._mk_req(url, headers, model, messages,
                                                 temperature, stream),
                                 timeout=300) as resp:
            if not stream:
                data = json.loads(resp.read().decode("utf-8"))
                c = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                return (c or ""), "stop", data.get("usage")
            return _read_sse(resp)

    @staticmethod
    def _validate(base, key, model, messages):
        if not base.startswith(("http://", "https://")):
            return False, "baseUrl 需以 http:// 或 https:// 开头"
        if not key:
            return False, "缺少 apiKey"
        if not model:
            return False, "缺少 model"
        if not isinstance(messages, list) or not messages:
            return False, "messages 需为非空数组"
        return True, ""

    @staticmethod
    def _log_ai_error(e, extra=""):
        """把上游异常写进 stderr，方便事后从日志定位根因。"""
        msg = "[ai-chat] %s: %s" % (type(e).__name__, e)
        if isinstance(e, urllib.error.HTTPError):
            msg += " (HTTP %d, url=%s)" % (e.code, getattr(e, "url", "?"))
        if extra:
            msg += " | %s" % str(extra)[:300]
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()


def already_running(port):
    """同端口已有实例在跑就直接开浏览器，不再起第二个服务。"""
    try:
        with _DIRECT_OPENER.open("http://127.0.0.1:%d/api/health" % port, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if already_running(8765):
        print("服务已在运行，直接打开浏览器…")
        webbrowser.open("http://127.0.0.1:8765/")
        return
    try:
        info = db.init_db()
        if info.get("imported"):
            print("已初始化数据库，导入历史文章 %d 篇" % info["imported"])
        print("数据库：%s" % db.DB_PATH)
    except Exception as e:
        print("数据库初始化失败（将回落到文件模式）：%s" % e)
    port = pick_port()
    url = "http://127.0.0.1:%d/" % port
    print("公众号风格工坊已启动: %s  （关闭本窗口或 Ctrl+C 退出）" % url)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    import threading
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
