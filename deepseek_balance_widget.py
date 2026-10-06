# -*- coding: utf-8 -*-
"""
DeepSeek 余额实时悬浮窗（Windows 桌面小部件）
=============================================

无边框、置顶、可拖动的小窗口，定时调用 DeepSeek 余额接口并显示余额。

用法：
    python deepseek_balance_widget.py                 # 读取 config.json
    python deepseek_balance_widget.py --key sk-xxxx   # 临时指定 API Key
    python deepseek_balance_widget.py --interval 30   # 30 秒刷新一次
    python deepseek_balance_widget.py --demo          # 离线演示数据

接口：GET https://api.deepseek.com/user/balance（Authorization: Bearer <key>）
"""

import argparse
import ctypes
import json
import math
import os
import random
import socket
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import font as tkfont
from tkinter import simpledialog

import grokbot_mascot_data as bot_data

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(APP_DIR, "config.json")
DEFAULT_API = "https://api.deepseek.com/user/balance"

# ---- 外观配色（深色主题） ----
BG = "#111827"
BG_HOVER = "#1b2436"
FG = "#f3f4f6"
MUTED = "#94a3b8"
OK = "#34d399"
ERR = "#f87171"
ACCENT = "#4d6bfe"

FONT_FAMILY = "Microsoft YaHei UI"
F_TITLE = (FONT_FAMILY, 8)
F_BALANCE = (FONT_FAMILY, 16, "bold")
F_FOOTER = (FONT_FAMILY, 7)
F_STATS = (FONT_FAMILY, 9, "bold")

# ---- 吉祥物（它同时就是刷新键） ----
# 造型、表情和动作节奏全部取自 Grok Bot 公开前端的几何数据，
# 由 tools/build_mascot_data.py 抽取到同目录的 grokbot_mascot_data.py。
TITLE_TEXT = "DeepSeek 余额"
BALL_W = 68             # 吉祥物画布宽（窗口尺寸不变，球就占这块地方）
BALL_FILL = "#e9edf9"   # 官方是黑身白瞳，这里为了在深色卡片上看得清做了反色
BALL_EDGE = "#b9c2dd"
BALL_INK = "#141b2e"
BALL_ERR_FILL = "#f7dfe2"
BALL_ERR_EDGE = "#e2a9af"
# 只用"不同程度的圆"：正圆、扁圆、半圆。
# 官方那 18 种里的 bean（豆形）/teardrop（水滴）/gem（宝石）/crystal/wedge… 一律不用，
# egg（细长蛋形）按你的要求也去掉了。想把哪个加回来，直接写进这个元组即可。
BALL_MORPH_SHAPES = ("blob", "pebble", "dome")
BALL_FRAME_MS = 33

# ---- 按天统计 ----
# 只需要知道"今天 0 点时的余额"，所以采样点留着最近这一天多的就够。
HISTORY_LIMIT = 288          # 最多留 288 个采样点（按 5 分钟一档刚好覆盖 24 小时）
HISTORY_HEARTBEAT = 300.0    # 余额没变也至少每 5 分钟记一笔，跨零点时才不会漏掉起点
SPARK_FILL = "#1a2340"       # 曲线下方的填充色（强调色压暗后的效果）


def clamp_value(value, low, high):
    return low if value < low else (high if value > high else value)


def lerp_ring(first, second, amount):
    """两个等长点环之间逐点插值——官方就是用这个做形状和表情过渡的。"""
    if amount <= 0.0:
        return first
    if amount >= 1.0:
        return second
    return [
        (a[0] + (b[0] - a[0]) * amount, a[1] + (b[1] - a[1]) * amount)
        for a, b in zip(first, second)
    ]


def ring_centroid(ring):
    count = len(ring)
    return (sum(p[0] for p in ring) / count, sum(p[1] for p in ring) / count)


def span_at(ring, y):
    """身体轮廓在高度 y 上的左右边界（扫描线求交），用来把眼睛裁在身体里面。"""
    hits = []
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 <= y < y2) or (y2 <= y < y1):
            hits.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    if not hits:
        return None
    return (min(hits), max(hits))

CURRENCY_SYMBOLS = {
    "CNY": "¥",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "HKD": "HK$",
    "SGD": "S$",
}


