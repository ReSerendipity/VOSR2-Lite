#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""API 契约扩展测试：表单校验矩阵、上传落盘、任务生命周期、SSE、历史库。

与 ``test_app_smoke.py`` 的分工：冒烟只验证「能通」，本文件验证「错了怎么错」——
每条错误路径的状态码与错误信封、任务状态机的合法迁移、SSE 事件的终止条件。

设计约束：
- 不起推理、不加载权重（任务状态直接写 sqlite，避免依赖 GPU 与模型文件）；
- 所有写盘文件在 teardown 删除，不污染 data/uploads 与 outputs。
"""

from __future__ import annotations

import io
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image


def _png_bytes(size=(32, 24), color=(90, 120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _upload(client, name="t.png", data=None, content_type="image/png", **form):
    return client.post(
        "/api/restore/",
        files={"file": (name, data if data is not None else _png_bytes(), content_type)},
        data=form or None,
    )


# ── 表单参数校验矩阵（Depends 在进入端点前生效，返回 400 而非 422）──────
@pytest.mark.parametrize(
    "form,keyword",
    [
        ({"upscale": 0}, "upscale"),
        ({"upscale": 5}, "upscale"),
        ({"upscale": -1}, "upscale"),
        ({"align_method": "lab"}, "align_method"),
        ({"align_method": "WAVELET"}, "align_method"),
        ({"steps": 0}, "steps"),
        ({"steps": 51}, "steps"),
        ({"seed": -2}, "seed"),
        ({"tile_size": -1}, "分块"),
        ({"vae_tile_size": -8}, "分块"),
    ],
)
def test_form_param_validation(client, form, keyword):
    """非法表单参数必须 400 且错误消息点明是哪个参数。"""
    resp = _upload(client, **form)
    assert resp.status_code == 400, resp.text
    assert keyword in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    "form",
    [
        {"upscale": 1},
        {"upscale": 2},
        {"upscale": 3},
        {"upscale": 4},
        {"steps": 1},
        {"steps": 25},
        {"steps": 50},
        {"align_method": "wavelet"},
        {"align_method": "adain"},
        {"align_method": "nofix"},
        {"seed": -1},
        {"seed": 0},
        {"tile_size": 0},
        {"tile_size": 512},
    ],
)
def test_form_param_accepts_legal_values(client, form):
    """合法参数必须放行（枚举边界不能误伤）。"""
    resp = _upload(client, **form)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


# ── 魔数校验（纯函数，覆盖全部支持格式）──────────────────────────────────
@pytest.mark.parametrize(
    "filename,head,expected",
    [
        ("a.png", b"\x89PNG\r\n\x1a\n\x00\x00", True),
        ("a.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", True),
        ("a.jpeg", b"\xff\xd8\xff\xdb", True),
        ("a.gif", b"GIF89a\x01\x00", True),
        ("a.gif", b"GIF87a\x01\x00", True),
        ("a.bmp", b"BM\x36\x84", True),
        ("a.webp", b"RIFF\x24\x00\x00\x00WEBPVP8 ", True),
        ("a.tif", b"II*\x00\x08\x00", True),
        ("a.tiff", b"MM\x00*\x00\x00", True),
        # 伪造与截断
        ("a.png", b"\xff\xd8\xff\xe0", False),  # JPEG 冒充 PNG
        ("a.jpg", b"\x89PNG\r\n\x1a\n", False),  # PNG 冒充 JPEG
        ("a.png", b"\x89PNG", False),  # 截断到 4 字节
        ("a.png", b"", False),  # 空文件
        ("a.webp", b"RIFF\x24\x00\x00\x00WAVE", False),  # RIFF 但不是 WEBP
        ("a.txt", b"\x89PNG\r\n\x1a\n", False),  # 扩展名本身不支持
        ("a", b"\x89PNG\r\n\x1a\n", False),  # 无扩展名
    ],
)
def test_validate_upload_magic(filename, head, expected):
    from app.integrated_app.security.magic_check import validate_upload_magic

    assert validate_upload_magic(filename, head) is expected


@pytest.mark.parametrize(
    "name,data",
    [
        ("truncated.png", b"\x89PNG"),
        ("empty.png", b""),
        ("jpeg-as-png.png", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"),
    ],
)
def test_upload_rejects_bad_magic(client, name, data):
    """接口层同样要拦：错误消息里必须点明「伪造」。"""
    resp = _upload(client, name=name, data=data)
    assert resp.status_code == 400
    assert "伪造" in resp.json()["error"]["message"]


@pytest.mark.parametrize("name", ["a.exe", "a.txt", "a.mp4", "noext"])
def test_upload_rejects_bad_extension(client, name):
    resp = _upload(client, name=name)
    assert resp.status_code == 400
    assert "不支持" in resp.json()["error"]["message"]


# ── 隔离：上传目录改到临时目录，模型加载打桩（不碰真实权重/GPU）──────────
@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch):
    from app.integrated_app.routes.restore import upload as upload_mod

    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(upload_mod, "get_uploads_dir", lambda: str(target))
    return target


@pytest.fixture(autouse=True)
def _stub_model(app, monkeypatch):
    """把引擎获取打桩成确定性失败，避免后台 worker 去碰真实权重。"""
    from app.integrated_app.model_manager import ModelManager

    def _boom(self, name=None):
        raise RuntimeError("测试桩：模型不可用")

    monkeypatch.setattr(ModelManager, "get_engine", _boom)


@pytest.fixture()
def db_path(app):
    return app.state.history_db.db_path


def _insert_task(
    db_path,
    status="pending",
    *,
    progress=0.0,
    message="",
    error="",
    output_path="",
    output_size=0,
    stage="",
    record_status=None,
    input_file="",
    output_file="",
):
    """直接写 sqlite 造一条指定状态的任务，返回 (task_id, record_id)。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    task_id = uuid.uuid4().hex[:16]
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            """
            INSERT INTO history (input_file, output_file, task_type, status,
                                 checkpoint, params_json, error, processing_time,
                                 output_size, created_at)
            VALUES (?, ?, 'image', ?, 'VOSR2', '{}', ?, 0, ?, ?)
            """,
            (input_file, output_file, record_status or status, error, output_size, now),
        )
        record_id = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO tasks (task_id, record_id, status, progress, message,
                               error, output_path, output_size, processing_time,
                               current_stage, task_type, params_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'image', '{}', ?)
            """,
            (
                task_id,
                record_id,
                status,
                progress,
                message,
                error,
                output_path,
                output_size,
                stage,
                now,
            ),
        )
        con.commit()
    finally:
        con.close()
    return task_id, record_id


def _task_row(db_path, task_id):
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _record_row(db_path, record_id):
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM history WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ── 上传成功路径：落盘、建记录、入队 ──────────────────────────────────────
def test_upload_accepts_and_creates_task(client, db_path, _isolated_uploads):
    resp = _upload(client, name="photo.png", upscale=2, align_method="adain", seed=7)
    assert resp.status_code == 200
    data = resp.json()["data"]
    task_id = data["task_id"]
    assert data["status"] == "pending"
    assert len(task_id) == 16 and all(c in "0123456789abcdef" for c in task_id)

    row = _task_row(db_path, task_id)
    assert row is not None and row["status"] in ("pending", "processing", "failed")
    params = json.loads(row["params_json"])
    assert params["upscale"] == 2
    assert params["align_method"] == "adain"
    assert params["seed"] == 7

    saved = list(Path(_isolated_uploads).glob(f"{task_id}_*"))
    assert len(saved) == 1
    assert saved[0].name == f"{task_id}_photo.png"
    assert saved[0].stat().st_size > 0


def test_upload_defaults_are_persisted(client, db_path):
    """未传表单参数时，落库的应是默认值（upscale=4 / steps=1 / wavelet）。"""
    task_id = _upload(client, name="plain.png").json()["data"]["task_id"]
    params = json.loads(_task_row(db_path, task_id)["params_json"])
    assert (params["upscale"], params["steps"], params["align_method"]) == (4, 1, "wavelet")
    assert params["seed"] == 42
    assert params["checkpoint"] == ""


def test_upload_strips_path_from_filename(client, _isolated_uploads):
    """文件名里的目录成分必须被剥掉（防目录穿越落盘）。"""
    resp = _upload(client, name="../../evil.png")
    assert resp.status_code == 200
    task_id = resp.json()["data"]["task_id"]
    saved = list(Path(_isolated_uploads).glob(f"{task_id}_*"))
    assert len(saved) == 1
    assert saved[0].parent == Path(_isolated_uploads)
    assert saved[0].name == f"{task_id}_evil.png"


# ── 取消：状态机只允许 pending / processing ───────────────────────────────
@pytest.mark.parametrize("status", ["pending", "processing"])
def test_cancel_active_task(client, db_path, status):
    """排队中/运行中可取消，且任务与历史记录同步置为 cancelled。"""
    task_id, record_id = _insert_task(db_path, status, progress=0.3)
    resp = client.post(f"/api/restore/{task_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"

    assert _task_row(db_path, task_id)["status"] == "cancelled"
    assert _record_row(db_path, record_id)["status"] == "cancelled"
    assert "取消" in _task_row(db_path, task_id)["error"]


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_cancel_terminal_task_rejected(client, db_path, status):
    """终态任务不可取消：必须 400 并回显当前状态，而不是静默改状态。"""
    task_id, _ = _insert_task(db_path, status)
    resp = client.post(f"/api/restore/{task_id}/cancel")
    assert resp.status_code == 400
    assert status in resp.json()["error"]["message"]
    assert _task_row(db_path, task_id)["status"] == status  # 未被篡改


def test_cancel_unknown_task(client):
    resp = client.post("/api/restore/deadbeefdeadbeef/cancel")
    assert resp.status_code == 404
    assert resp.json()["success"] is False


def test_cancel_is_idempotent_at_second_call(client, db_path):
    """取消成功后再取消应被状态机拒绝（400），而不是重复写库。"""
    task_id, _ = _insert_task(db_path, "pending")
    assert client.post(f"/api/restore/{task_id}/cancel").status_code == 200
    assert client.post(f"/api/restore/{task_id}/cancel").status_code == 400


# ── 结果查询：四种状态的返回形状 ──────────────────────────────────────────
@pytest.mark.parametrize("status", ["pending", "processing"])
def test_result_in_progress(client, db_path, status):
    task_id, _ = _insert_task(db_path, status, progress=0.42)
    body = client.get(f"/api/restore/{task_id}/result").json()
    assert body["success"] is True
    assert body["data"]["status"] == status
    assert body["data"]["progress"] == 0.42


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_result_terminal_with_error(client, db_path, status):
    task_id, _ = _insert_task(db_path, status, error="显存不足")
    body = client.get(f"/api/restore/{task_id}/result").json()
    assert body["data"]["status"] == status
    assert body["data"]["error"] == "显存不足"


def test_result_completed_with_existing_file(client, db_path, tmp_path):
    out = tmp_path / "result.png"
    out.write_bytes(_png_bytes())
    task_id, _ = _insert_task(
        db_path, "completed", progress=1.0, output_path=str(out), output_size=out.stat().st_size
    )
    data = client.get(f"/api/restore/{task_id}/result").json()["data"]
    assert data["status"] == "completed"
    assert data["output_path"] == str(out)
    assert data["output_size"] == out.stat().st_size
    assert "warning" not in data


def test_result_completed_with_missing_file_warns(client, db_path, tmp_path):
    """结果文件被删后仍应返回 completed，但必须带 warning 而不是 500。"""
    task_id, _ = _insert_task(db_path, "completed", output_path=str(tmp_path / "gone.png"))
    data = client.get(f"/api/restore/{task_id}/result").json()["data"]
    assert data["status"] == "completed"
    assert data["warning"] == "输出文件不存在"


def test_result_unknown_task(client):
    resp = client.get("/api/restore/nosuchtask0000/result")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# ── SSE 进度流：终止条件与事件内容 ────────────────────────────────────────
def _sse_events(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_sse_completed_task_emits_single_terminal_event(client, db_path):
    """已完成任务：发一条终态事件后立即结束（不能挂着等 1 小时）。"""
    task_id, _ = _insert_task(
        db_path, "completed", progress=1.0, message="修复完成", stage="done", output_size=2048
    )
    resp = client.get(f"/api/restore/{task_id}/progress")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert len(events) == 1
    ev = events[0]
    assert ev["task_id"] == task_id
    assert ev["status"] == "completed"
    assert ev["progress"] == 1.0
    assert ev["current_stage"] == "done"
    assert ev["output_size"] == 2048


def test_sse_failed_task_carries_error_and_ends(client, db_path):
    task_id, _ = _insert_task(db_path, "failed", error="CUDA out of memory")
    events = _sse_events(client.get(f"/api/restore/{task_id}/progress").text)
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert "out of memory" in events[0]["error"]


def test_sse_cancelled_task_ends(client, db_path):
    task_id, _ = _insert_task(db_path, "cancelled", error="用户取消")
    events = _sse_events(client.get(f"/api/restore/{task_id}/progress").text)
    assert events[-1]["status"] == "cancelled"


def test_sse_unknown_task_404(client):
    assert client.get("/api/restore/nosuchtask0000/progress").status_code == 404


def test_sse_headers_disable_buffering(client, db_path):
    """SSE 必须禁用缓存与反向代理缓冲，否则进度会攒到最后一次性吐出。"""
    task_id, _ = _insert_task(db_path, "completed", progress=1.0)
    resp = client.get(f"/api/restore/{task_id}/progress")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"
    assert resp.headers["connection"] == "keep-alive"


# ── 历史库：分页边界、状态过滤、删除 ──────────────────────────────────────
@pytest.mark.parametrize(
    "query",
    [
        "?page=0",
        "?page_size=0",
        "?page_size=101",
        "?page=-1",
    ],
)
def test_history_rejects_out_of_range_paging(client, query):
    """分页参数越界必须是 422（Query 约束），不能静默纠正。"""
    assert client.get(f"/api/system/history{query}").status_code == 422


def test_history_pagination_shape(client, db_path):
    for _ in range(3):
        _insert_task(db_path, "completed")
    body = client.get("/api/system/history?page=1&page_size=2").json()["data"]
    assert len(body["records"]) == 2
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2


def test_history_status_filter(client, db_path):
    _insert_task(db_path, "completed")
    _insert_task(db_path, "failed")
    failed = client.get("/api/system/history?status=failed").json()["data"]["records"]
    assert failed and all(r["status"] == "failed" for r in failed)
    completed = client.get("/api/system/history?status=completed").json()["data"]["records"]
    assert completed and all(r["status"] == "completed" for r in completed)


def test_history_delete_then_gone(client, db_path):
    _, record_id = _insert_task(db_path, "completed")
    assert client.delete(f"/api/system/history/{record_id}").json()["data"] == {
        "deleted": record_id
    }
    assert client.delete(f"/api/system/history/{record_id}").status_code == 404


def test_history_delete_unknown(client):
    assert client.delete("/api/system/history/99999").status_code == 404


def test_history_thumb_falls_back_to_input(client, db_path, tmp_path):
    """失败记录没有 output_file，缩略图应回退到原图而不是 404。"""
    from app.integrated_app.config import PROJECT_ROOT

    uploads = Path(PROJECT_ROOT) / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    src = uploads / f"thumb_src_{uuid.uuid4().hex[:8]}.png"
    src.write_bytes(_png_bytes())
    try:
        _, record_id = _insert_task(db_path, "failed", input_file=str(src))
        resp = client.get(f"/api/system/history/{record_id}/thumb?max=64")
        assert resp.status_code == 200
        assert max(Image.open(io.BytesIO(resp.content)).size) <= 64
    finally:
        src.unlink(missing_ok=True)


# ── 语言切换：参数契约 ────────────────────────────────────────────────────
@pytest.mark.parametrize("payload", [{}, {"locale": ""}, {"locale": "  "}])
def test_locale_requires_value(client, payload):
    assert client.post("/api/system/locale", json=payload).status_code == 400


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_locale_switch_ok(client, locale):
    resp = client.post("/api/system/locale", json={"locale": locale})
    assert resp.status_code == 200
    assert resp.json()["data"]["locale"] == locale
