# -*- coding: utf-8 -*-
"""生成实践报告用的两张图：系统架构图、文章生成流程图。
输出到 D:\\下载\\report_figs\\，供 docx 插入使用。"""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

OUT = r'D:\下载\report_figs'
os.makedirs(OUT, exist_ok=True)

BLUE = '#2563EB'
DARK = '#0F172A'
GREY = '#64748B'
LIGHT = '#F1F5F9'
WHITE = '#FFFFFF'


def box(ax, x, y, w, h, text, fc=LIGHT, tc=DARK, fs=9, bold=False, ec=None, r=0.02):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f'round,pad=0.006,rounding_size={r}',
                                linewidth=1.0,
                                edgecolor=ec if ec else fc,
                                facecolor=fc, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha='center', va='center',
            fontsize=fs, color=tc, zorder=3,
            fontweight='bold' if bold else 'normal', linespacing=1.5)


def arrow(ax, x1, y1, x2, y2, style='->', color=GREY, lw=1.2, ls='-', rad=0.0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=11, linewidth=lw, color=color,
                                 linestyle=ls, zorder=1,
                                 connectionstyle=f'arc3,rad={rad}'))


# ---------------- 图1 系统总体架构 ----------------
fig, ax = plt.subplots(figsize=(6.6, 4.4), dpi=200)
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis('off')

box(ax, 0.34, 0.90, 0.32, 0.075, '用户（浏览器）', fc=DARK, tc=WHITE, fs=10, bold=True)

# 前端层
ax.add_patch(FancyBboxPatch((0.03, 0.60), 0.94, 0.24,
                            boxstyle='round,pad=0.006,rounding_size=0.02',
                            linewidth=1.2, edgecolor=BLUE, facecolor='#FFFFFF', zorder=1))
ax.text(0.05, 0.815, '前端层  单文件单页应用（原生 JS，无框架）', fontsize=9,
        color=BLUE, fontweight='bold', va='center')
mods = ['风格分析', '文章生成', '图片素材', '表情包库', '批量总结', '设置']
for i, m in enumerate(mods):
    box(ax, 0.055 + i * 0.152, 0.635, 0.135, 0.115, m, fc=LIGHT, fs=8.5)

# 服务层
ax.add_patch(FancyBboxPatch((0.03, 0.28), 0.94, 0.26,
                            boxstyle='round,pad=0.006,rounding_size=0.02',
                            linewidth=1.2, edgecolor=BLUE, facecolor='#FFFFFF', zorder=1))
ax.text(0.05, 0.505, '本地服务层  Python HTTP 服务（仅监听本机）', fontsize=9,
        color=BLUE, fontweight='bold', va='center')
svcs = ['静态资源\n托管', '文章抓取\n与图片落盘', '大模型\n请求转发', '图片\n反向代理', '素材与\n分组查询']
for i, s in enumerate(svcs):
    box(ax, 0.055 + i * 0.185, 0.305, 0.168, 0.13, s, fc='#E8F0FE', fs=8)

# 外部与存储
box(ax, 0.06, 0.09, 0.40, 0.115, '外部大模型 API\n（OpenAI 兼容：百炼 / 火山方舟 / DeepSeek）',
    fc='#FEF3C7', ec='#F59E0B', fs=8)
box(ax, 0.54, 0.09, 0.40, 0.115, '本机存储\narticles/ 文章图片   ·   浏览器本地风格档案与密钥',
    fc='#DCFCE7', ec='#22C55E', fs=8)

arrow(ax, 0.50, 0.895, 0.50, 0.845, color=BLUE, lw=1.4)
arrow(ax, 0.50, 0.595, 0.50, 0.545, color=BLUE, lw=1.4)
arrow(ax, 0.26, 0.275, 0.26, 0.208, color='#F59E0B', lw=1.3)
arrow(ax, 0.74, 0.275, 0.74, 0.208, color='#22C55E', lw=1.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT, 'fig1_arch.png'), bbox_inches='tight', facecolor='white')
plt.close()

# ---------------- 图2 文章生成与字数保证流程 ----------------
fig, ax = plt.subplots(figsize=(6.6, 4.6), dpi=200)
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis('off')

box(ax, 0.10, 0.90, 0.80, 0.072, '输入：风格档案参数 + 文章主题 + 必备要点 + 目标篇幅',
    fc=DARK, tc=WHITE, fs=8.5, bold=True)

box(ax, 0.10, 0.775, 0.80, 0.075,
    '首写：提示词约束\n（字数中值目标 · 风格参数 · 词汇与句式黑名单 · 句长节奏 · 事实边界）',
    fc=LIGHT, fs=8)

box(ax, 0.10, 0.655, 0.80, 0.062, '去 AI 味二次润色：删过渡词 · 拆对仗句式 · 打散均匀句长',
    fc='#E8F0FE', ec=BLUE, fs=8)

box(ax, 0.06, 0.545, 0.44, 0.062, '三重校验\n标记数量 / 字数 / 标题', fc='#FEF3C7', ec='#F59E0B', fs=8)

box(ax, 0.10, 0.415, 0.80, 0.062, '确定性汉字计数（排除标题与配图标记）',
    fc=LIGHT, fs=8)

box(ax, 0.06, 0.300, 0.44, 0.068, '低于篇幅下限？\n自动续写补齐一次', fc='#FEF3C7', ec='#F59E0B', fs=8)

box(ax, 0.06, 0.155, 0.88, 0.075, '输出 Markdown → 槽位配图（点击可更换）→ 导出带内联样式的 HTML',
    fc='#DCFCE7', ec='#22C55E', fs=8.5)

arrow(ax, 0.50, 0.895, 0.50, 0.855, color=BLUE, lw=1.3)
arrow(ax, 0.50, 0.770, 0.50, 0.722, color=BLUE, lw=1.3)
arrow(ax, 0.50, 0.650, 0.50, 0.612, color=BLUE, lw=1.3)
arrow(ax, 0.28, 0.540, 0.28, 0.482, color=GREY, lw=1.2)
arrow(ax, 0.50, 0.410, 0.50, 0.373, color=BLUE, lw=1.3)
arrow(ax, 0.28, 0.295, 0.28, 0.235, color=GREY, lw=1.2)
arrow(ax, 0.50, 0.150, 0.50, 0.122, color='#22C55E', lw=1.3)

# 回退与补齐回路
arrow(ax, 0.06, 0.576, 0.035, 0.576, color='#F59E0B', lw=1.1)
arrow(ax, 0.035, 0.576, 0.035, 0.812, color='#F59E0B', lw=1.1)
arrow(ax, 0.035, 0.812, 0.095, 0.812, color='#F59E0B', lw=1.1, style='->')
ax.text(0.043, 0.70, '校验未通过\n回退原稿', fontsize=7.5, color='#B45309',
        rotation=90, va='center', ha='left')

plt.tight_layout()
plt.savefig(os.path.join(OUT, 'fig2_flow.png'), bbox_inches='tight', facecolor='white')
plt.close()

for f in ('fig1_arch.png', 'fig2_flow.png'):
    p = os.path.join(OUT, f)
    print(f, os.path.getsize(p), 'bytes')
