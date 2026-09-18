# -*- coding: utf-8 -*-
"""数据层测试：db.py 的建表、CRUD、六层拆表、实验表与迁移。

用临时目录当库文件，不碰项目里真实的 data/workshop.db。
运行：python test_db.py
"""
import os
import sys
import json
import shutil
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import db  # noqa: E402

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print("  ok  %s: %s" % (name, json.dumps(actual, ensure_ascii=False)[:90]))
    else:
        FAIL += 1
        print("FAIL  %s: got %r, want %r" % (name, actual, expected))


def ok(name, cond):
    check(name, bool(cond), True)


tmp = tempfile.mkdtemp(prefix="wxwdb_")
db.DATA_DIR = tmp
db.DB_PATH = os.path.join(tmp, "test.db")
db.ARTICLES_DIR = os.path.join(tmp, "articles")  # 空目录，跳过历史迁移

print("== 建表与迁移 ==")
info = db.init_db()
check("首次建库", info["created"], True)
conn = db.connect()
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
conn.close()
check("表数量", len(tables), 11)
for t in ["account", "article", "article_snapshot", "style_profile", "profile_layer",
          "image_group", "image_asset", "generation", "generation_metric",
          "evaluation", "fetch_log"]:
    ok("存在表 %s" % t, t in tables)
check("重复初始化不报错", db.init_db()["created"], False)

print("== 账号 ==")
aid = db.create_account({"name": "测试号", "wx_id": "testid", "note": "备注"})
ok("建账号返回 id", aid > 0)
check("账号入库", db.list_accounts()[0]["name"], "测试号")
check("账号带文章数", db.list_accounts()[0]["article_count"], 0)
db.update_account(aid, {"note": "改过"})
check("改账号", db.list_accounts()[0]["note"], "改过")

print("== 风格档案与六层 ==")
prof = {
    "id": "p_test", "name": "测试档案", "tone": "口语化",
    "css": {"fontSize": 15, "color": "rgb(76, 76, 76)"},
    "paragraphs": {"avgLen": 55, "maxLen": 90},
    "rhythm": {"avg": 4}, "images": {"total": 10},
    "topic": {"total": 8, "topics": {"评测": 6}},
    "argument": {"total": 5, "data": 2},
    "stance": {"judges": ["别买首发"]},
}
pid = db.save_profile(prof)
check("档案 id", pid, "p_test")
check("读回档案", db.get_profile("p_test")["name"], "测试档案")
check("嵌套字段保留", db.get_profile("p_test")["css"]["fontSize"], 15)
layers = [x["layer_name"] for x in db.get_layers("p_test")]
check("六层拆表", layers, ["表层语言", "文章结构", "选题逻辑", "素材策略", "认知框架", "视觉风格"])
check("层内指标可解析", json.loads(db.get_layers("p_test")[0]["metrics_json"])["avgLen"], 55)
check("列表读回", db.list_profiles()[0]["name"], "测试档案")

print("== 整量同步（前端 saveProfiles 走这条）==")
db.save_profile({"id": "p_old", "name": "待删档案"})
check("同步前两份", len(db.list_profiles()), 2)
db.replace_profiles([prof])
check("同步后只剩传来的", len(db.list_profiles()), 1)
check("被删的是 p_old", db.get_profile("p_old"), None)

print("== 生成记录与指标 ==")
gid = db.add_generation({"profileId": "p_test", "profileName": "测试档案", "topic": "主题",
                         "markdown": "# 标题\n\n正文", "wordCount": 120})
ok("生成记录 id", gid > 0)
check("列表含标题", db.list_generations()[0]["title"], "标题")
db.add_metrics(gid, [{"name": "avgParaLen", "value": 48, "target": 55},
                     {"name": "wordCount", "value": 120, "target": 0}])
check("指标写入", len(db.list_metrics(generation_id=gid)), 2)
check("指标带目标值", db.list_metrics(generation_id=gid)[0]["target_value"], 55)

print("== 盲评与汇总（实验）==")
db.add_evaluation({"generationId": gid, "judge": "评委A", "scoreStyle": 4,
                   "scoreRead": 5, "scoreFact": 3, "preferred": True})
db.add_evaluation({"generationId": gid, "judge": "评委B", "scoreStyle": 5,
                   "scoreRead": 4, "scoreFact": 4, "preferred": True})
s = db.eval_summary()[0]
check("汇总评委数", s["judge_count"], 2)
check("风格均分", s["avg_style"], 4.5)
check("偏好次数", s["preferred_count"], 2)

print("== 抓取日志与文章 ==")
db.add_fetch_log("https://mp.weixin.qq.com/s/abc", "ok", 0, False, "")
db.add_fetch_log("https://mp.weixin.qq.com/s/bad", "fail", 1, True, "超时")
logs = db.list_fetch_logs()
check("日志条数", len(logs), 2)
check("最新的在前", logs[0]["status"], "fail")
check("快照标记", logs[0]["used_snapshot"], 1)

art_dir = os.path.join(db.ARTICLES_DIR, "job_test")
os.makedirs(art_dir, exist_ok=True)
img_path = os.path.join(art_dir, "img_000.png")
with open(img_path, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 120)
res = db.upsert_article({
    "jobId": "job_test", "title": "标题", "author": "测试号", "url": "https://x/y",
    "fetchedAt": 1.0, "imageCount": 1, "okCount": 1,
    "images": [{"file": "articles/job_test/img_000.png", "local": "/articles/job_test/img_000.png",
                "src": "https://mmbiz.qpic.cn/x", "width": 800, "height": 400,
                "ratio": 0.5, "inline": False, "context": "上下文"}],
})
ok("文章落库返回 id", res["article_id"] > 0)
articles = db.list_articles()
check("文章列表", len(articles), 1)
check("图片随文章返回", len(articles[0]["images"]), 1)
check("图片宽高", articles[0]["images"][0]["width"], 800)
check("素材总数", len(db.list_images()), 1)
check("素材带分组名", db.list_images()[0]["group_name"], "job_test")

