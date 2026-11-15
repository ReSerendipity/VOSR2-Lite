#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""批量队列与中途取消 E2E（TD-014 未覆盖清单 (b) 的前两项）。

覆盖的是 ``restore.js::startRun`` 那个 ``for`` 循环的两侧行为——

* 顺序侧：3 个文件真的**按选中顺序逐张**进引擎，每张都有中间进度，
  全部完成后落盘 3 个成品，命名/尺寸/内容各自对得上自己那张输入；
* 取消侧：点 ``#btnCancel`` 后循环在下一张之前 break，**后续项既不上传也不进引擎**
  （用「上传目录只多 1 个文件」+「桩引擎只多 1 次调用」两条副作用断言守住，
  而不是只看 UI 文案），被取消的那张在服务端留下 ``cancelled`` + 原因，
  且不产生任何可用结果（预览/下载端点都拒绝）、不留临时文件。

断言只挑「真浏览器 / 真 HTTP 才拿得到」的东西：队列行顺序与时序、逐行进度徽标、
浏览器里点出来的取消、以及服务端落库状态。
"""

from __future__ import annotations

import os

import httpx
from _e2e_helpers import (
    click_start,
    dir_snapshot,
    install_queue_observer,
    make_solid_png,
    output_files,
    queue_log,
    queue_rows,
    select_files,
    temp_files,
    wait_batch_completed,
    wait_run_finished,
)
from PIL import Image

INPUT_SIZE = (64, 48)
UPSCALE = 4
EXPECTED_SIZE = (INPUT_SIZE[0] * UPSCALE, INPUT_SIZE[1] * UPSCALE)

# 三张不同纯色：桩引擎 BICUBIC ×4 后纯色仍是纯色，于是「成品中心像素」能反查
# 这张成品到底由哪张输入产出——防「批量只处理了第一张却复制了三份」这类假绿。
BATCH_INPUTS = (
    ("bq_a.png", (200, 30, 40)),
    ("bq_b.png", (30, 180, 60)),
    ("bq_c.png", (40, 60, 210)),
)

CANCEL_INPUTS = (
    ("cancel_a.png", (210, 60, 60)),
    ("cancel_b.png", (60, 200, 120)),
    ("cancel_c.png", (70, 90, 220)),
)


PARTIAL_INPUTS = (
    ("pf_a.png", (200, 30, 40)),
    ("pf_bad.png", None),  # 扩展名 .png、内容是文本：服务端魔数校验必拒（400）
    ("pf_c.png", (40, 60, 210)),
)


def _write_fake_png(path: str) -> str:
    """写一个「扩展名骗人」的输入：内容是纯文本，不是任何图片格式。"""
    with open(path, "wb") as fh:
        fh.write(b"this is plain text, not an image\n")
    return path


def _center_pixel(path: str) -> tuple[int, int, int]:
    with Image.open(path) as img:
        width, height = img.size
        return img.convert("RGB").getpixel((width // 2, height // 2))


def _near(actual: tuple[int, int, int], expected: tuple[int, int, int], tol: int = 6) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def test_batch_queue_three_files_in_order(page, e2e_server, tmp_path) -> None:
    base = e2e_server.base_url
    sources = [make_solid_png(tmp_path / name, INPUT_SIZE, color) for name, color in BATCH_INPUTS]
    outputs_before = output_files(e2e_server.outputs)
    calls_before = len(e2e_server.stub.restore_calls)

    select_files(page, base, sources)

    # ① 多选后走的是队列分支：队列卡出现、单张卡收起、按钮带张数
    page.wait_for_selector("#queueBox", state="visible")
    assert page.locator("#pickedBox").is_hidden(), "多文件时不应再显示单张卡"
    assert "(3)" in (page.text_content("#btnStart") or ""), page.text_content("#btnStart")

    rows = queue_rows(page)
    assert [row["name"] for row in rows] == [name for name, _ in BATCH_INPUTS], rows
    assert [row["idx"] for row in rows] == [0, 1, 2], rows
    assert {row["status"] for row in rows} == {"idle"}, rows
    assert {row["fill"] for row in rows} == {"0%"}, rows

    # ② 逐行进度与时序：装观察器后开跑
    install_queue_observer(page)
    click_start(page)
    wait_batch_completed(page)

    log = queue_log(page)
    assert log, "队列行一次变化都没记录到——观察器没挂上或队列根本没渲染"

    first_running = {}
    for entry in log:
        if entry["status"] == "running":
            first_running.setdefault(entry["idx"], entry["n"])
    assert set(first_running) == {0, 1, 2}, f"有队列行从未进入 running: {first_running}"
    # 顺序：第 1 张先 running，第 2 张次之，第 3 张最后（串行队列的真实证据）
    assert first_running[0] < first_running[1] < first_running[2], first_running

    # 每项都真的出现过 1..99 的中间进度（不是「一张跑完直接跳已完成」）
    for idx in (0, 1, 2):
        pcts = sorted(
            int(entry["badge"].rstrip("%"))
            for entry in log
            if entry["idx"] == idx and entry["status"] == "running" and entry["badge"].endswith("%")
        )
        assert any(1 <= pct <= 99 for pct in pcts), f"行 {idx} 没有中间进度，实际序列 {pcts}"

    # ③ 终态：三行都是 done，汇总计数 3/3，进度条满格
    rows = queue_rows(page)
    assert {row["status"] for row in rows} == {"done"}, rows
    assert {row["fill"] for row in rows} == {"100%"}, rows
    assert (page.text_content("#queueSummary") or "").strip().startswith("3/3"), page.text_content(
        "#queueSummary"
    )

    # ④ 落盘：恰好 3 个新成品，命名 <输入名>_vosr2_<task_id 前 8 位>.png
    produced = output_files(e2e_server.outputs) - outputs_before
    assert len(produced) == 3, f"落盘数量不对: {sorted(produced)}"
    suffixes = set()
    for name, color in BATCH_INPUTS:
        stem = os.path.splitext(name)[0]
        matched = [p for p in produced if p.startswith(f"{stem}_vosr2_")]
        assert len(matched) == 1, f"{name} 没有唯一的成品: {sorted(produced)}"
        suffix = matched[0][len(f"{stem}_vosr2_") : -len(".png")]
        assert len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix), matched[0]
        suffixes.add(suffix)
        full = os.path.join(e2e_server.outputs, matched[0])
        with Image.open(full) as img:
            assert img.size == EXPECTED_SIZE, f"{matched[0]} 尺寸 {img.size} != {EXPECTED_SIZE}"
        center = _center_pixel(full)
        assert _near(center, color), (
            f"{matched[0]} 中心像素 {center} 与输入 {name} 的 {color} 不符"
            "（批量把某一张的结果复制了三份？）"
        )
    # 三个 task_id 前缀互不相同：三张是三个独立任务，不是同一个任务被复用
    assert len(suffixes) == 3, suffixes

    # ⑤ 批量结束后停在**最后一张**的结果上（btnDownload 的 href 里就是那个 task_id）
    href = page.get_attribute("#btnDownload", "href") or ""
    task_id = href.split("/api/restore/")[1].split("/")[0] if "/api/restore/" in href else ""
    assert len(task_id) == 16, f"下载链接里没有 task_id: {href!r}"
    detail = httpx.get(f"{base}/api/restore/{task_id}/result", timeout=10.0).json()["data"]
    assert detail["status"] == "completed", detail
    assert os.path.basename(detail["output_path"]).startswith("bq_c_vosr2_"), detail

    # ⑥ 三次串行推理，参数一致
    calls = e2e_server.stub.restore_calls[calls_before:]
    assert len(calls) == 3, f"桩引擎调用次数 {len(calls)} != 3（某张没进引擎或重复进引擎）"
    assert [call["size"] for call in calls] == [INPUT_SIZE] * 3, calls
    assert {call["upscale"] for call in calls} == {UPSCALE}, calls


def test_cancel_mid_batch_stops_remaining_items(page, e2e_server, tmp_path) -> None:
    base = e2e_server.base_url
    sources = [make_solid_png(tmp_path / name, INPUT_SIZE, color) for name, color in CANCEL_INPUTS]
    outputs_before = output_files(e2e_server.outputs)
    uploads_before = dir_snapshot(e2e_server.uploads)
    exports_before = dir_snapshot(e2e_server.exports)
    calls_before = len(e2e_server.stub.restore_calls)

    # 把桩引擎放慢到每阶段 0.6s（整个循环仍只在秒级）。理由：取消必须落在
    # 「任务已建、还没跑完」的窗口里才有判别力；默认 0.25s 时从看到第一个进度
    # 到整张跑完不足 0.3s，点慢一点就会撞上「任务已完成 → /cancel 返回 400」，
    # 变成一条会偶发红的用例。用完即复原，避免污染同会话其它用例。
    original_delay = e2e_server.stub.stage_delay
    e2e_server.stub.stage_delay = 0.6
    try:
        select_files(page, base, sources)
        page.wait_for_selector("#queueBox", state="visible")
        rows = queue_rows(page)
        assert [row["name"] for row in rows] == [name for name, _ in CANCEL_INPUTS], rows

        click_start(page)
        # 等第 1 行真的进入 running 且出现中间进度：此时 ``state.taskId`` 已存在，
        # /cancel 打的是一个「存在且未结束」的任务，不会吃 400。
        page.wait_for_function(
            "() => {"
            "  const li = document.querySelector('#queueList li.sv-queue-item[data-idx=\"0\"]');"
            "  if (!li || li.dataset.status !== 'running') return false;"
            "  const badge = li.querySelector('.sv-queue-badge').textContent;"
            "  const pct = parseInt(badge, 10);"
            "  return badge.endsWith('%') && pct > 0 && pct < 100;"
            "}",
            timeout=20_000,
        )
        page.click("#btnCancel")
        wait_run_finished(page)
    finally:
        e2e_server.stub.stage_delay = original_delay

    # ① 被取消的那行：状态与文案都上屏（不是静默结束）
    rows = queue_rows(page)
    assert rows[0]["status"] == "cancelled", rows[0]
    assert "已取消" in rows[0]["badge"], rows[0]
    # 取消不该被当成「失败」：错误卡与结果卡都不该出现
    assert page.locator("#errorCard").is_hidden(), "取消后弹出了失败卡"
    assert page.locator("#resultCard").is_hidden(), "取消后出现了结果卡（没有可用结果却报成功）"

    # ② 未执行项必须被标成「已取消」：循环 break 后要把剩下的 idle 行回写状态，
    # 否则它们一直显示「排队中」，看起来像「还在等」（这是 TD-014 §七-3 那条 UI 缺口）。
    # 同时它们既没进引擎，也没有任何进度。
    for idx in (1, 2):
        assert rows[idx]["status"] == "cancelled", rows[idx]
        assert "已取消" in rows[idx]["badge"], rows[idx]
        assert rows[idx]["fill"] == "0%", rows[idx]

    calls = e2e_server.stub.restore_calls[calls_before:]
    assert len(calls) == 1, f"取消后仍进了 {len(calls)} 次引擎（后续项没被拦住）: {calls}"

    # ③ 后续项连上传都没发生：上传目录只多 1 个文件（不带任何临时后缀）
    uploads_after = dir_snapshot(e2e_server.uploads)
    new_uploads = uploads_after - uploads_before
    assert len(new_uploads) == 1, f"上传目录多了 {sorted(new_uploads)}（后续项也上传了？）"
    only = next(iter(new_uploads))
    assert only.endswith("_cancel_a.png"), only
    assert temp_files(e2e_server.uploads) == [], temp_files(e2e_server.uploads)

    # ④ 无残留：取消不产出成品，也不在导出目录留半成品
    assert output_files(e2e_server.outputs) - outputs_before == set(), "取消后仍落了成品"
    assert dir_snapshot(e2e_server.exports) == exports_before, "导出目录被写脏"
    assert temp_files(e2e_server.exports) == [], temp_files(e2e_server.exports)

    # ⑤ 服务端也认这笔账：记录/任务都是 cancelled + 原因，且没有输出文件
    history = httpx.get(f"{base}/api/system/history?status=cancelled&page_size=100", timeout=10.0)
    records = history.json()["data"]["records"]
    target = [r for r in records if os.path.basename(r.get("input_file") or "") == only]
    assert len(target) == 1, f"取消记录没落库: {[r.get('input_file') for r in records]}"
    record = target[0]
    assert record["status"] == "cancelled", record
    assert record["error"] == "用户取消", record
    assert not record.get("output_file"), record

    task_id = record["task_id"]
    result = httpx.get(f"{base}/api/restore/{task_id}/result", timeout=10.0).json()["data"]
    assert result["status"] == "cancelled", result
    # 没有可用结果：预览与下载端点都必须拒绝（用户拿不到半成品）
    assert httpx.get(f"{base}/api/restore/{task_id}/preview", timeout=10.0).status_code == 400
    assert httpx.get(f"{base}/api/restore/{task_id}/download", timeout=10.0).status_code == 400


def test_batch_partial_failure_keeps_other_items(page, e2e_server, tmp_path) -> None:
    """3 张里 1 张必失败：失败项自己红、其余照旧完成，且失败不弄脏别处。

    失败注入用的是「扩展名 .png、内容是文本」——服务端在**写盘之前**用魔数校验
    400 拒掉（见 ``security/magic_check.py``），因此这条失败是**确定的**（不依赖
    文件大小、随机种子或桩引擎行为），又恰好落在「批量循环中间」而不是第一张/最后一张。
    """
    base = e2e_server.base_url
    sources: list[str] = []
    for name, color in PARTIAL_INPUTS:
        path = tmp_path / name
        sources.append(
            make_solid_png(path, INPUT_SIZE, color) if color else _write_fake_png(str(path))
        )

    outputs_before = output_files(e2e_server.outputs)
    uploads_before = dir_snapshot(e2e_server.uploads)
    exports_before = dir_snapshot(e2e_server.exports)
    calls_before = len(e2e_server.stub.restore_calls)

    select_files(page, base, sources)
    page.wait_for_selector("#queueBox", state="visible")
    rows = queue_rows(page)
    assert [row["name"] for row in rows] == [name for name, _ in PARTIAL_INPUTS], rows
    assert {row["status"] for row in rows} == {"idle"}, rows

    install_queue_observer(page)
    click_start(page)
    wait_batch_completed(page)

    # ① 只有中间那项失败，两侧仍完成（不是全军覆没，也不是静默跳过）
    rows = queue_rows(page)
    assert [row["status"] for row in rows] == ["done", "failed", "done"], rows
    assert "已完成" in rows[0]["badge"], rows[0]
    assert "失败" in rows[1]["badge"], rows[1]
    assert "已完成" in rows[2]["badge"], rows[2]

    # ② 失败原因是服务端原文（队列行说清为什么），不是聚合计数
    assert "不符" in rows[1]["meta"], rows[1]

    # ③ 无跨项串扰：成功两行没有沾上失败状态或失败原因
    for idx in (0, 2):
        assert "失败" not in rows[idx]["badge"], rows[idx]
        assert "不符" not in rows[idx]["meta"], rows[idx]

    # ④ 失败项从未真正跑起来：没有 >0 的中间进度，也从未 done
    log = queue_log(page)
    bad = [entry for entry in log if entry["idx"] == 1]
    assert bad, "失败行一次变化都没记录到（观察器没挂上？）"
    assert not [
        entry
        for entry in bad
        if entry["status"] == "running"
        and entry["badge"].endswith("%")
        and entry["badge"] != "0%"
    ], f"失败项出现了真实进度: {bad}"
    assert not [entry for entry in bad if entry["status"] == "done"], bad

    # ⑤ 聚合层：2/3 + 失败 1；批量失败只给计数（单张才上屏原因，见用例 3）
    summary = (page.text_content("#queueSummary") or "").strip()
    assert summary.startswith("2/3"), summary
    assert "失败" in summary and "1" in summary, summary
    err_msg = (page.text_content("#errorMsg") or "").strip()
    assert "有任务失败" in err_msg and err_msg.endswith(": 1"), err_msg
    assert page.locator("#errorCard").is_visible(), "部分失败没有把失败汇总上屏"

    # ⑥ 结果卡停在**最后一个成功项**（pf_c），失败项没有顶掉它
    assert page.locator("#resultCard").is_visible(), "仍有成功项却未展示结果"
    href = page.get_attribute("#btnDownload", "href") or ""
    task_id = href.split("/api/restore/")[1].split("/")[0] if "/api/restore/" in href else ""
    assert len(task_id) == 16, href
    detail = httpx.get(f"{base}/api/restore/{task_id}/result", timeout=10.0).json()["data"]
    assert detail["status"] == "completed", detail
    assert os.path.basename(detail["output_path"]).startswith("pf_c_vosr2_"), detail

    # ⑦ 落盘：恰好 2 个新成品，没有失败项的成品；每张中心像素对得上自己的输入
    produced = output_files(e2e_server.outputs) - outputs_before
    assert len(produced) == 2, f"落盘数量不对: {sorted(produced)}"
    assert not [p for p in produced if "pf_bad" in p], produced
    for name, color in PARTIAL_INPUTS:
        if color is None:
            continue
        stem = os.path.splitext(name)[0]
        matched = [p for p in produced if p.startswith(f"{stem}_vosr2_")]
        assert len(matched) == 1, f"{name} 没有唯一的成品: {sorted(produced)}"
        full = os.path.join(e2e_server.outputs, matched[0])
        with Image.open(full) as img:
            assert img.size == EXPECTED_SIZE, img.size
        assert _near(_center_pixel(full), color), (
            f"{matched[0]} 中心像素与输入 {name} 的 {color} 不符（跨项串用了结果？）"
        )

    # ⑧ 上传只发生在成功两项上：失败项在魔数校验就被拒，连文件都没落
    new_uploads = dir_snapshot(e2e_server.uploads) - uploads_before
    assert len(new_uploads) == 2, f"上传目录多了 {sorted(new_uploads)}"
    assert {name.split("_", 1)[-1] for name in new_uploads} == {"pf_a.png", "pf_c.png"}, (
        new_uploads
    )
    assert temp_files(e2e_server.uploads) == [], temp_files(e2e_server.uploads)

    # ⑨ 导出目录干净：没有任何成品被导出（失败项更不该有）
    assert dir_snapshot(e2e_server.exports) == exports_before, "导出目录被写脏"
    assert temp_files(e2e_server.exports) == [], temp_files(e2e_server.exports)

    # ⑩ 引擎只被成功两项各调用 1 次（失败项没进推理），尺寸与倍数一致
    calls = e2e_server.stub.restore_calls[calls_before:]
    assert len(calls) == 2, f"桩引擎调用次数 {len(calls)} != 2: {calls}"
    assert [call["size"] for call in calls] == [INPUT_SIZE] * 2, calls
    assert {call["upscale"] for call in calls} == {UPSCALE}, calls

    # ⑪ 服务端没为失败项建任务/记录：拒绝发生在 add_record 之前
    records = httpx.get(f"{base}/api/system/history?page_size=100", timeout=10.0).json()["data"][
        "records"
    ]
    names = [os.path.basename(r.get("input_file") or "") for r in records]
    assert not [n for n in names if n.endswith("pf_bad.png")], (
        f"失败输入居然落了历史记录（拒绝点太靠后）: {names[:8]}"
    )
