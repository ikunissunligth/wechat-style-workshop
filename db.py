# -*- coding: utf-8 -*-
"""数据层：SQLite（Python 标准库 sqlite3，零第三方依赖）。

定位：**SQLite 是唯一真源**。
- articles/<job>/index.json 是抓取落盘的产物，启动时会被导入库中，之后只作为图片文件的索引副本；
- 浏览器 localStorage 是前端缓存，启动时从接口拉取覆盖，保存时异步回写；
- 密钥等敏感设置**不入库**，仍留在浏览器本地存储（README 承诺"密钥不出机"）。

表设计的两条主线：
- style_profile + profile_layer：把六层画像拆成可 SQL 查询的行，论文里的统计表与图都从这里出；
- generation + generation_metric + evaluation：为效果评估实验准备，"像不像"从主观判断变成可查的数据。
"""
import os
import json
import time
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
ARTICLES_DIR = os.path.join(ROOT, "articles")
DB_PATH = os.path.join(DATA_DIR, "workshop.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    wx_id       TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS article (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER REFERENCES account(id) ON DELETE SET NULL,
    job_id      TEXT UNIQUE,
    title       TEXT DEFAULT '',
    author      TEXT DEFAULT '',
    url         TEXT DEFAULT '',
    fetched_at  REAL,
    image_count INTEGER DEFAULT 0,
    ok_count    INTEGER DEFAULT 0,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS article_snapshot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER REFERENCES article(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    size        INTEGER DEFAULT 0,
    saved_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS style_profile (
    id          TEXT PRIMARY KEY,
    account_id  INTEGER REFERENCES account(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    tone        TEXT DEFAULT '',
    data_json   TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_layer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  TEXT NOT NULL REFERENCES style_profile(id) ON DELETE CASCADE,
    layer_name  TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_layer_profile ON profile_layer(profile_id);

CREATE TABLE IF NOT EXISTS image_group (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    account_id  INTEGER REFERENCES account(id) ON DELETE SET NULL,
    article_id  INTEGER REFERENCES article(id) ON DELETE SET NULL,
    job_id      TEXT DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS image_asset (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER REFERENCES image_group(id) ON DELETE CASCADE,
    file        TEXT NOT NULL,
    local       TEXT DEFAULT '',
    src         TEXT DEFAULT '',
    md5         TEXT DEFAULT '',
    width       INTEGER DEFAULT 0,
    height      INTEGER DEFAULT 0,
    ratio       REAL DEFAULT 0,
    is_inline   INTEGER DEFAULT 0,
    context     TEXT DEFAULT '',
    size        INTEGER DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_group ON image_asset(group_id);
CREATE INDEX IF NOT EXISTS idx_asset_md5 ON image_asset(md5);

CREATE TABLE IF NOT EXISTS generation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  TEXT REFERENCES style_profile(id) ON DELETE SET NULL,
    profile_name TEXT DEFAULT '',
    topic       TEXT DEFAULT '',
    length_sel  TEXT DEFAULT '',
    model       TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    markdown    TEXT DEFAULT '',
    word_count  INTEGER DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gen_profile ON generation(profile_id);

CREATE TABLE IF NOT EXISTS generation_metric (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER REFERENCES generation(id) ON DELETE CASCADE,
    metric_name   TEXT NOT NULL,
    metric_value  REAL DEFAULT 0,
    target_value  REAL DEFAULT 0,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metric_gen ON generation_metric(generation_id);

CREATE TABLE IF NOT EXISTS evaluation (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER REFERENCES generation(id) ON DELETE SET NULL,
    judge         TEXT DEFAULT '',
    score_style   INTEGER DEFAULT 0,
    score_read    INTEGER DEFAULT 0,
    score_fact    INTEGER DEFAULT 0,
    preferred     INTEGER DEFAULT 0,
    comment       TEXT DEFAULT '',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT DEFAULT '',
    status       TEXT NOT NULL,
    retry_count  INTEGER DEFAULT 0,
    used_snapshot INTEGER DEFAULT 0,
    message      TEXT DEFAULT '',
    created_at   REAL NOT NULL
);
"""


def connect():
    """每次调用新建连接：http.server 多线程，共享连接会有线程安全问题。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def rows_to_list(cur):
    return [dict(r) for r in cur.fetchall()]


def init_db():
    """建表 + 首次建库时把已有的 index.json 导入。重复调用是安全的。

    只在库文件从不存在时才迁移：否则每次启动都会重扫一遍 articles/，
    用户手动清空的记录会被"复活"。"""
    fresh = not os.path.isfile(DB_PATH)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        if fresh:
            imported = _migrate_articles(conn)
            conn.commit()
            return {"created": True, "imported": imported}
        return {"created": False, "imported": 0}
    finally:
        conn.close()


# ---------------------------------------------------------------- 迁移
def _migrate_articles(conn):
    """把 articles/<job>/index.json 导入库。没有 index.json 的目录也要建组，
    否则批量抓图留下的图片在图池里查不到（当前 28 个目录里只有 2 个有 index.json）。"""
    if not os.path.isdir(ARTICLES_DIR):
        return 0
    count = 0
    for job in sorted(os.listdir(ARTICLES_DIR)):
        job_dir = os.path.join(ARTICLES_DIR, job)
        if not os.path.isdir(job_dir) or job.startswith("_"):
            continue
        idx_file = os.path.join(job_dir, "index.json")
        meta = {}
        if os.path.isfile(idx_file):
            try:
                with open(idx_file, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                meta = {}
        if conn.execute("SELECT 1 FROM image_group WHERE job_id=?", (job,)).fetchone():
            continue
        acc_id = _ensure_account(conn, meta.get("author") or job.split("_")[0])
        art_id = None
        if meta.get("url"):
            cur = conn.execute(
                "INSERT INTO article(account_id,job_id,title,author,url,fetched_at,"
                "image_count,ok_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (acc_id, job, meta.get("title", ""), meta.get("author", ""),
                 meta.get("url", ""), meta.get("fetchedAt") or time.time(),
                 meta.get("imageCount", 0), meta.get("okCount", 0), time.time()))
            art_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO image_group(name,account_id,article_id,job_id,created_at)"
            " VALUES(?,?,?,?,?)",
            (job, acc_id, art_id, job, meta.get("fetchedAt") or time.time()))
        gid = cur.lastrowid
        imported = meta.get("images") or []
        if imported:
            for im in imported:
                _insert_asset(conn, gid, im)
        else:
            # 无 index.json：直接扫目录里的图片文件补进图池
            for fn in sorted(os.listdir(job_dir)):
                if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    continue
                rel = "articles/%s/%s" % (job, fn)
                _insert_asset(conn, gid, {"file": rel, "local": "/" + rel, "src": ""})
        count += 1
    return count


def _insert_asset(conn, group_id, im):
    file_rel = im.get("file") or ""
    abs_path = os.path.join(ROOT, file_rel.replace("/", os.sep)) if file_rel else ""
    size = os.path.getsize(abs_path) if abs_path and os.path.isfile(abs_path) else 0
    conn.execute(
        "INSERT INTO image_asset(group_id,file,local,src,md5,width,height,ratio,"
        "is_inline,context,size,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (group_id, file_rel, im.get("local", ""), im.get("src", ""), im.get("md5", ""),
         im.get("width", 0), im.get("height", 0), im.get("ratio", 0),
         1 if im.get("inline") else 0, im.get("context", "") or "", size, time.time()))


# ---------------------------------------------------------------- 账号
def _ensure_account(conn, name):
    name = (name or "未命名账号").strip()
    row = conn.execute("SELECT id FROM account WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO account(name,created_at) VALUES(?,?)", (name, time.time()))
    return cur.lastrowid


def list_accounts():
    conn = connect()
    try:
        rows = rows_to_list(conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM article t WHERE t.account_id=a.id) article_count,"
            " (SELECT COUNT(*) FROM style_profile p WHERE p.account_id=a.id) profile_count"
            " FROM account a ORDER BY a.id"))
        return rows
    finally:
        conn.close()


def create_account(data):
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO account(name,wx_id,note,created_at) VALUES(?,?,?,?)",
            (data.get("name") or "未命名账号", data.get("wx_id", ""),
             data.get("note", ""), time.time()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_account(acc_id, data):
    conn = connect()
    try:
        fields, values = [], []
        for k in ("name", "wx_id", "note"):
            if k in data:
                fields.append("%s=?" % k)
                values.append(data[k])
        if not fields:
            return False
        values.append(acc_id)
        conn.execute("UPDATE account SET %s WHERE id=?" % ",".join(fields), values)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_account(acc_id):
    conn = connect()
    try:
        conn.execute("DELETE FROM account WHERE id=?", (acc_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------- 风格档案
def list_profiles():
    conn = connect()
    try:
        rows = rows_to_list(conn.execute(
            "SELECT id,account_id,name,tone,created_at,updated_at FROM style_profile"
            " ORDER BY updated_at DESC"))
        out = []
        for r in rows:
            p = get_profile(r["id"]) or {}
            p["_meta"] = {"created_at": r["created_at"], "updated_at": r["updated_at"],
                          "account_id": r["account_id"]}
            out.append(p)
        return out
    finally:
        conn.close()


def get_profile(pid):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM style_profile WHERE id=?", (str(pid),)).fetchone()
        if not row:
            return None
        try:
            p = json.loads(row["data_json"])
        except Exception:
            p = {}
        p["id"] = row["id"]
        return p
    finally:
        conn.close()


def save_profile(profile):
    """整档 upsert：前端档案是嵌套对象，整体存 data_json，六层另外拆进 profile_layer。"""
    pid = str(profile.get("id") or ("p_%d" % int(time.time() * 1000)))
    now = time.time()
    conn = connect()
    try:
        existing = conn.execute("SELECT id FROM style_profile WHERE id=?", (pid,)).fetchone()
        acc_id = profile.get("account_id")
        if existing:
            conn.execute("UPDATE style_profile SET name=?,tone=?,data_json=?,updated_at=?,"
                         "account_id=COALESCE(?,account_id) WHERE id=?",
                         (profile.get("name") or "未命名档案", profile.get("tone", ""),
                          json.dumps(profile, ensure_ascii=False), now, acc_id, pid))
        else:
            conn.execute("INSERT INTO style_profile(id,account_id,name,tone,data_json,"
                         "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (pid, acc_id, profile.get("name") or "未命名档案",
                          profile.get("tone", ""), json.dumps(profile, ensure_ascii=False),
                          now, now))
        _save_layers(conn, pid, profile)
        conn.commit()
        return pid
    finally:
        conn.close()


LAYER_FIELDS = [
    ("表层语言", "paragraphs"),
    ("文章结构", "rhythm"),
    ("选题逻辑", "topic"),
    ("素材策略", "argument"),
    ("认知框架", "stance"),
    ("视觉风格", "images"),
]


def _save_layers(conn, pid, profile):
    """六层画像拆表：让"每一层的量化结果"能被 SQL 直接查询与统计。"""
    conn.execute("DELETE FROM profile_layer WHERE profile_id=?", (pid,))
    for layer_name, key in LAYER_FIELDS:
        val = profile.get(key)
        if val in (None, "", {}):
            continue
        conn.execute("INSERT INTO profile_layer(profile_id,layer_name,metrics_json,updated_at)"
                     " VALUES(?,?,?,?)",
                     (pid, layer_name, json.dumps(val, ensure_ascii=False), time.time()))


def get_layers(pid):
    conn = connect()
    try:
        return rows_to_list(conn.execute(
            "SELECT layer_name,metrics_json,updated_at FROM profile_layer"
            " WHERE profile_id=? ORDER BY id", (str(pid),)))
    finally:
        conn.close()


def delete_profile(pid):
    conn = connect()
    try:
        conn.execute("DELETE FROM style_profile WHERE id=?", (str(pid),))
        conn.commit()
        return True
    finally:
        conn.close()


def replace_profiles(profiles):
    """整量同步：前端把整个档案列表传过来，库里多出来的删掉（保持"库为真源"）。"""
    conn = connect()
    try:
        keep = set()
        for p in profiles or []:
            pid = str(p.get("id") or ("p_%d" % int(time.time() * 1000)))
            p["id"] = pid
            keep.add(pid)
            conn.execute("INSERT INTO style_profile(id,account_id,name,tone,data_json,"
                         "created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET name=excluded.name,tone=excluded.tone,"
                         "data_json=excluded.data_json,updated_at=excluded.updated_at",
                         (pid, p.get("account_id"), p.get("name") or "未命名档案",
                          p.get("tone", ""), json.dumps(p, ensure_ascii=False),
                          time.time(), time.time()))
            _save_layers(conn, pid, p)
        if keep:
            marks = ",".join("?" * len(keep))
            conn.execute("DELETE FROM style_profile WHERE id NOT IN (%s)" % marks, tuple(keep))
        else:
            conn.execute("DELETE FROM style_profile")
        conn.commit()
        return len(keep)
    finally:
        conn.close()


# ---------------------------------------------------------------- 生成记录
def _first_title(md):
    for line in str(md or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def add_generation(data):
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO generation(profile_id,profile_name,topic,length_sel,model,title,"
            "markdown,word_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(data.get("profileId") or ""), data.get("profileName", ""),
             data.get("topic", ""), data.get("lengthSel", ""), data.get("model", ""),
             data.get("title") or _first_title(data.get("markdown", "")),
             data.get("markdown", ""),
             int(data.get("wordCount") or 0), data.get("createdAt") or time.time()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_generations(limit=50, profile_id=None):
    conn = connect()
    try:
        if profile_id:
            rows = rows_to_list(conn.execute(
                "SELECT id,profile_id,profile_name,topic,length_sel,model,title,word_count,"
                "created_at FROM generation WHERE profile_id=? ORDER BY created_at DESC LIMIT ?",
                (str(profile_id), limit)))
        else:
            rows = rows_to_list(conn.execute(
                "SELECT id,profile_id,profile_name,topic,length_sel,model,title,word_count,"
                "created_at FROM generation ORDER BY created_at DESC LIMIT ?", (limit,)))
        return rows
    finally:
        conn.close()


def get_generation(gid):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM generation WHERE id=?", (gid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_generation(gid):
    """删一条生成稿。指标与盲评必须显式先删：

    generation_metric 是 ON DELETE CASCADE 会自动清，但 evaluation 是
    ON DELETE SET NULL——不先删就会留下 generation_id 为空的孤儿评分，
    汇总表会多出一行来源不明的记录，实验数据就脏了。
    """
    conn = connect()
    try:
        with conn:
            conn.execute("DELETE FROM generation_metric WHERE generation_id=?", (gid,))
            conn.execute("DELETE FROM evaluation WHERE generation_id=?", (gid,))
            cur = conn.execute("DELETE FROM generation WHERE id=?", (gid,))
            return cur.rowcount
    finally:
        conn.close()


def add_metrics(generation_id, metrics):
    """metrics: [{name, value, target}]——生成稿的量化指标，实验评估的数据源。"""
    conn = connect()
    try:
        now = time.time()
        for m in metrics or []:
            conn.execute("INSERT INTO generation_metric(generation_id,metric_name,"
                         "metric_value,target_value,updated_at) VALUES(?,?,?,?,?)",
                         (generation_id, m.get("name", ""), float(m.get("value") or 0),
                          float(m.get("target") or 0), now))
        conn.commit()
        return len(metrics or [])
    finally:
        conn.close()


def list_metrics(generation_id=None):
    conn = connect()
    try:
        if generation_id:
            return rows_to_list(conn.execute(
                "SELECT * FROM generation_metric WHERE generation_id=? ORDER BY id",
                (generation_id,)))
        return rows_to_list(conn.execute(
            "SELECT m.*,g.title,g.profile_name FROM generation_metric m"
            " JOIN generation g ON g.id=m.generation_id ORDER BY m.id DESC LIMIT 500"))
    finally:
        conn.close()


# ---------------------------------------------------------------- 评估（实验）
def add_evaluation(data):
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO evaluation(generation_id,judge,score_style,score_read,score_fact,"
            "preferred,comment,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (data.get("generationId"), data.get("judge", ""), int(data.get("scoreStyle") or 0),
             int(data.get("scoreRead") or 0), int(data.get("scoreFact") or 0),
             1 if data.get("preferred") else 0, data.get("comment", ""), time.time()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_evaluations(generation_id=None):
    conn = connect()
    try:
        if generation_id:
            return rows_to_list(conn.execute(
                "SELECT * FROM evaluation WHERE generation_id=? ORDER BY created_at DESC",
                (generation_id,)))
        return rows_to_list(conn.execute("SELECT * FROM evaluation ORDER BY created_at DESC LIMIT 200"))
    finally:
        conn.close()


def eval_next(count=2, topic=None):
    """盲评抽题：随机取 count 篇生成稿，**只下发正文相关字段**。

    profile_id / profile_name / model 一律不下发——评委在客户端看不到来源，
    才算盲评；想作弊得另外发请求去查，那是自己拆自己的实验。
    同主题优先（A/B 才有可比性）；凑不齐就退化为全库随机。
    """
    count = max(1, min(int(count or 2), 5))
    conn = connect()
    try:
        if not topic:
            row = conn.execute(
                "SELECT topic FROM generation WHERE topic<>'' "
                "GROUP BY topic HAVING COUNT(*)>=? ORDER BY RANDOM() LIMIT 1",
                (count,)).fetchone()
            topic = row["topic"] if row else None
        if topic:
            cur = conn.execute(
                "SELECT id,title,markdown,word_count,topic FROM generation "
                "WHERE topic=? ORDER BY RANDOM() LIMIT ?", (topic, count))
        else:
            cur = conn.execute(
                "SELECT id,title,markdown,word_count,topic FROM generation "
                "ORDER BY RANDOM() LIMIT ?", (count,))
        return rows_to_list(cur)
    finally:
        conn.close()


def eval_export():
    """主观盲评 × 客观指标联表——论文里的对比表直接取这个。

    指标用「行转列」子查询再 join：直接 join generation_metric 会让每个指标
    变成一行，COUNT(e.id) 和 AVG 会被放大成倍数，数就废了。
    """
    conn = connect()
    try:
        return rows_to_list(conn.execute(
            "SELECT g.id, g.title, g.profile_name, g.model, g.topic, g.word_count,"
            " COUNT(e.id) judge_count,"
            " ROUND(AVG(e.score_style),2) avg_style,"
            " ROUND(AVG(e.score_read),2) avg_read,"
            " ROUND(AVG(e.score_fact),2) avg_fact,"
            " COALESCE(SUM(e.preferred),0) preferred_count,"
            " m.ai_flavor, m.ai_flavor_per1k, m.sent_len_cv, m.avg_para_len"
            " FROM generation g"
            " LEFT JOIN evaluation e ON e.generation_id=g.id"
            " LEFT JOIN (SELECT generation_id,"
            "   MAX(CASE WHEN metric_name='aiFlavor' THEN metric_value END) ai_flavor,"
            "   MAX(CASE WHEN metric_name='aiFlavorPer1k' THEN metric_value END) ai_flavor_per1k,"
            "   MAX(CASE WHEN metric_name='sentLenCv' THEN metric_value END) sent_len_cv,"
            "   MAX(CASE WHEN metric_name='avgParaLen' THEN metric_value END) avg_para_len"
            "   FROM generation_metric GROUP BY generation_id) m ON m.generation_id=g.id"
            " GROUP BY g.id ORDER BY judge_count DESC, avg_style DESC"))
    finally:
        conn.close()


def eval_summary():
    """按生成稿汇总盲评结果：平均分、被偏好次数、评委数。论文里的对比表直接取这个。"""
    conn = connect()
    try:
        return rows_to_list(conn.execute(
            "SELECT e.generation_id, g.title, g.profile_name, COUNT(*) judge_count,"
            " ROUND(AVG(e.score_style),2) avg_style, ROUND(AVG(e.score_read),2) avg_read,"
            " ROUND(AVG(e.score_fact),2) avg_fact, SUM(e.preferred) preferred_count"
            " FROM evaluation e LEFT JOIN generation g ON g.id=e.generation_id"
            " GROUP BY e.generation_id ORDER BY avg_style DESC"))
    finally:
        conn.close()


# ---------------------------------------------------------------- 抓取日志
def add_fetch_log(url, status, retry_count=0, used_snapshot=False, message=""):
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO fetch_log(url,status,retry_count,used_snapshot,message,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (url, status, int(retry_count or 0), 1 if used_snapshot else 0,
             (message or "")[:500], time.time()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_fetch_logs(limit=100):
    conn = connect()
    try:
        return rows_to_list(conn.execute(
            "SELECT * FROM fetch_log ORDER BY created_at DESC LIMIT ?", (limit,)))
    finally:
        conn.close()


# ---------------------------------------------------------------- 文章与素材
def upsert_article(meta):
    """抓取成功后写库：article + image_group + image_asset + snapshot。"""
    conn = connect()
    try:
        acc_id = _ensure_account(conn, meta.get("author") or "未命名账号")
        job = meta.get("jobId") or ("job_%d" % int(time.time() * 1000))
        row = conn.execute("SELECT id FROM article WHERE job_id=?", (job,)).fetchone()
        if row:
            conn.execute("UPDATE article SET title=?,author=?,url=?,fetched_at=?,"
                         "image_count=?,ok_count=?,account_id=? WHERE id=?",
                         (meta.get("title", ""), meta.get("author", ""), meta.get("url", ""),
                          meta.get("fetchedAt") or time.time(), meta.get("imageCount", 0),
                          meta.get("okCount", 0), acc_id, row["id"]))
            art_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO article(account_id,job_id,title,author,url,fetched_at,"
                "image_count,ok_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (acc_id, job, meta.get("title", ""), meta.get("author", ""),
                 meta.get("url", ""), meta.get("fetchedAt") or time.time(),
                 meta.get("imageCount", 0), meta.get("okCount", 0), time.time()))
            art_id = cur.lastrowid
        grow = conn.execute("SELECT id FROM image_group WHERE job_id=?", (job,)).fetchone()
        if grow:
            gid = grow["id"]
            conn.execute("DELETE FROM image_asset WHERE group_id=?", (gid,))
        else:
            cur = conn.execute(
                "INSERT INTO image_group(name,account_id,article_id,job_id,created_at)"
                " VALUES(?,?,?,?,?)", (job, acc_id, art_id, job, time.time()))
            gid = cur.lastrowid
        for im in meta.get("images") or []:
            _insert_asset(conn, gid, im)
        if meta.get("snapshot"):
            snap = os.path.join(ROOT, meta["snapshot"].replace("/", os.sep))
            conn.execute("INSERT INTO article_snapshot(article_id,file_path,size,saved_at)"
                         " VALUES(?,?,?,?)",
                         (art_id, meta["snapshot"],
                          os.path.getsize(snap) if os.path.isfile(snap) else 0, time.time()))
        conn.commit()
        return {"article_id": art_id, "group_id": gid, "account_id": acc_id}
    finally:
        conn.close()


def list_articles():
    """给 /api/articles 用：输出格式与原来的 index.json 列表保持一致，前端不用改。"""
    conn = connect()
    try:
        groups = rows_to_list(conn.execute("SELECT * FROM image_group ORDER BY created_at DESC"))
        arts = {a["id"]: a for a in rows_to_list(conn.execute("SELECT * FROM article"))}
        out = []
        for g in groups:
            art = arts.get(g["article_id"]) or {}
            assets = rows_to_list(conn.execute(
                "SELECT file,local,src,width,height,ratio,is_inline,context FROM image_asset"
                " WHERE group_id=? ORDER BY id", (g["id"],)))
            out.append({
                "jobId": g["job_id"],
                "title": art.get("title", ""),
                "author": art.get("author", ""),
                "url": art.get("url", ""),
                "fetchedAt": art.get("fetched_at") or g["created_at"],
                "imageCount": len(assets),
                "okCount": len(assets),
                "images": [{"file": a["file"], "local": a["local"] or ("/" + a["file"]),
                            "src": a["src"], "width": a["width"], "height": a["height"],
                            "ratio": a["ratio"], "inline": bool(a["is_inline"]),
                            "context": a["context"]} for a in assets],
            })
        out.sort(key=lambda x: x.get("fetchedAt", 0), reverse=True)
        return out
    finally:
        conn.close()


def list_images(group=None, tiny_limit=0):
    conn = connect()
    try:
        sql = ("SELECT a.*, g.name group_name, g.job_id FROM image_asset a"
               " JOIN image_group g ON g.id=a.group_id")
        args = []
        if group:
            sql += " WHERE g.job_id=?"
            args.append(group)
        sql += " ORDER BY g.job_id, a.file"
        rows = rows_to_list(conn.execute(sql, args))
        if tiny_limit:
            for r in rows:
                r["tiny"] = (0 < r["width"] < tiny_limit) or (0 < r["height"] < tiny_limit)
        return rows
    finally:
        conn.close()


def delete_images(files):
    """清理图片：删文件的同时把库里的记录也删掉，避免图池里留悬空引用。"""
    conn = connect()
    try:
        removed = 0
        for f in files or []:
            rel = (f.get("file") or f) if isinstance(f, dict) else f
            rel = str(rel).replace("\\", "/")
            conn.execute("DELETE FROM image_asset WHERE file=? OR local=?",
                         (rel, "/" + rel.lstrip("/")))
            removed += 1
            fp = os.path.join(ROOT, rel.replace("/", os.sep))
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass
        conn.commit()
        return removed
    finally:
        conn.close()


def stats():
    """给状态栏/论文用的一眼概览。"""
    conn = connect()
    try:
        def c(t):
            return conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        return {"account": c("account"), "article": c("article"),
                "snapshot": c("article_snapshot"), "profile": c("style_profile"),
                "layer": c("profile_layer"), "group": c("image_group"),
                "image": c("image_asset"), "generation": c("generation"),
                "metric": c("generation_metric"), "evaluation": c("evaluation"),
                "fetch_log": c("fetch_log")}
    finally:
        conn.close()


if __name__ == "__main__":
    info = init_db()
    print("init:", info)
    print("stats:", stats())
