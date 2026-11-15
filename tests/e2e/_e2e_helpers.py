#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""E2E 用例侧的小工具：造图、选文件、等终态、读队列行、目录快照。

刻意不放进 ``conftest.py``：那里放的是**环境搭建**（起服务、起浏览器），
这里放的是**用例自己的动作与断言辅助**。混在一起会让「夹具」与「用例逻辑」
的边界变模糊。文件名以 ``_`` 开头，pytest 不会把它当用例模块收集。
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from PIL import Image

# 「任一终态可观测」= 进度卡还在跑 / 错误卡 / 结果卡。沿用主旅程用例里那条
# 踩过的坑：``startRun`` 结束循环时会立刻把进度卡收起，毫秒级失败只会让它
# 可见一瞬，等「进度卡可见」约 1/10 概率超时；三者必有一个开着才是可靠信号。
TERMINAL_ANY = (
    "() => { const g = id => document.getElementById(id);"
    " return !g('progressCard').hidden || !g('errorCard').hidden"
    " || !g('resultCard').hidden; }"
)

# 临时文件/半成品的名字特征：``tempfile.mkstemp`` 的默认前缀 ``tmp``、
# 下载中间态、打包半成品。用来证明「取消后没留下垃圾」不是只看某个固定文件名。
TEMP_NAME_MARKERS = (".tmp", ".part", ".partial", ".crdownload", ".download")
TEMP_NAME_PREFIXES = ("tmp", ".tmp")


def make_solid_png(
    path: str | os.PathLike[str], size: tuple[int, int], color: tuple[int, int, int]
) -> str:
    """纯色 PNG（桩引擎按 BICUBIC 放大后仍是同一纯色，便于反查「这张图来自哪个输入」）。"""
    Image.new("RGB", size, color).save(str(path), "PNG")
    return str(path)


