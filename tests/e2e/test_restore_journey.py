#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""主旅程 E2E：上传 → 推理(mock) → 对比 → 下载。

对应 TD-014 的处置方向原文（「上传→推理(mock)→对比→下载」主旅程 Playwright 化）。
这里断言的都是**只有真浏览器 + 真 HTTP 才拿得到的东西**：

* 进度条真的走过 SSE 的中间态（不是「直接跳到 100%」）；
* 对比查看器左右两张图都真的解码成功，且右图分辨率等于「原图 × upscale」
  —— 这条同时证明桩引擎的参数、输出落盘、预览端点、viewer 的 naturalWidth 全都对得上；
* 下载是浏览器真实的 ``download`` 事件，文件名与字节（PNG/JPEG 魔数、像素尺寸）都对；
* 伪造扩展名的文件被服务端魔数校验挡下后，UI 落到错误卡而**没有**进入推理
  （用桩引擎的调用次数守住：错误路径不该产生一次推理）。
"""

from __future__ import annotations

import io
import os

from PIL import Image

INPUT_SIZE = (64, 48)
UPSCALE = 4
EXPECTED_SIZE = (INPUT_SIZE[0] * UPSCALE, INPUT_SIZE[1] * UPSCALE)


def _make_png(path: str, size: tuple[int, int] = INPUT_SIZE) -> str:
    Image.new("RGB", size, (180, 40, 90)).save(path, "PNG")
    return path


def _upload_and_start(page, base_url: str, image_path: str) -> None:
    """打开工作台、选图、点开始（不含等待结果）。

    点完之后**不等 ``#progressCard`` 可见**：``startRun`` 在循环结束时会立刻把进度卡
    收起来，上传被 400 拒掉这类「毫秒级失败」会让它只可见一个极短的中间态
    （实测约 1/10 概率读到 hidden 而超时）。等「任一终态可观测」才是可靠信号——
    进度卡（还在跑）/ 错误卡 / 结果卡，三者总有一个是开的。
    """
    page.goto(f"{base_url}/restore")
    page.wait_for_selector("#dropzone", state="visible")
    page.set_input_files("#fileInput", image_path)
    page.wait_for_selector("#pickedBox", state="visible")
    page.wait_for_function(
        "() => !document.getElementById('btnStart').disabled",
        timeout=10_000,
    )
    page.click("#btnStart")
    page.wait_for_function(
        "() => { const g = id => document.getElementById(id);"
        " return !g('progressCard').hidden || !g('errorCard').hidden"
        " || !g('resultCard').hidden; }",
        timeout=20_000,
    )


def _wait_result(page) -> None:
    page.wait_for_selector("#resultCard", state="visible", timeout=60_000)


def test_journey_upload_infer_compare_download(page, e2e_server, tmp_path) -> None:
    base = e2e_server.base_url
    src = _make_png(str(tmp_path / "cat.png"))

    _upload_and_start(page, base, src)

    # ① 上传：文件名真的进了 pickedName（不是「选中了但没解析出文件」）
    assert "cat.png" in page.text_content("#pickedName")

    # ② 推理中：SSE 中间态必须被页面收到，否则进度条只会从 0 跳 100
    page.wait_for_function(
        "() => {"
        "  const el = document.getElementById('progressPct');"
        "  return el && parseInt(el.textContent, 10) > 0 && parseInt(el.textContent, 10) < 100;"
        "}",
        timeout=20_000,
    )
    assert page.text_content("#progressStage").strip() != ""

    # ③ 结果：对比查看器两张图都解码完成（is-ready 是 viewer 在两图 onload 后才加的）
    _wait_result(page)
    page.wait_for_function(
        "() => document.getElementById('viewer').classList.contains('is-ready')",
        timeout=20_000,
    )
    sizes = page.evaluate(
        "() => ({"
        "  before: document.querySelector('.sv-viewer-img-before').naturalWidth,"
        "  after: document.querySelector('.sv-viewer-img-after').naturalWidth,"
        "  hud: (document.getElementById('viewerHud') || {}).textContent || '',"
        "})"
    )
    assert sizes["before"] == INPUT_SIZE[0], f"左图（原图）宽度异常: {sizes}"
    assert sizes["after"] == EXPECTED_SIZE[0], f"右图宽度未按 upscale={UPSCALE} 放大: {sizes}"

    # ④ 对比：默认分屏 → 点按钮切掉 → 再点恢复；拖分割线真的会改 clipPath
    viewer = page.locator("#viewer")
    assert "is-split" in (viewer.get_attribute("class") or "")
    page.click("#btnCompare")
    assert "is-split" not in (page.locator("#viewer").get_attribute("class") or "")
    page.click("#btnCompare")
    assert "is-split" in (page.locator("#viewer").get_attribute("class") or "")

    box = viewer.bounding_box()
    assert box, "对比查看器没有可见尺寸"
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 60, cy)
    page.mouse.move(cx + 90, cy, steps=8)
    clip = page.evaluate("() => document.querySelector('.sv-viewer-after').style.clipPath")
    assert clip.startswith("inset("), f"拖动分割线后 clipPath 未生效: {clip!r}"

    # ⑤ 下载：浏览器真实 download 事件 + 字节校验
    with page.expect_download(timeout=30_000) as dl_info:
        page.click("#btnDownload")
    download = dl_info.value
    assert download.suggested_filename.endswith(".png"), download.suggested_filename
    out_path = str(tmp_path / "downloaded.png")
    download.save_as(out_path)
    raw = open(out_path, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "下载内容不是 PNG"
    with Image.open(io.BytesIO(raw)) as img:
        assert img.size == EXPECTED_SIZE, f"下载图尺寸 {img.size} != {EXPECTED_SIZE}"

    # ⑥ 参数确实传到了引擎（UI → 表单 → 路由 → 引擎这一跳没断）
    assert e2e_server.stub.restore_calls, "桩引擎一次都没被调用"
    assert e2e_server.stub.restore_calls[-1]["upscale"] == UPSCALE
    # ⑦ 结果确实落到了输出目录（不是只存在于内存里）
    produced = os.listdir(e2e_server.outputs)
    assert any("_vosr2_" in name and name.endswith(".png") for name in produced), produced


def test_download_format_switch_produces_jpeg(page, e2e_server, tmp_path) -> None:
    base = e2e_server.base_url
    src = _make_png(str(tmp_path / "cat2.png"))

    _upload_and_start(page, base, src)
    _wait_result(page)

    page.select_option("#downloadFormat", "jpeg")
    with page.expect_download(timeout=30_000) as dl_info:
        page.click("#btnDownload")
    download = dl_info.value
    # 服务端按 ``format`` 原样拼扩展名（jpeg 是 ``.jpeg``，不是 ``.jpg``）——
    # 这条断言就是把这个既有契约锁住，别让「改名」悄悄变了下载文件名。
    assert download.suggested_filename.endswith(".jpeg"), download.suggested_filename
    out_path = str(tmp_path / "downloaded.jpg")
    download.save_as(out_path)
    raw = open(out_path, "rb").read()
    assert raw[:2] == b"\xff\xd8", "下载内容不是 JPEG（SOI 魔数不符）"
    with Image.open(io.BytesIO(raw)) as img:
        assert img.format == "JPEG"
        assert img.size == EXPECTED_SIZE


def test_fake_png_is_rejected_in_ui_without_inference(page, e2e_server, tmp_path) -> None:
    base = e2e_server.base_url
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"this is definitely not a png")

    calls_before = len(e2e_server.stub.restore_calls)
    _upload_and_start(page, base, str(fake))

    # 服务端魔数校验 400 → 前端落到错误卡，且**原因要上屏**。
    # 首跑时这里拿到的是一句「有任务失败: 1」——原因被聚合计数吞掉了（单文件时
    # 队列行还是隐藏的），所以本用例先按「原因可见」写断言，再改实现去满足它。
    #
    # 用 ``wait_for_function`` 轮询而不是「等卡片可见再读文本」：卡片可见（``hidden=false``）
    # 与文本写入虽是同一 tick，但把「等文本」写成轮询后，慢机/带覆盖率跑时不会读到中间态；
    # 失败时把当时的 DOM 状态一起报出来，避免只留一个 TimeoutError 让人无从下手。
    try:
        page.wait_for_function(
            "() => { const el = document.getElementById('errorMsg');"
            " return !!el && el.textContent.includes('不符'); }",
            timeout=20_000,
        )
    except Exception as exc:
        dump = page.evaluate(
            "() => ({"
            " cardHidden: document.getElementById('errorCard').hidden,"
            " msg: document.getElementById('errorMsg').textContent,"
            " progressHidden: document.getElementById('progressCard')?.hidden ?? null,"
            " resultHidden: document.getElementById('resultCard').hidden,"
            "})"
        )
        raise AssertionError(f"20 秒内失败原因没上屏，页面状态 {dump}（{exc}）") from None

    page.wait_for_selector("#errorCard", state="visible")
    msg = page.text_content("#errorMsg").strip()
    assert msg != ""
    assert "不符" in msg, f"失败原因没有上屏，只显示了聚合计数: {msg!r}"
    assert not msg.endswith(": 1"), f"失败原因仍是聚合计数: {msg!r}"
    assert not page.locator("#resultCard").is_visible(), "被拒的上传不应该出现结果卡"

    # 错误路径没有触发推理（否则「拒绝」只是前端假象）
    page.wait_for_timeout(500)
    assert len(e2e_server.stub.restore_calls) == calls_before
