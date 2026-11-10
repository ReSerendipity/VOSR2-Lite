#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""视觉像素 diff E2E（TD-014 未覆盖清单 (b) 的第三项）。

**这条用例在测什么**：浏览器**真的**把服务端产出的那张图按原生像素画了出来。
它比「元素尺寸对不对 / DOM 状态对不对」更靠里一层——尺寸对但画错图、或画的是
上一张的缓存、或 CSS 把图拉伸过，DOM 断言都看不出来，像素比对看得见。

**为什么没有跨机 golden 基线**：截图基线会把「字体渲染 / 图像缩放实现 / 显卡」
的跨平台差异固化成一条每跑必红的用例（本仓 E2E 还在 Linux runner 上跑）。
所以基线不落盘，改成**在同一次运行里由桩引擎的确定性输出算出来**：
桩引擎只做 BICUBIC ×4，同一个输入必然产出同一批像素，于是「期望图像」可以由
Python 侧独立复算/读回，比对判据与机器无关。

**自带的负控**：同一张截图再和「左右镜像的期望图」比一次，必须**判不匹配**。
否则说明容差宽到「随便一张图都能过」，这条用例就是空转。
"""

from __future__ import annotations

import io
import os

from _e2e_helpers import (
    click_start,
    make_pattern_png,
    output_files,
    select_files,
    settle_frames,
    wait_batch_completed,
)
from PIL import Image, ImageChops, ImageOps

INPUT_SIZE = (64, 48)
UPSCALE = 4
EXPECTED_SIZE = (INPUT_SIZE[0] * UPSCALE, INPUT_SIZE[1] * UPSCALE)

# 画布顶部一条被「原图 / 修复后」角标覆盖，角标是文字，属于 UI 而非图像像素，
# 比对时整条裁掉——像素 diff 只对「图像内容」负责，不对字体渲染负责。
CANVAS_TAG_BAND = 44

# 单像素容差：1:1 显示下缩放比例理论上恰好是 1，但 ``oneToOne()`` 的 mag 来自
# 未取整的显示宽度，可能引入零点几个像素的重采样；8 个色阶以内视为「同一像素」。
PER_PIXEL_TOLERANCE = 8
# 允许超差像素占比（边缘重采样留下的零星像素）。
MAX_BAD_RATIO = 0.01
MAX_MEAN_DIFF = 1.0
# 镜像负控必须至少这么多像素超差（远大于上面的容差上限，判据不可能是空的）。
MIN_MIRROR_BAD_RATIO = 0.15


def _stats(left: Image.Image, right: Image.Image) -> tuple[float, float, int]:
    """逐像素比：返回（超差占比, 平均最大通道差, 超差像素数）。

    用 ``tobytes()`` 而不是 ``getdata()``：后者在 Pillow 12 起被标记弃用
    （Pillow 14 移除），E2E 用例不该拖一条必然要改的弃用告警。
    """
    diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    raw = diff.tobytes()
    total = len(raw) // 3
    over = 0
    acc = 0
    for i in range(0, len(raw), 3):
        worst = max(raw[i], raw[i + 1], raw[i + 2])
        acc += worst
        if worst > PER_PIXEL_TOLERANCE:
            over += 1
    return over / total, acc / total, over


def _crop_both(a: Image.Image, b: Image.Image) -> tuple[Image.Image, Image.Image]:
    """裁到「同一块可比区域」：去掉顶部角标带，取两图公共尺寸。"""
    width = min(a.width, b.width)
    height = min(a.height, b.height)
    top = CANVAS_TAG_BAND if min(a.height, b.height) > CANVAS_TAG_BAND + 20 else 0
    box = (0, top, width, height)
    return a.crop(box), b.crop(box)


def test_viewer_pixels_match_stub_output(page, e2e_server, tmp_path) -> None:
    base = e2e_server.base_url
    name = "px_patch.png"
    source = make_pattern_png(tmp_path / name, INPUT_SIZE)
    outputs_before = output_files(e2e_server.outputs)

    select_files(page, base, [source])
    click_start(page)
    wait_batch_completed(page)
    page.wait_for_function(
        "() => document.getElementById('viewer').classList.contains('is-ready')",
        timeout=20_000,
    )
    # 必须等「原图已升级完成」再截图：viewer 先挂预览（``/preview``，无 alpha 时是
    # **JPEG q88** 有损编码），随后才把 src 换成 ``/download`` 的原文件。
    # 若截在预览上，比的就不是「引擎产出」而是「预览的再压缩结果」——
    # 断言会变成一条时快时慢的时序赌局。判据是 src 已经是 download 且解码完成。
    page.wait_for_function(
        "() => {"
        "  const img = document.querySelector('.sv-viewer-img-after');"
        "  return !!img && img.src.includes('/download') && img.complete"
        " && img.naturalWidth > 0;"
        "}",
        timeout=20_000,
    )

    # 关掉分割线：分割线是一条压在画布中缝上的线，且两条 ``.sv-viewer-tag`` 角标
    # 也压在图上。关掉对比后只有「修复后」一张图整幅铺满画布，分割线隐藏，
    # 剩下的干扰就只有顶部角标带（已在裁剪时排除）。
    page.click("#btnCompare")
    # 切 1:1：让一个图像像素对应一个 CSS 像素（deviceScaleFactor 默认 1），
    # 于是截图上的像素就是图像像素，不需要跟「适配缩放」的重采样混在一起。
    page.click('[data-viewer-action="one"]')
    settle_frames(page, frames=3)

    shot = Image.open(io.BytesIO(page.locator(".sv-viewer-canvas").screenshot())).convert("RGB")

    produced = output_files(e2e_server.outputs) - outputs_before
    assert len(produced) == 1, f"成品数量不对: {sorted(produced)}"
    produced_path = os.path.join(e2e_server.outputs, produced.pop())
    with Image.open(produced_path) as expected_raw:
        expected = expected_raw.convert("RGB")

    # 截图尺寸与期望尺寸应当一致（一致才谈得上「逐像素」；差 1-2 px 允许裁齐，
    # 差得多说明 1:1 没生效，那是用例前提不成立，直接失败而不是放宽判据）。
    assert abs(shot.width - expected.width) <= 2, (shot.width, expected.width)
    assert abs(shot.height - expected.height) <= 2, (shot.height, expected.height)
    assert expected.size == EXPECTED_SIZE, expected.size

    crop_shot, crop_expected = _crop_both(shot, expected)
    bad_ratio, mean_diff, over = _stats(crop_shot, crop_expected)

    # 负控：镜像期望图必须判不匹配，否则说明判据空转
    mirror_bad_ratio, _, _ = _stats(crop_shot, ImageOps.mirror(crop_expected))

    print(
        f"[pixel-diff] shot={shot.size} expected={expected.size} "
        f"crop={crop_shot.size} bad_ratio={bad_ratio:.4%} over={over} "
        f"mean={mean_diff:.3f} mirror_bad_ratio={mirror_bad_ratio:.2%}"
    )

    assert bad_ratio <= MAX_BAD_RATIO, (
        f"渲染像素与引擎产出不一致：超差 {over} 个像素（{bad_ratio:.2%}），平均差 {mean_diff:.2f}"
    )
    assert mean_diff <= MAX_MEAN_DIFF, f"平均像素差 {mean_diff:.2f} 过大"
    assert mirror_bad_ratio >= MIN_MIRROR_BAD_RATIO, (
        f"镜像负控只差 {mirror_bad_ratio:.2%} 像素 —— 判据过宽，这条用例证明不了什么"
    )