def make_pattern_png(path: str | os.PathLike[str], size: tuple[int, int]) -> str:
    """硬边图案 PNG（像素 diff 用）。

    为什么不用纯色：纯色经 BICUBIC ×4 后与「朴素拉伸」在像素上几乎一致，
    截图比对会既测不出差异、也无法做镜像负控。这里用四象限异色 + 8px 明暗格：
    放大后边缘会留下确定的振铃，左右镜像后象限互换——负控必然判红。
    """
    width, height = size
    img = Image.new("RGB", size, (250, 250, 250))
    pixels = img.load()
    quad_colors = ((220, 40, 40), (40, 160, 60), (40, 80, 200), (240, 190, 40))
    for y in range(height):
        for x in range(width):
            quad = (0 if x < width // 2 else 1) + (0 if y < height // 2 else 2)
            base = quad_colors[quad]
            bump = 0 if (x // 8 + y // 8) % 2 == 0 else 26
            pixels[x, y] = tuple(min(255, channel + bump) for channel in base)
    img.save(str(path), "PNG")
    return str(path)


def select_files(page: Any, base_url: str, paths: Iterable[str | os.PathLike[str]]) -> None:
    """打开工作台并选图（单张落 pickedBox、多张落 queueBox，这里不区分）。"""
    page.goto(f"{base_url}/restore")
    page.wait_for_selector("#dropzone", state="visible")
    page.set_input_files("#fileInput", [str(p) for p in paths])
    page.wait_for_function(
        "() => !document.getElementById('btnStart').disabled",
        timeout=10_000,
    )


def click_start(page: Any) -> None:
    """点开始并等到「任一终态可观测」。"""
    page.click("#btnStart")
    page.wait_for_function(TERMINAL_ANY, timeout=20_000)


def queue_rows(page: Any) -> list[dict[str, Any]]:
    """当前队列行快照（名字/状态/徽标/进度条宽度），按 DOM 顺序。"""
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#queueList li.sv-queue-item'))"
        ".map(li => {"
        "  const f = li.querySelector('.sv-queue-fill');"
        "  const b = li.querySelector('.sv-queue-badge');"
        "  const n = li.querySelector('.sv-queue-name');"
        "  const m = li.querySelector('.sv-queue-meta');"
        "  return {idx: Number(li.dataset.idx), status: li.dataset.status || '',"
        "    badge: b ? b.textContent : '', fill: f ? f.style.width : '',"
        "    name: n ? n.textContent : '', meta: m ? m.textContent : ''};"
        "})"
    )


def install_queue_observer(page: Any) -> None:
    """记录每一次队列行变化（带序号），供「顺序 / 每项进度」这类时序断言使用。

    只读 DOM：``MutationObserver`` 盯 ``#queueList`` 子树的属性与文本，
    每次变化拍一张全行快照。序号（``n``）比时间戳更可靠——同一毫秒内两次变化
    时挂钟会分不出先后，序号不会。
    """
    page.evaluate(
        "() => {"
        "  window.__queueLog = [];"
        "  const list = document.getElementById('queueList');"
        "  const snap = () => {"
        "    Array.from(document.querySelectorAll('#queueList li.sv-queue-item')).forEach(li => {"
        "      const f = li.querySelector('.sv-queue-fill');"
        "      const b = li.querySelector('.sv-queue-badge');"
        "      const n = li.querySelector('.sv-queue-name');"
        "      const m = li.querySelector('.sv-queue-meta');"
        "      window.__queueLog.push({idx: Number(li.dataset.idx),"
        "        status: li.dataset.status || '', badge: b ? b.textContent : '',"
        "        fill: f ? f.style.width : '', name: n ? n.textContent : '',"
        "        meta: m ? m.textContent : '',"
        "        n: window.__queueLog.length + 1});"
        "    });"
        "  };"
        "  const obs = new MutationObserver(snap);"
        "  obs.observe(list, {subtree: true, childList: true, characterData: true,"
        "    attributes: true, attributeFilter: ['data-status', 'style']});"
        "  window.__queueObs = obs;"
        "  snap();"
        "}"
    )


def queue_log(page: Any) -> list[dict[str, Any]]:
    return page.evaluate("() => window.__queueLog || []")


def wait_batch_completed(page: Any, timeout_ms: int = 90_000) -> None:
    """批量跑完：进度卡收起且结果卡出现（``startRun`` 的收尾动作）。"""
    page.wait_for_function(
        "() => document.getElementById('progressCard').hidden"
        " && !document.getElementById('resultCard').hidden",
        timeout=timeout_ms,
    )


def wait_run_finished(page: Any, timeout_ms: int = 60_000) -> None:
    """没有结果卡的成功期望时用：进度卡收起即「这一轮循环结束了」。"""
    page.wait_for_function(
        "() => document.getElementById('progressCard').hidden",
        timeout=timeout_ms,
    )


def dir_snapshot(path: str) -> set[str]:
    """目录里的文件名集合（不存在时返回空集）。"""
    try:
        return set(os.listdir(path))
    except FileNotFoundError:
        return set()


def temp_files(path: str) -> list[str]:
    """目录里像「临时文件/半成品」的名字。"""
    names = []
    for name in sorted(dir_snapshot(path)):
        lowered = name.lower()
        if lowered.endswith(TEMP_NAME_MARKERS) or lowered.startswith(TEMP_NAME_PREFIXES):
            names.append(name)
    return names


def output_files(path: str) -> set[str]:
    """输出目录里的成品（``*_vosr2_*.png``）。"""
    return {name for name in dir_snapshot(path) if "_vosr2_" in name and name.endswith(".png")}


def settle_frames(page: Any, frames: int = 2) -> None:
    """等若干帧，让 transform / clip-path / 图片解码后的绘制真正落到屏幕上。"""
    page.evaluate(
        "(n) => new Promise(resolve => {"
        "  let left = n;"
        "  const tick = () => (left-- <= 0 ? resolve(true) : requestAnimationFrame(tick));"
        "  requestAnimationFrame(tick);"
        "})",
        frames,
    )