print("== 删除素材（图池清理同步库）==")
old_root = db.ROOT
db.ROOT = tmp  # 让相对路径解析到临时目录，验证"删库记录的同时删文件"
db.delete_images([{"file": "articles/job_test/img_000.png"}])
db.ROOT = old_root
check("素材已删", len(db.list_images()), 0)
check("文件也删了", os.path.isfile(img_path), False)

print("== 历史迁移（无 index.json 的目录也要建组）==")
tmp2 = tempfile.mkdtemp(prefix="wxwmig_")
os.makedirs(os.path.join(tmp2, "带索引"))
os.makedirs(os.path.join(tmp2, "无索引"))
with open(os.path.join(tmp2, "带索引", "index.json"), "w", encoding="utf-8") as f:
    json.dump({"jobId": "带索引", "title": "T", "author": "A", "url": "https://u",
               "fetchedAt": 1.0, "imageCount": 0, "images": []}, f, ensure_ascii=False)
with open(os.path.join(tmp2, "无索引", "a.png"), "wb") as f:
    f.write(b"0" * 64)
db.DATA_DIR = tmp2
db.DB_PATH = os.path.join(tmp2, "m.db")
db.ARTICLES_DIR = tmp2
info = db.init_db()
check("迁移了 2 个目录", info["imported"], 2)
check("有索引的建了文章", db.stats()["article"], 1)
check("无索引的也建了组", db.stats()["group"], 2)
check("扫目录补进图片", db.stats()["image"], 1)

print("== 盲评抽题（服务端匿名）==")
tmp3 = tempfile.mkdtemp(prefix="wxweval_")
db.DATA_DIR = tmp3
db.DB_PATH = os.path.join(tmp3, "e.db")
db.ARTICLES_DIR = tmp3
db.init_db()
db.save_profile({"id": "1", "name": "小米风"})
db.save_profile({"id": "2", "name": "差评风"})
g1 = db.add_generation({"profileId": "1", "profileName": "小米风", "topic": "手机",
                        "model": "qwen-plus", "markdown": "# 一\n\n正文甲", "wordCount": 3})
g2 = db.add_generation({"profileId": "2", "profileName": "差评风", "topic": "手机",
                        "model": "deepseek", "markdown": "# 二\n\n正文乙", "wordCount": 3})
g3 = db.add_generation({"profileId": "1", "profileName": "小米风", "topic": "汽车",
                        "model": "qwen-plus", "markdown": "# 三\n\n正文丙", "wordCount": 3})
pair = db.eval_next(2)
check("抽到两篇", len(pair), 2)
ok("抽到的同主题", len({x["topic"] for x in pair}) == 1)
for f in ("profile_name", "profile_id", "model"):
    ok("不下发来源字段 %s（否则不是盲评）" % f, all(f not in x for x in pair))
ok("带正文供评委阅读", all(x.get("markdown") for x in pair))
check("指定主题抽题", len(db.eval_next(2, topic="汽车")), 1)
check("不足两篇时如实返回", len(db.eval_next(2, topic="不存在的主题")), 0)

print("== 盲评记录与联表导出 ==")
db.add_evaluation({"generationId": g1, "judge": "评审A", "scoreStyle": 5,
                   "scoreRead": 4, "scoreFact": 5, "preferred": True})
db.add_evaluation({"generationId": g1, "judge": "评审B", "scoreStyle": 3,
                   "scoreRead": 4, "scoreFact": 3, "preferred": False})
db.add_evaluation({"generationId": g2, "judge": "评审A", "scoreStyle": 4,
                   "scoreRead": 5, "scoreFact": 4, "preferred": False})
# 三篇指标一起塞：若联表把行数放大，judge_count 会变成 3 的倍数而不是 2
for gid, v in ((g1, 7.0), (g2, 2.0)):
    db.add_metrics(gid, [{"name": "aiFlavor", "value": v, "target": 0},
                         {"name": "sentLenCv", "value": 0.5, "target": 0}])
rows = db.eval_export()
by_id = {r["id"]: r for r in rows}
check("导出覆盖全部生成稿", len(rows), 3)
check("评委数没被指标行数放大", by_id[g1]["judge_count"], 2)
check("风格分取均值", by_id[g1]["avg_style"], 4.0)
check("被偏好次数", by_id[g1]["preferred_count"], 1)
check("客观指标行转列", by_id[g1]["ai_flavor"], 7.0)
check("没被评的稿评委数为 0", by_id[g3]["judge_count"], 0)
check("没指标的稿留空", by_id[g3]["ai_flavor"], None)
check("汇总接口可用", len(db.eval_summary()), 2)
# 删稿必须连带清掉指标与盲评，否则留下孤儿评分（evaluation 是 SET NULL 不是 CASCADE）
db.delete_generation(g1)
check("删稿后指标清空", len(db.list_metrics(g1)), 0)
ok("删稿后不留孤儿评分", all(e["generation_id"] is not None for e in db.list_evaluations()))
check("删稿后汇总只剩另一篇", len(db.eval_summary()), 1)

print("== 统计 ==")
st = db.stats()
ok("统计含 11 张表", len(st) == 11)

for d in (tmp, tmp2, tmp3):
    shutil.rmtree(d, ignore_errors=True)

print("== 结果 ==")
print("PASS %d, FAIL %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
