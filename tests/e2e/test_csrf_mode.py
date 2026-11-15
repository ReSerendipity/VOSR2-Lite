#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CSRF 开启模式 E2E（TD-014 未覆盖清单 §七-7）。

两个视角，缺一个就会留出不同的盲区：

* **协议视角**（``httpx`` 直连）：GET 种签名 cookie；非安全请求「无 header」「有
  header 无 cookie」「签名被改」三种都必须 403 ``CSRF_INVALID``；带正确 token 必须
  真的建任务并跑完——而且被拒的那几次**不能留下任何业务副作用**（上传目录没多文件、
  桩引擎没多调用），否则「403」只是个状态码，挡不住请求。
* **浏览器视角**（Playwright）：``app.js::csrfHeaders`` 从 ``document.cookie`` 读
  token 并回传 ``X-CSRF-Token``，开启 CSRF 后整条「选图 → 上传 → 推理 → 结果」链
  仍然可用。只做协议视角会漏掉最常见的坏法——中间件装上了、前端不回传，页面能开、
  一点开始就 403。

浏览器视角里再打一发**不带 header 的 POST**：必须拿到 403 ``CSRF_INVALID``。这条
是本用例自带的活体负控——它证明这个服务上「拒绝链」真的闭合，而不是 CSRF 根本没
生效、所有断言只是空转（同样的 POST 若 CSRF 关闭会走到 422/400 的表单校验）。

签名可复算：夹具把 ``VOSR2_SECRET_KEY`` 固定成已知 32 字节 hex，于是断言可以离线
重算 ``HMAC-SHA256(secret, nonce)`` 并与 cookie 里的签名比——证明这枚 token 是**服务端
用密钥签的**，不是随机串，也不是别的密钥签的。
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx
from _e2e_helpers import make_solid_png, output_files, select_files
from PIL import Image

CSRF_ERROR_CODE = "CSRF_INVALID"
INPUT_SIZE = (32, 32)
UPSCALE = 4


