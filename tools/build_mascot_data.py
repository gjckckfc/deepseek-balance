#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从公开的 Grok Bot 前端几何数据生成 tkinter 用的吉祥物数据模块。

数据来源：x.ai/bot 的公开前端几何，经 iduu/grokbot-animation 仓库的
component/original-data.js 提取（该文件由作者的 extract-original-data.mjs 生成）。

这里只搬运"数字"：身体轮廓环、25 套眼睛环、以及官方状态机的节奏表。
不涉及任何渲染代码，生成出来的模块可以被 tkinter 的 Canvas 直接消费。

用法：
    python tools/build_mascot_data.py                      # 自动联网取源文件
    python tools/build_mascot_data.py path/to/original-data.js
    python tools/build_mascot_data.py -o other_name.py
"""

import argparse
import json
import os
import re
import sys
import urllib.request

SOURCE_API = "https://api.github.com/repos/iduu/grokbot-animation/contents/component/original-data.js"

# 挑出来要用的身体形状（其余的形状数据不带进产物，控制体积）
KEEP_SHAPES = ("blob", "pebble", "bean", "egg", "squircle", "teardrop", "gem", "dome")

# 挑出来要用的状态（挂件只用到这几档）
KEEP_STATES = (
    "idle", "listening", "thinking", "working",
    "happy", "celebrate", "confused", "surprised",
)

# 采样步长：环上每隔 N 个点取一个。官方环有 96/48 个点，55px 的球上用不到这么密。
SHAPE_STRIDE = 2
EXPR_STRIDE = 2

# 官方那两份眼睛环不在同一个坐标系中心，绘制时先把两只眼按中点重新对齐到 HEAD_C，
# 再整体上移一点，看起来才像一张脸。
EYE_Y_SHIFT = -6.0


def load_source(path):
    """读源文件；没给路径就联网取。"""
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    req = urllib.request.Request(SOURCE_API, headers={"Accept": "application/vnd.github.raw"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def exported(src, name):
    """取出 `export const NAME = ...;` 的值并解析成 Python 对象。"""
    match = re.search(r"export const %s = (.*?);\n" % name, src, re.S)
    if not match:
        raise SystemExit("源文件里找不到导出 %s" % name)
    return json.loads(match.group(1))


def fmt(value):
    """坐标保留 1 位小数：官方数据是 0～228 区间，这个精度在 55px 上远超一个像素。"""
    text = "%.1f" % float(value)
    return text[:-2] if text.endswith(".0") else text


def ring_literal(ring, stride):
    points = ring[::stride]
    body = ", ".join("(%s, %s)" % (fmt(p[0]), fmt(p[1])) for p in points)
    return "(%s%s)" % (body, "," if len(points) == 1 else "")


def build_module(src):
    head_c = exported(src, "HEAD_C")
    eye_half = exported(src, "EYE_HALF")
    shapes = exported(src, "SHAPES")
    expressions = exported(src, "EXPRESSIONS")
    state_data = exported(src, "ORIGINAL_STATE_DATA")

    out = []
    out.append("# -*- coding: utf-8 -*-")
    out.append('"""Grok Bot 吉祥物几何数据（自动生成，请勿手改）。')
    out.append("")
    out.append("数据来源：x.ai/bot 公开前端几何，经 iduu/grokbot-animation 的")
    out.append("component/original-data.js 提取。环上坐标已按步长采样并保留 1 位小数。")
    out.append("")
    out.append("重新生成：python tools/build_mascot_data.py")
    out.append('"""')
    out.append("")
    out.append("# 官方坐标系的中心（身体与眼睛都以此为原点）")
    out.append("HEAD_C = %s" % fmt(head_c))
    out.append("EYE_HALF = %s" % fmt(eye_half))
    out.append("")
    out.append("# 两只眼睛按中点重新对齐到 HEAD_C 后再整体上移的像素数")
    out.append("EYE_Y_SHIFT = %s" % fmt(EYE_Y_SHIFT))
    out.append("")
    out.append("# 身体形状：face = (x, y, sx, sy, eye)，决定眼睛在该形状上的位置与缩放；")
    out.append("# ring 是闭合轮廓环，(x, y) 绝对坐标。")
    out.append("SHAPES = {")
    for name in KEEP_SHAPES:
        shape = shapes[name]
        face = shape["face"]
        out.append('    "%s": {' % name)
        out.append(
            '        "face": (%s, %s, %s, %s, %s),'
            % (fmt(face["x"]), fmt(face["y"]), fmt(face["sx"]), fmt(face["sy"]), fmt(face["eye"]))
        )
        out.append('        "top": %s,' % fmt(shape["top"]))
        out.append('        "bottom": %s,' % fmt(shape["bottom"]))
        out.append('        "ring": %s,' % ring_literal(shape["ring"], SHAPE_STRIDE))
        out.append("    },")
    out.append("}")
    out.append("")
    out.append("# 25 套眼睛表情：每项是 (左眼环, 右眼环)，环为绝对坐标的点列。")
    out.append("# 相邻表情之间可以逐点插值，所以两套环的点数必须一致。")
    out.append("EXPRESSIONS = (")
    for index, expression in enumerate(expressions):
        left = ring_literal(expression[0], EXPR_STRIDE)
        right = ring_literal(expression[1], EXPR_STRIDE)
        out.append("    (%s, %s),  # %d" % (left, right, index))
    out.append(")")
    out.append("")
    out.append("# 官方状态机节奏（毫秒）：")
    out.append("#   blink = 眨眼间隔范围，expr = 表情更换间隔范围，pool = 可用表情下标")
    out.append("STATES = {")
    for name in KEEP_STATES:
        blink = state_data["BLINK_CADENCE"].get(name) or (4000, 8000)
        cadence = state_data["EXPRESSION_CADENCE"].get(name) or (3000, 6000)
        pool = state_data["EXPRESSION_POOLS"][name]
        out.append(
            '    "%s": {"blink": (%d, %d), "expr": (%d, %d), "pool": (%s)},'
            % (
                name,
                int(blink[0]), int(blink[1]),
                int(cadence[0]), int(cadence[1]),
                ", ".join(str(int(i)) for i in pool),
            )
        )
    out.append("}")
    out.append("")
    return "\n".join(out)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(os.path.dirname(here), "grokbot_mascot_data.py")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", help="original-data.js 路径（缺省则联网获取）")
    parser.add_argument("-o", "--out", default=default_out, help="输出路径")
    args = parser.parse_args()

    src = load_source(args.source)
    module = build_module(src)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(module)
    print("已生成 %s（%.1f KB）" % (args.out, os.path.getsize(args.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
