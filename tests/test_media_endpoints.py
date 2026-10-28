#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""媒体端点测试：原图 / 预览 / 下载格式转换 / 历史缩略图。

不起推理：直接用 sqlite3 往测试库写一条"已完成"任务，把原图与结果文件
放在 PathGuard 白名单内的真实目录（data/uploads、outputs），用完删除。
"""

from __future__ import annotations

import io
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image


def _png_bytes(size=(64, 48), color=(120, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def completed_task(app, client):
    """造一条 completed 任务，返回 (task_id, record_id)。"""
    from app.integrated_app.config import PROJECT_ROOT

    uploads = Path(PROJECT_ROOT) / "data" / "uploads"
    outputs = Path(PROJECT_ROOT) / "outputs"
    uploads.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)

    task_id = uuid.uuid4().hex[:16]
    src = uploads / f"{task_id}_media_test.png"
    out = outputs / f"media_test_vosr2_{task_id[:8]}.png"
    src.write_bytes(_png_bytes())
    out.write_bytes(_png_bytes(size=(128, 96), color=(30, 140, 90)))
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    db_path = app.state.history_db.db_path
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            """
            INSERT INTO history (input_file, output_file, task_type, status,
                                 checkpoint, params_json, error, processing_time,
                                 output_size, created_at)
            VALUES (?, ?, 'image', 'completed', 'VOSR2', '{}', '', 1.25, ?, ?)
            """,
            (str(src), str(out), out.stat().st_size, now),
        )
        record_id = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO tasks (task_id, record_id, status, progress, message,
                               error, output_path, output_size, processing_time,
                               current_stage, task_type, params_json, created_at)
            VALUES (?, ?, 'completed', 1.0, '修复完成', '', ?, ?, 1.25,
                    'done', 'image', '{}', ?)
            """,
            (task_id, record_id, str(out), out.stat().st_size, now),
        )
        con.commit()
    finally:
        con.close()

    yield task_id, record_id

    src.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


def test_input_endpoint_serves_original(client, completed_task):
    task_id, _ = completed_task
    resp = client.get(f"/api/restore/{task_id}/input")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")


def test_preview_endpoint_downscales(client, completed_task):
    task_id, _ = completed_task
    resp = client.get(f"/api/restore/{task_id}/preview?kind=output&max=128")
    assert resp.status_code == 200
    img = Image.open(io.BytesIO(resp.content))
    assert max(img.size) <= 128


def test_preview_rejects_unknown_kind(client, completed_task):
    task_id, _ = completed_task
    assert client.get(f"/api/restore/{task_id}/preview?kind=evil").status_code == 422


def test_preview_rejects_bad_task_id(client):
    resp = client.get("/api/restore/zzzz/preview")
    assert resp.status_code in (400, 404)


def test_download_default_is_png(client, completed_task):
    task_id, _ = completed_task
    resp = client.get(f"/api/restore/{task_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_download_converts_to_jpeg(client, completed_task):
    task_id, _ = completed_task
    resp = client.get(f"/api/restore/{task_id}/download?format=jpeg&quality=80")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(resp.content)).format == "JPEG"


def test_download_converts_to_webp(client, completed_task):
    task_id, _ = completed_task
    resp = client.get(f"/api/restore/{task_id}/download?format=webp")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.content[:4] == b"RIFF"


def test_download_rejects_bad_format(client, completed_task):
    task_id, _ = completed_task
    assert client.get(f"/api/restore/{task_id}/download?format=exe").status_code == 422


def test_history_record_carries_task_id(client, completed_task):
    task_id, _ = completed_task
    body = client.get("/api/system/history?page=1&page_size=5").json()
    records = body["data"]["records"]
    assert records and records[0]["task_id"] == task_id
    assert records[0]["processing_time"] == 1.25


def test_history_thumbnail(client, completed_task):
    _, record_id = completed_task
    resp = client.get(f"/api/system/history/{record_id}/thumb?max=64")
    assert resp.status_code == 200
    assert max(Image.open(io.BytesIO(resp.content)).size) <= 64


def test_history_thumbnail_missing_record(client):
    assert client.get("/api/system/history/99999/thumb").status_code == 404