def _assert_signed(token: str, secret_key_hex: str) -> None:
    """token 形如 ``nonce.HMAC-SHA256(secret, nonce)``，离线复算签名。"""
    assert "." in token, f"token 形状不对: {token!r}"
    nonce, signature = token.rsplit(".", 1)
    expected = hmac.new(
        bytes.fromhex(secret_key_hex), nonce.encode(), hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(signature, expected), (
        f"token 签名与服务端密钥不符（不是这个服务签的？）: {token[:16]}…"
    )


def _post_restore(base: str, source: str, **kwargs) -> httpx.Response:
    """带一个真 PNG 打 ``POST /api/restore/``；cookie/header 全部由调用方显式给。"""
    with open(source, "rb") as fh:
        return httpx.post(
            f"{base}/api/restore/",
            files={"file": (os.path.basename(source), fh, "image/png")},
            data={"upscale": str(UPSCALE)},
            timeout=15.0,
            **kwargs,
        )


def _wait_completed(base: str, task_id: str, timeout: float = 30.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = httpx.get(f"{base}/api/restore/{task_id}/result", timeout=10.0).json()["data"]
        if data.get("status") in ("completed", "failed", "cancelled"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内没有终态")


def test_csrf_double_submit_protocol(e2e_server_csrf, tmp_path) -> None:
    base = e2e_server_csrf.base_url
    secret = e2e_server_csrf.secret_key_hex
    source = make_solid_png(tmp_path / "csrf_a.png", INPUT_SIZE, (130, 40, 200))
    uploads_before = set(os.listdir(e2e_server_csrf.uploads))
    outputs_before = output_files(e2e_server_csrf.outputs)
    calls_before = len(e2e_server_csrf.stub.restore_calls)

    # ① 安全方法必须种 cookie，且是密钥签的、JS 可读（HttpOnly 会让前端回传不了）
    seed = httpx.get(f"{base}/restore", timeout=10.0)
    assert seed.status_code == 200, seed.text
    token = seed.cookies.get("csrf_token")
    assert token, f"GET /restore 没有种 csrf_token：{seed.headers.get('set-cookie')!r}"
    _assert_signed(token, secret)
    assert "httponly" not in (seed.headers.get("set-cookie") or "").lower(), (
        "csrf_token 被标成 HttpOnly，app.js 读不到，开启 CSRF 后前端必 403"
    )
    nonce, signature = token.rsplit(".", 1)
    forged = f"{nonce}.{'0' * len(signature)}"

    # ② 三种「不该通过」的组合，必须都是 403 + CSRF_INVALID
    rejections = {
        "无 cookie 无 header": _post_restore(base, source),
        "有 cookie 无 header": _post_restore(base, source, cookies={"csrf_token": token}),
        "有 cookie 但 header 签名被改": _post_restore(
            base, source, cookies={"csrf_token": token}, headers={"X-CSRF-Token": forged}
        ),
        "有 header 无 cookie": _post_restore(base, source, headers={"X-CSRF-Token": token}),
    }
    for label, resp in rejections.items():
        assert resp.status_code == 403, f"{label}: 期望 403，实际 {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["success"] is False, f"{label}: {body}"
        assert body["error"]["code"] == CSRF_ERROR_CODE, f"{label}: {body}"

    # ③ 被拒的请求不许有任何业务副作用（403 应是「在业务之前」挡下的）
    assert set(os.listdir(e2e_server_csrf.uploads)) == uploads_before, (
        "被 CSRF 拒掉的请求仍然写了上传文件：403 发生在业务之后，判据是假绿"
    )
    assert len(e2e_server_csrf.stub.restore_calls) == calls_before, "被拒的请求进了引擎"

    # ④ 失败响应会补种一枚**新的、签名合法**的 cookie（浏览器下一次请求自愈）
    healed = rejections["有 cookie 但 header 签名被改"].cookies.get("csrf_token")
    assert healed and healed != forged, f"403 没有补种新 token: {healed!r}"
    _assert_signed(healed, secret)

    # ⑤ 带正确 token：真的建任务、跑完、落盘
    ok = _post_restore(
        base, source, cookies={"csrf_token": token}, headers={"X-CSRF-Token": token}
    )
    assert ok.status_code == 200, f"带正确 token 仍被拒: {ok.status_code} {ok.text}"
    task_id = ok.json()["data"]["task_id"]
    assert len(task_id) == 16, task_id

    detail = _wait_completed(base, task_id)
    assert detail["status"] == "completed", detail
    produced = output_files(e2e_server_csrf.outputs) - outputs_before
    assert len(produced) == 1, sorted(produced)
    out_path = detail["output_path"]
    assert os.path.dirname(out_path) == e2e_server_csrf.outputs, out_path
    assert os.path.basename(out_path).startswith("csrf_a_vosr2_"), out_path
    with Image.open(out_path) as img:
        assert img.size == (INPUT_SIZE[0] * UPSCALE, INPUT_SIZE[1] * UPSCALE), img.size
    assert len(e2e_server_csrf.stub.restore_calls) == calls_before + 1, (
        "正确 token 的请求没有进引擎"
    )


def test_csrf_enabled_frontend_sends_header(page, e2e_server_csrf, tmp_path) -> None:
    base = e2e_server_csrf.base_url
    secret = e2e_server_csrf.secret_key_hex
    color = (200, 120, 30)
    source = make_solid_png(tmp_path / "csrf_b.png", (64, 48), color)
    outputs_before = output_files(e2e_server_csrf.outputs)
    calls_before = len(e2e_server_csrf.stub.restore_calls)

    page.goto(f"{base}/restore")
    page.wait_for_selector("#dropzone", state="visible")

    # ① 浏览器里真的拿得到这枚 cookie（HttpOnly 的 cookie 读出来是空串）
    token = page.evaluate(
        "() => { const m = document.cookie.match(/(?:^|;\\s*)csrf_token=([^;]*)/);"
        " return m ? decodeURIComponent(m[1]) : ''; }"
    )
    assert token, "页面里读不到 csrf_token（被 HttpOnly 挡住或压根没种）"
    _assert_signed(token, secret)

    # ② 自带的活体负控：同一页面、同一端点、**故意不带 header** 必须 403
    probe = page.evaluate(
        "async () => {"
        "  const r = await fetch('/api/restore/', {method: 'POST', body: new FormData()});"
        "  let b = null; try { b = await r.json(); } catch (_) {}"
        "  return {status: r.status, code: (b && b.error && b.error.code) || ''};"
        "}"
    )
    assert probe["status"] == 403, f"不带 header 的 POST 没被拒: {probe}"
    assert probe["code"] == CSRF_ERROR_CODE, probe

    # ③ 正常旅程：前端自动回传 header，整条链仍然通
    select_files(page, base, [source])
    page.click("#btnStart")
    # 等「进度卡收起 + 错误卡/结果卡二者之一出现」这个**真终态**：只等 TERMINAL_ANY
    # 会在进度卡刚亮起时就放行（那时错误卡还没写出来），断言看不出发生了什么。
    # 失败时错误卡带着服务端原文上屏，断言就能直接给出「CSRF token 验证失败」。
    page.wait_for_function(
        "() => document.getElementById('progressCard').hidden"
        " && (!document.getElementById('errorCard').hidden"
        " || !document.getElementById('resultCard').hidden)",
        timeout=30_000,
    )
    assert page.locator("#errorCard").is_hidden(), (
        f"开启 CSRF 后前端请求被拒：{(page.text_content('#errorMsg') or '').strip()}"
    )
    assert page.locator("#resultCard").is_visible(), "没有出现结果卡（旅程没走完）"
    page.wait_for_function(
        "() => document.getElementById('viewer').classList.contains('is-ready')",
        timeout=20_000,
    )

    href = page.get_attribute("#btnDownload", "href") or ""
    assert "/api/restore/" in href, href
    task_id = href.split("/api/restore/")[1].split("/")[0]
    assert len(task_id) == 16, href

    detail = httpx.get(f"{base}/api/restore/{task_id}/result", timeout=10.0).json()["data"]
    assert detail["status"] == "completed", detail

    produced = output_files(e2e_server_csrf.outputs) - outputs_before
    assert len(produced) == 1, sorted(produced)
    out_path = os.path.join(e2e_server_csrf.outputs, next(iter(produced)))
    with Image.open(out_path) as img:
        assert img.size == (64 * 4, 48 * 4), img.size
        center = img.convert("RGB").getpixel((img.size[0] // 2, img.size[1] // 2))
    assert all(abs(a - e) <= 6 for a, e in zip(center, color)), (
        f"成品中心像素 {center} 与输入 {color} 不符"
    )
    assert len(e2e_server_csrf.stub.restore_calls) == calls_before + 1, (
        "浏览器这次上传没有进引擎"
    )