def enable_dpi_awareness():
    """让高分屏下文字清晰（Win10+）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class BalanceWidget:
    def __init__(self, root, cfg, cfg_path, args):
        self.root = root
        self.cfg = cfg
        self.cfg_path = cfg_path
        # 走势采样点单独存一个文件，免得把 config.json 撑成一堆数字
        self.history_path = os.path.splitext(cfg_path)[0] + ".history.json" if cfg_path else None
        self.demo = args.demo
        self.api_key = (args.key or cfg.get("api_key") or "").strip()
        self.api_url = args.url or cfg.get("api_url") or DEFAULT_API
        try:
            self.interval = max(5, int(args.interval or cfg.get("refresh_interval_seconds") or 60))
        except (TypeError, ValueError):
            self.interval = 60
        self.topmost = bool(cfg.get("topmost", True))

        self.data = None          # 最近一次成功的余额数据
        self.error = None         # 当前错误信息
        self.history = self._load_history()   # [(时间戳, 余额), ...]
        self._fetching = False
        self._drag = None
        self._labels = []
        self._frames = []
        self._status_id = None    # 标题行临时提示的定时器
        self._autostart_busy = False

        # 吉祥物动画状态
        self._frame = 0
        self._anim_id = None
        self._face = "idle"                    # idle / think / happy / error
        self._face_timer = None
        self._shape_a = "blob"
        self._shape_b = "blob"
        self._morph_p = 1.0                    # 身体形变进度 0→1
        self._next_morph_at = time.time() + 3.0
        self._expr_from = 0
        self._expr_to = 0
        self._expr_p = 1.0                     # 表情过渡进度 0→1
        self._expr_next_at = 0.0
        self._eye_open = 1.0                   # 1 睁开、0 闭合
        self._blink_start = 0.0
        self._blink_next_at = 0.0
        self._drift = (0.0, 0.0)               # 眼珠的轻微游移 / 跟随鼠标
        self._hover_ball = False
        self._cursor = None
        self._ball_down = None
        self._ball_dragged = False

        self._pick_expression()
        self._schedule_blink()

        self._build()
        self._restore_position()
        self.root.after(200, self._round_corners)
        self.root.after(500, self._boot)
        self.root.after(60, self._animate)

    # ---------------- UI ----------------
    def _build(self):
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", self.topmost)

        outer = tk.Frame(self.root, bg=BG, padx=10, pady=8)
        outer.pack(fill="both", expand=True)
        self.outer = outer

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True)

        title_row = tk.Frame(body, bg=BG)
        title_row.pack(fill="x")
        self.dot = tk.Label(title_row, text="●", bg=BG, fg=MUTED, font=F_TITLE)
        self.dot.pack(side="left")
        self.title_lbl = tk.Label(title_row, text=TITLE_TEXT, bg=BG, fg=MUTED, font=F_TITLE, anchor="w")
        self.title_lbl.pack(side="left", padx=(6, 0))
        self._title_font = tkfont.Font(font=F_TITLE)

        self.balance_lbl = tk.Label(body, text="--", bg=BG, fg=FG, font=F_BALANCE, anchor="w")
        self.balance_lbl.pack(fill="x", pady=(2, 0))

        # 底部空行：不再显示任何文字，只负责把窗口撑在原来的尺寸上
        self.footer_row = tk.Frame(body, bg=BG, width=1, height=1)
        self.footer_row.pack(fill="x", pady=(1, 0))
        self.footer_row.pack_propagate(False)
        self.footer_lbl = tk.Label(self.footer_row, text="", bg=BG, fg=MUTED, font=F_FOOTER, anchor="w")
        self.footer_lbl.pack(side="left")
        self.time_lbl = tk.Label(self.footer_row, text="", bg=BG, fg=MUTED, font=F_FOOTER, anchor="e")
        self.time_lbl.pack(side="right", padx=(8, 0))
        self._reserve_original_size()

        # 「今日已用」画在原来空着的那一行上。注意右边有一块被吉祥物的画布盖住，
        # 所以只能用球左边那一段，宽度要减掉球的位置。
        stats_width = max(60, self.footer_row.winfo_reqwidth() - BALL_W - 6)
        self.stats = tk.Canvas(self.footer_row, width=stats_width, height=24,
                               bg=BG, highlightthickness=0, bd=0)
        self.stats.place(x=0, y=0, width=stats_width, relheight=1.0)

        # 吉祥物：占满右侧整块高度，点它就是刷新
        self.canvas = tk.Canvas(
            body, width=BALL_W, height=1, bg=BG,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self.canvas.place(in_=body, relx=1.0, relheight=1.0, x=-2, y=0, anchor="ne")

        self._labels = [self.dot, self.title_lbl, self.balance_lbl, self.footer_lbl, self.time_lbl]
        self._frames = [outer, body, title_row, self.footer_row, self.canvas, self.stats]
        for w in [outer, body, title_row, self.footer_row, self.dot, self.balance_lbl,
                  self.footer_lbl, self.time_lbl, self.stats]:
            w.bind("<Button-1>", self._press)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_release)
            w.bind("<Button-3>", self._show_menu)
            w.bind("<Enter>", lambda e, o=outer: self._hover(o, True))
            w.bind("<Leave>", lambda e, o=outer: self._hover(o, False))

        self.canvas.bind("<Button-1>", self._ball_press)
        self.canvas.bind("<B1-Motion>", self._ball_move)
        self.canvas.bind("<ButtonRelease-1>", self._ball_release)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.canvas.bind("<Motion>", self._ball_cursor)
        self.canvas.bind("<Enter>", self._ball_enter)
        self.canvas.bind("<Leave>", self._ball_leave)

    def _reserve_original_size(self):
        """脚注行不显示文字了，但它的宽度还得留着——卡片宽度一直是这行文字撑出来的。

        高度定在 24px，正好给走势曲线当画布（卡片总高因此回到 103px）。
        """
        interval_txt = "演示模式" if self.demo else f"每 {self.interval} 秒"
        self.footer_lbl.configure(text=f"{interval_txt}自动刷新 · 最近更新")
        self.time_lbl.configure(text="00:00:00")
        try:
            self.root.update_idletasks()
            w = self.footer_lbl.winfo_reqwidth() + self.time_lbl.winfo_reqwidth() + 8
            self.footer_row.configure(width=max(w, 10), height=24)
        except tk.TclError:
            pass
        finally:
            self.footer_lbl.configure(text="")
            self.time_lbl.configure(text="")

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="立即刷新", command=self.refresh)
        self.menu.add_command(label=f"每 {self.interval} 秒自动刷新", state="disabled")
        self.menu.add_command(label="设置 API Key…", command=self.set_api_key)
        self.menu.add_command(label="复制余额", command=self.copy_balance)
        self.menu.add_separator()
        self.topmost_var = tk.BooleanVar(value=self.topmost)
        self.menu.add_checkbutton(label="窗口置顶", variable=self.topmost_var, command=self.toggle_topmost)
        self.autostart_var = tk.BooleanVar(value=self._autostart_enabled())
        self.menu.add_checkbutton(label="开机自动启动", variable=self.autostart_var, command=self.toggle_autostart)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)

    def _round_corners(self):
        """Windows 11 圆角窗口（失败则忽略）。"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if hwnd:
                DWMWA_WINDOW_CORNER_PREFERENCE = 33
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                    ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int),
                )
        except Exception:
            pass

    def _hover(self, outer, on):
        bg = BG_HOVER if on else BG
        for f in self._frames:
            f.configure(bg=bg)
        for lbl in self._labels:
            lbl.configure(bg=bg)
        self._draw_stats()

    # ---------------- 按天统计 ----------------
    def _load_history(self):
        """读回采样点；旧版本存在 config 里的会自动搬到新文件，坏数据跳过。"""
        raw = None
        if self.history_path and os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = None
        if raw is None and isinstance(self.cfg.get("balance_history"), list):
            raw = self.cfg.pop("balance_history")
            self._save_cfg()
        history = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    try:
                        history.append((float(item[0]), float(item[1])))
                    except (TypeError, ValueError):
                        continue
        history.sort(key=lambda point: point[0])
        return history[-HISTORY_LIMIT:]

    def _save_history(self):
        if not self.history_path:
            return
        try:
            with open(self.history_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    [[int(moment), round(amount, 4)] for moment, amount in self.history],
                    handle, ensure_ascii=False, separators=(",", ":"),
                )
        except OSError:
            pass

    def _record_sample(self, value):
        """记一笔余额：变了就记，没变也每 5 分钟记一次，保证时间轴连续。

        最开始几次每次都记，这样曲线几分钟内就能有形状，不用干等。
        """
        now = time.time()
        if self.history and len(self.history) >= 6:
            last_time, last_value = self.history[-1]
            if abs(last_value - value) < 1e-9 and now - last_time < HISTORY_HEARTBEAT:
                return
        self.history.append((now, value))
        if len(self.history) > HISTORY_LIMIT:
            del self.history[:len(self.history) - HISTORY_LIMIT]
        self._save_history()

    def _today_used(self):
        """今天净用了多少钱 = 今天 0 点时的余额 − 现在的余额。"""
        if not self.data:
            return None
        try:
            current = float(self.data.get("total_balance"))
        except (TypeError, ValueError):
            return None
        clock = time.localtime()
        midnight = time.mktime((clock.tm_year, clock.tm_mon, clock.tm_mday, 0, 0, 0, 0, 0, -1))
        # 起点取"今天 0 点之前最后一次记到的余额"；如果那笔太旧（电脑关了不止一天），
        # 就退而用今天第一条记录，免得把好几天的支出都算到今天头上。
        earlier = [sample for sample in self.history if sample[0] < midnight]
        if earlier and midnight - earlier[-1][0] <= 86400:
            base = earlier[-1][1]
        else:
            today = [sample for sample in self.history if sample[0] >= midnight]
            base = today[0][1] if today else current
        return base - current

    def _draw_stats(self):
        canvas = self.stats
        canvas.delete("all")
        width = canvas.winfo_width() or 199
        height = canvas.winfo_height() or 24

        # 左边画余额趋势（最近这一天多的采样），右边写今天的累计变化
        block = 48.0                 # 右侧"今日已用 + 金额"占的宽度
        right = width - block
        if len(self.history) >= 2:
            values = [amount for _, amount in self.history]
            low, high = min(values), max(values)
            margin = (high - low) * 0.15 or max(abs(high) * 0.01, 0.01)
            floor, ceiling = low - margin, high + margin
            first_moment = self.history[0][0]
            span = (self.history[-1][0] - first_moment) or 1.0
            points = []
            for moment, amount in self.history:
                x = 1.0 + (moment - first_moment) / span * (right - 6.0)
                y = height - 3.0 - (amount - floor) / (ceiling - floor) * (height - 6.0)
                points.extend((x, y))
            canvas.create_polygon(points + [right - 5.0, height, 1.0, height],
                                  fill=SPARK_FILL, outline="")
            canvas.create_line(points, fill=ACCENT, width=1.6)
            canvas.create_oval(points[-2] - 2.0, points[-1] - 2.0,
                               points[-2] + 2.0, points[-1] + 2.0, fill=ACCENT, outline="")

        used = self._today_used()
        if used is None:
            canvas.create_text(width - 2, 7, anchor="e", fill=MUTED, font=F_FOOTER, text="今日已用")
            canvas.create_text(width - 2, 17, anchor="e", fill=MUTED, font=F_STATS, text="--")
            return

        currency = (self.data or {}).get("currency") or "CNY"
        symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
        if used > 0.005:
            label, color, amount = "今日已用", ERR, "%s%.2f" % (symbol, used)
        elif used < -0.005:
            label, color, amount = "今日充值", OK, "+%s%.2f" % (symbol, -used)
        else:
            label, color, amount = "今日已用", MUTED, "%s0.00" % symbol
        canvas.create_text(width - 2, 7, anchor="e", fill=MUTED, font=F_FOOTER, text=label)
        canvas.create_text(width - 2, 17, anchor="e", fill=color, font=F_STATS, text=amount)

    # ---------------- 吉祥物（它就是刷新键） ----------------
    def _ball_enter(self, _e):
        self._hover_ball = True
        self._hover(self.outer, True)

    def _ball_leave(self, _e):
        self._hover_ball = False
        self._cursor = None
        self._hover(self.outer, False)

    def _ball_cursor(self, e):
        self._cursor = (e.x, e.y)

    def _ball_press(self, e):
        self._ball_down = (e.x_root, e.y_root)
        self._ball_dragged = False
        self._press(e)

    def _ball_move(self, e):
        # 按下后挪动超过 6px 才算拖窗口：阈值太小的话，手抖一下就把窗口拖歪了
        if self._ball_down and not self._ball_dragged:
            moved = abs(e.x_root - self._ball_down[0]) + abs(e.y_root - self._ball_down[1])
            if moved > 6:
                self._ball_dragged = True
        if self._ball_dragged:
            self._drag_move(e)

    def _ball_release(self, e):
        dragged = self._ball_dragged
        self._ball_down = None
        self._ball_dragged = False
        if dragged:
            self._drag_release(e)
        else:
            self.refresh()

    def _set_face(self, face, ms=0):
        """切换状态；ms 为 0 表示一直保持到下一次切换。"""
        if self._face_timer:
            self.root.after_cancel(self._face_timer)
            self._face_timer = None
        if face != self._face:
            self._face = face
            self._pick_expression()
            self._schedule_blink()
        if ms:
            self._face_timer = self.root.after(ms, self._face_restore)

    def _face_restore(self):
        self._face_timer = None
        self._set_face("idle")

    def _state_cadence(self):
        return bot_data.STATES.get(self._face, bot_data.STATES["idle"])

    def _pick_expression(self):
        """从当前状态的表情池里换一套眼睛，节奏用官方的表。"""
        cadence = self._state_cadence()
        pool = cadence["pool"]
        choices = [i for i in pool if i != self._expr_to] or list(pool)
        self._expr_from = self._expr_to
        self._expr_to = random.choice(choices)
        self._expr_p = 0.0
        low, high = cadence["expr"]
        self._expr_next_at = time.time() + random.uniform(low, high) / 1000.0

    def _schedule_blink(self):
        low, high = self._state_cadence()["blink"]
        self._blink_next_at = time.time() + random.uniform(low, high) / 1000.0

    def _animate(self):
        self._frame += 1
        try:
            self._draw_mascot()
        except tk.TclError:
            return
        self._anim_id = self.root.after(BALL_FRAME_MS, self._animate)

    def _draw_mascot(self):
        c = self.canvas
        c.delete("all")
        now = time.time()

        # ---- 推进动画状态 ----
        if self._expr_p < 1.0:
            self._expr_p = min(1.0, self._expr_p + 0.055)
        elif now >= self._expr_next_at:
            self._pick_expression()

        if self._blink_start <= 0.0 and now >= self._blink_next_at:
            self._blink_start = now
        if self._blink_start > 0.0:
            progress = (now - self._blink_start) / 0.15
            if progress >= 1.0:
                self._blink_start = 0.0
                self._eye_open = 1.0
                self._schedule_blink()
            else:
                self._eye_open = 1.0 - 0.94 * math.sin(progress * math.pi)

        # 每隔几秒换一种身体形状（官方 18 种里的 8 种），平滑过渡
        if self._morph_p < 1.0:
            self._morph_p = min(1.0, self._morph_p + 0.02)
        elif now >= self._next_morph_at:
            self._shape_a = self._shape_b
            candidates = [s for s in BALL_MORPH_SHAPES if s != self._shape_a]
            # 半圆偶尔来一次就够，每次都变半圆会太抢戏
            if "dome" in candidates and len(candidates) > 1 and random.random() > 0.6:
                candidates.remove("dome")
            self._shape_b = random.choice(candidates)
            self._morph_p = 0.0
            self._next_morph_at = now + random.uniform(4.0, 7.0)

        # 眼珠的游移：鼠标在球上时跟着光标，平时自己慢慢飘
        width = c.winfo_width() or BALL_W
        height = c.winfo_height() or BALL_W
        if self._hover_ball and self._cursor:
            target_x = 22.0 * clamp_value((self._cursor[0] - width / 2.0) / width, -0.6, 0.6)
            target_y = 14.0 * clamp_value((self._cursor[1] - height / 2.0) / height, -0.6, 0.6)
        else:
            target_x = 1.4 * math.sin(now * 0.42) + 0.5 * math.sin(now * 1.0 + 2.0)
            target_y = 0.9 * math.sin(now * 0.58)
        self._drift = (
            self._drift[0] + (target_x - self._drift[0]) * 0.25,
            self._drift[1] + (target_y - self._drift[1]) * 0.25,
        )

        # ---- 几何：把官方 228.5 见方的坐标系映射到画布 ----
        shape_a = bot_data.SHAPES[self._shape_a]
        shape_b = bot_data.SHAPES[self._shape_b]
        morph = self._morph_p
        body_ring = lerp_ring(shape_a["ring"], shape_b["ring"], morph)
        face_a = shape_a["face"]
        face_b = shape_b["face"]
        face = tuple(face_a[i] + (face_b[i] - face_a[i]) * morph for i in range(5))
        top = shape_a["top"] + (shape_b["top"] - shape_a["top"]) * morph
        bottom = shape_a["bottom"] + (shape_b["bottom"] - shape_a["bottom"]) * morph

        scale = min(width, height) * 0.86 / 228.5
        cx, cy = width / 2.0, height / 2.0
        breath = 1.0 + 0.008 * math.sin(now * 2.1)

        def to_screen(px, py):
            return (cx + (px - bot_data.HEAD_C) * scale * breath,
                    cy + (py - bot_data.HEAD_C) * scale * breath)

        fill, edge = BALL_FILL, BALL_EDGE
        if self._face == "confused":
            fill, edge = BALL_ERR_FILL, BALL_ERR_EDGE

        body_points = []
        for px, py in body_ring:
            sx, sy = to_screen(px, py)
            body_points.append(sx)
            body_points.append(sy)
        c.create_polygon(body_points, fill=fill, outline=edge, width=1.2)

        # ---- 眼睛：官方数据里两只眼不在同一坐标系中心，先按中点对齐再整体上移 ----
        eye_rings = (
            lerp_ring(bot_data.EXPRESSIONS[self._expr_from][0],
                      bot_data.EXPRESSIONS[self._expr_to][0], self._expr_p),
            lerp_ring(bot_data.EXPRESSIONS[self._expr_from][1],
                      bot_data.EXPRESSIONS[self._expr_to][1], self._expr_p),
        )
        centers = [ring_centroid(r) for r in eye_rings]
        mid_x = (centers[0][0] + centers[1][0]) / 2.0
        mid_y = (centers[0][1] + centers[1][1]) / 2.0
        left_half = max(abs(p[0] - centers[0][0]) for p in eye_rings[0])
        right_half = max(abs(p[0] - centers[1][0]) for p in eye_rings[1])
        distance = abs(centers[1][0] - centers[0][0]) * face[2]
        halves = left_half + right_half
        fit = clamp_value((distance - 5.0) / halves, 0.35, 4.0) if halves > 0.5 else 4.0
        pulse = 1.0 + 0.07 * math.sin(self._expr_p * math.pi)
        eye_scale = min(face[4], fit / pulse)
        scale_x = eye_scale * pulse
        scale_y = max(self._eye_open, 0.06) * eye_scale * pulse

        for index in (0, 1):
            ring = eye_rings[index]
            center_x, center_y = centers[index]
            desired = bot_data.HEAD_C + face[0] + (center_x - mid_x) + self._drift[0] * face[2]
            half_height = bot_data.EYE_HALF * scale_y + 2.0
            target_y = bot_data.HEAD_C + face[1] + (center_y - mid_y) + bot_data.EYE_Y_SHIFT \
                + self._drift[1] * face[3]
            target_y = clamp_value(target_y, top + half_height, bottom - half_height)

            # 把眼睛裁在身体轮廓里面（官方 renderEyes 的做法）
            max_left = -1e9
            min_right = 1e9
            for point in ring[::2]:
                offset_x = (point[0] - center_x) * scale_x
                span = span_at(body_ring, target_y + (point[1] - center_y) * scale_y)
                if span:
                    max_left = max(max_left, span[0] - offset_x)
                    min_right = min(min_right, span[1] - offset_x)
            if max_left <= min_right:
                final_x = clamp_value(desired, max_left, min_right)
            else:
                final_x = (max_left + min_right) / 2.0

            eye_points = []
            for px, py in ring:
                sx, sy = to_screen(final_x + (px - center_x) * scale_x,
                                   target_y + (py - center_y) * scale_y)
                eye_points.append(sx)
                eye_points.append(sy)
            c.create_polygon(eye_points, fill=BALL_INK)

        # 请求中：绕球转一圈的弧
        if self._face == "thinking":
            radius = scale * 228.5 / 2.0 + 3.0
            spin = (self._frame * 7) % 360
            c.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                         start=spin, extent=90, style="arc", outline=ACCENT, width=2.5)

    # ---------------- 拖动与位置 ----------------
    def _press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_release(self, _e):
        if self._drag:
            self._drag = None
            self._save_position()

    def _restore_position(self):
        pos = self.cfg.get("window_position")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            self.root.geometry(f"+{int(pos[0])}+{int(pos[1])}")
        else:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            self.root.geometry(f"+{sw - self.root.winfo_reqwidth() - 40}+{40}")

    def _save_position(self):
        self.cfg["window_position"] = [self.root.winfo_x(), self.root.winfo_y()]
        self._save_cfg()

    def _save_cfg(self):
        if not self.cfg_path:
            return
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------------- 菜单动作 ----------------
    def _show_menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    def set_api_key(self):
        self.root.attributes("-topmost", False)
        try:
            val = simpledialog.askstring(
                "DeepSeek API Key",
                "请输入 DeepSeek API Key（sk-…）：",
                initialvalue=self.api_key,
                parent=self.root,
            )
        finally:
            self.root.attributes("-topmost", self.topmost)
            self.root.lift()
        if not val or not val.strip():
            return
        self.api_key = val.strip()
        self.cfg["api_key"] = self.api_key
        self._save_cfg()
        self.refresh()

    def toggle_topmost(self):
        self.topmost = self.topmost_var.get()
        self.root.attributes("-topmost", self.topmost)
        self.cfg["topmost"] = self.topmost
        self._save_cfg()

    def toggle_autostart(self):
        if self._autostart_busy:
            return
        self._autostart_busy = True
        enable = self.autostart_var.get()
        self._flash_status("正在设置自启…", MUTED)
        threading.Thread(target=self._autostart_worker, args=(enable,), daemon=True).start()

    def _autostart_worker(self, enable):
        script = "install_autostart.ps1" if enable else "remove_autostart.ps1"
        ok, msg = self._run_autostart_script(script)
        enabled = self._autostart_enabled()
        self.root.after(0, self._on_autostart_done, enable, ok, msg, enabled)

    def _on_autostart_done(self, enable, ok, msg, enabled):
        self._autostart_busy = False
        self.autostart_var.set(enabled)
        if ok and enabled == enable:
            if enable:
                self._flash_status("已开启自启", OK)
            else:
                self._flash_status("已关闭自启", MUTED)
        else:
            self._flash_status(f"设置失败：{msg or '未知错误'}", ERR)

    def _run_autostart_script(self, name):
        script = os.path.join(APP_DIR, name)
        if not os.path.exists(script):
            return False, f"缺少脚本 {name}"
        try:
            flags = 0x08000000  # CREATE_NO_WINDOW
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
            )
            _, err = proc.communicate(timeout=30)
            return proc.returncode == 0, err.decode("utf-8", "replace").strip()
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    @staticmethod
    def _autostart_enabled():
        startup = os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        )
        return os.path.exists(os.path.join(startup, "DeepSeek-Balance-Widget.lnk"))

    def _set_status(self, text, color):
        """标题行的常驻文字；正在显示临时提示时先不抢占。"""
        if self._status_id:
            return
        self.title_lbl.configure(text=self._fit_text(text), fg=color)

    def _flash_status(self, text, color, ms=1600):
        """临时提示借用标题行的位置，不额外占用卡片空间。"""
        if self._status_id:
            self.root.after_cancel(self._status_id)
            self._status_id = None
        self.title_lbl.configure(text=self._fit_text(text), fg=color)
        self._status_id = self.root.after(ms, self._clear_status)

    def _fit_text(self, text, max_px=106):
        """按实际像素宽度裁掉超长的提示，避免压到吉祥物身上。"""
        text = str(text)
        if self._title_font.measure(text) <= max_px:
            return text
        while text and self._title_font.measure(text + "…") > max_px:
            text = text[:-1]
        return text + "…"

    def _clear_status(self):
        self._status_id = None
        self._render()

    def copy_balance(self):
        if not self.data:
            return
        d = self.data
        cur = d.get("currency") or "CNY"
        text = f"DeepSeek 余额 {self._money(d.get('total_balance'), cur)}"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._flash_status("已复制到剪贴板", ACCENT)

    def quit(self):
        if self._anim_id:
            self.root.after_cancel(self._anim_id)
            self._anim_id = None
        if self._face_timer:
            self.root.after_cancel(self._face_timer)
            self._face_timer = None
        if self._status_id:
            self.root.after_cancel(self._status_id)
            self._status_id = None
        self._save_position()
        self.root.destroy()

    # ---------------- 数据刷新 ----------------
    def _boot(self):
        if not self.demo and not self.api_key:
            self.root.attributes("-topmost", False)
            try:
                val = simpledialog.askstring(
                    "DeepSeek 余额悬浮窗",
                    "未检测到 API Key。\n\n请输入 DeepSeek API Key（sk-…）：",
                    parent=self.root,
                )
            finally:
                self.root.attributes("-topmost", self.topmost)
                self.root.lift()
            if val and val.strip():
                self.api_key = val.strip()
                self.cfg["api_key"] = self.api_key
                self._save_cfg()
        self.refresh()

    def refresh(self):
        if self._fetching:
            return
        if not self.demo and not self.api_key:
            self.error = "未设置 API Key"
            self._render()
            self._set_face("confused", 1500)
            self._schedule()
            return
        self._fetching = True
        self._set_face("thinking")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            data = self._fetch()
            self.root.after(0, self._on_success, data)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._on_error, self._friendly_error(exc))

    def _fetch(self):
        if self.demo:
            time.sleep(0.4)
            return {
                "is_available": True,
                "balance_infos": [{
                    "currency": "CNY",
                    "total_balance": "88.88",
                    "granted_balance": "8.00",
                    "topped_up_balance": "80.88",
                }],
            }
        req = urllib.request.Request(
            self.api_url,
            headers={"Authorization": "Bearer " + self.api_key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _on_success(self, data):
        self._fetching = False
        infos = data.get("balance_infos") or []
        if not data.get("is_available") or not infos:
            self.error = "账户不可用或接口返回异常"
            self.data = None
            self._set_face("confused", 1500)
        else:
            self.error = None
            self.data = infos[0]
            self._set_face("celebrate", 900)
            try:
                self._record_sample(float(self.data.get("total_balance")))
            except (TypeError, ValueError):
                pass
        self._render()
        self._schedule()

    def _on_error(self, msg):
        self._fetching = False
        self.error = msg
        self._set_face("confused", 1500)
        self._render()
        self._schedule()

    def _friendly_error(self, exc):
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code == 401:
                return "API Key 无效（401）"
            if exc.code == 402:
                return "余额不足（402）"
            if exc.code == 429:
                return "请求过于频繁（429）"
            return f"接口错误（HTTP {exc.code}）"
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return "网络超时"
            return f"网络错误：{reason}"
        if isinstance(exc, (json.JSONDecodeError, ValueError)):
            return "接口返回数据异常"
        if isinstance(exc, OSError):
            return "网络连接失败"
        return str(exc).strip() or "未知错误"

    def _schedule(self):
        self.root.after(self.interval * 1000, self.refresh)

    # ---------------- 渲染 ----------------
    def _render(self):
        if self.error and self.data is None:
            self.dot.configure(fg=ERR)
            self.balance_lbl.configure(text="--", fg=ERR)
            self._set_status(self.error, ERR)
        elif self.data is not None:
            self._render_data()
            if self.error:
                self.dot.configure(fg=ERR)
                self._set_status(self.error, ERR)
            else:
                self.dot.configure(fg=OK)
                self._set_status(TITLE_TEXT, MUTED)
        else:
            self.dot.configure(fg=MUTED)
            self.balance_lbl.configure(text="--", fg=MUTED)
            self._set_status("右键设置 API Key", MUTED)
        self._draw_stats()

    def _render_data(self):
        d = self.data
        cur = d.get("currency") or "CNY"
        self.balance_lbl.configure(
            text=self._money(d.get("total_balance"), cur),
            fg=FG,
        )

    @staticmethod
    def _money(value, currency):
        if value in (None, ""):
            return "--"
        try:
            text = f"{float(value):,.2f}"
        except (TypeError, ValueError):
            text = str(value)
        sym = CURRENCY_SYMBOLS.get(currency)
        return f"{sym}{text}" if sym else f"{currency} {text}"

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="DeepSeek 余额实时悬浮窗")
    parser.add_argument("--key", help="临时指定 DeepSeek API Key")
    parser.add_argument("--url", help="余额接口地址（默认官方接口）")
    parser.add_argument("--interval", type=int, help="刷新间隔（秒，最小 5）")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--demo", action="store_true", help="使用离线演示数据")
    args = parser.parse_args()

    enable_dpi_awareness()
    cfg = load_config(args.config)
    root = tk.Tk()
    widget = BalanceWidget(root, cfg, args.config, args)
    widget.run()


if __name__ == "__main__":
    main()
