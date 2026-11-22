#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VOSR2-Lite 统一命令行入口。

用法:
    python -m app.integrated_app.cli --help
    python -m app.integrated_app.cli models status --json
    python -m app.integrated_app.cli system status --json
    python -m app.integrated_app.cli restore -i photo.jpg -o outputs/ -u 2
    python -m app.integrated_app.cli restore -i dir/ -o outputs/ --batch
    python -m app.integrated_app.cli history list --page 1 --json
    python -m app.integrated_app.cli --json restore -i photo.jpg   # --json 任意位置

设计（按项目实况收敛，不照搬 skill 的 P0-P4）：
- 单引擎、单业务族：只做 restore / models / system / history 四类。
- 默认直调 Service 层（ModelManager 带权重校验）；--server-url 显式指定时走 HTTP 复用常驻服务。
- Result 信封 + Human/JSON 双输出 + 语义退出码，供 AI agent 自动化调用。
"""

from __future__ import annotations

import argparse
import os
import sys

# 脚本直跑无包上下文时兜底：把项目根注入 sys.path。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.integrated_app.cli_output import (  # noqa: E402
    EXIT_USAGE_ERROR,
    Result,
    begin_json_mode,
    emit,
    end_json_mode,
)


def _add_restore_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-i", "--input", required=True, help="输入图片文件或目录")
    parser.add_argument("-o", "--output-dir", default="outputs", help="输出目录（默认 outputs/）")
    parser.add_argument("--batch", action="store_true", help="输入按目录处理（否则当单文件）")
    parser.add_argument("-c", "--checkpoint", default=None, help="检查点名（默认读配置）")
    parser.add_argument("-u", "--upscale", type=int, default=4, choices=[1, 2, 3, 4])
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--align-method", default="wavelet", choices=["wavelet", "adain", "nofix"])
    parser.add_argument("--tile-size", type=int, default=0, help="DiT 分块（0=自动）")
    parser.add_argument("--vae-tile-size", type=int, default=0, help="VAE 分块（0=自动）")
    parser.add_argument(
        "--server-url", default=None, help="复用常驻服务地址（如 http://127.0.0.1:7871）"
    )
    parser.add_argument("--config", default=None, help="指定 config.yaml 路径")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vosr2", description="VOSR2-Lite CLI（图像超分辨率本地工具箱）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_restore = sub.add_parser("restore", help="修复图片（单图或批量）")
    _add_restore_args(p_restore)

    p_models = sub.add_parser("models", help="模型检查点盘查")
    models_sub = p_models.add_subparsers(dest="action", required=True)
    ms = models_sub.add_parser("status", help="列出各检查点权重完整性")
    ms.add_argument("--config", default=None)

    p_system = sub.add_parser("system", help="环境状态")
    system_sub = p_system.add_subparsers(dest="action", required=True)
    ss = system_sub.add_parser("status", help="GPU / 权重 / 关键配置摘要")
    ss.add_argument("--config", default=None)

    p_history = sub.add_parser("history", help="历史记录查询")
    history_sub = p_history.add_subparsers(dest="action", required=True)
    hl = history_sub.add_parser("list", help="分页列出历史")
    hl.add_argument("--page", type=int, default=1)
    hl.add_argument("--page-size", type=int, default=20)
    hl.add_argument("--status", default=None)
    hl.add_argument("--config", default=None)
    hs = history_sub.add_parser("show", help="按 id 查看单条")
    hs.add_argument("record_id", type=int)
    hs.add_argument("--config", default=None)

    return parser


def _inputs_from_args(args: argparse.Namespace) -> list[str]:
    if args.batch:
        if not os.path.isdir(args.input):
            raise ValueError(f"--batch 要求输入是目录: {args.input}")
        from app.integrated_app.cli_service import IMAGE_EXTENSIONS

        names = sorted(
            name
            for name in os.listdir(args.input)
            if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
        )
        if not names:
            raise ValueError(f"输入目录没有图片: {args.input}")
        return [os.path.join(args.input, name) for name in names]
    return [args.input]


def _run_restore(args: argparse.Namespace, as_json: bool) -> int:
    from app.integrated_app.cli_remote import restore as remote_restore
    from app.integrated_app.cli_service import restore as local_restore

    try:
        inputs = _inputs_from_args(args)
    except ValueError as exc:
        return emit(Result.failure(str(exc), "USAGE_ERROR", command="restore"), as_json)

    checkpoint = args.checkpoint or "VOSR2"

    def _progress(stage: str, ratio: float) -> None:
        if not as_json:
            print(f"[{stage}] {ratio * 100:.0f}%", file=sys.stderr)

    try:
        if args.server_url:
            params = {
                "upscale": args.upscale,
                "steps": args.steps,
                "align_method": args.align_method,
                "seed": args.seed,
                "tile_size": args.tile_size,
                "vae_tile_size": args.vae_tile_size,
            }
            data = remote_restore(
                args.server_url,
                inputs,
                args.output_dir,
                params=params,
                checkpoint=checkpoint,
                progress_cb=_progress,
            )
        else:
            data = local_restore(
                inputs,
                args.output_dir,
                checkpoint=args.checkpoint,
                config_path=args.config,
                args=args,
                progress_cb=_progress,
            )
    except FileNotFoundError as exc:
        return emit(Result.failure(str(exc), "NOT_READY", command="restore"), as_json)
    except Exception as exc:
        return emit(Result.failure(str(exc), "BUSINESS_ERROR", command="restore"), as_json)

    if data.get("failed"):
        result = Result.failure(
            f"{data['failed']} 张失败: "
            + "; ".join(f"{f['input']}: {f['error']}" for f in data.get("failures", [])),
            "BUSINESS_ERROR",
            command="restore",
        )
        result.data = data
        return emit(result, as_json)
    return emit(Result.success(data, command="restore"), as_json)


def main(argv: list[str] | None = None) -> int:
    """入口：管理 JSON 模式的 stdout 隔离生命周期，委托给 _main_impl。"""
    raw = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in raw
    if as_json:
        raw = [a for a in raw if a != "--json"]
        # 必须在导入 cli_service / 引擎之前：vendored vosr/tiled_vae.py 直接 print，
        # 否则分块信息会混进 JSON 的 stdout（实测本轮超分踩到）。
        begin_json_mode()
    try:
        return _main_impl(raw, as_json)
    finally:
        if as_json:
            end_json_mode()


def _main_impl(raw: list[str], as_json: bool) -> int:
    # --json 已由 main 预提取
    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        return EXIT_USAGE_ERROR if exc.code else 0

    cmd = args.command
    try:
        if cmd == "restore":
            return _run_restore(args, as_json)
        if cmd == "models" and args.action == "status":
            from app.integrated_app.cli_service import models_status

            return emit(
                Result.success(models_status(config_path=args.config), command="models status"),
                as_json,
            )
        if cmd == "system" and args.action == "status":
            from app.integrated_app.cli_service import system_status

            return emit(
                Result.success(system_status(config_path=args.config), command="system status"),
                as_json,
            )
        if cmd == "history" and args.action == "list":
            from app.integrated_app.cli_service import history_list

            return emit(
                Result.success(
                    history_list(
                        page=args.page,
                        page_size=args.page_size,
                        status=args.status,
                        config_path=args.config,
                    ),
                    command="history list",
                ),
                as_json,
            )
        if cmd == "history" and args.action == "show":
            from app.integrated_app.cli_service import history_show

            data = history_show(args.record_id, config_path=args.config)
            return emit(Result.success(data, command="history show"), as_json)
    except FileNotFoundError as exc:
        return emit(Result.failure(str(exc), "NOT_READY", command=cmd), as_json)
    except ValueError as exc:
        return emit(Result.failure(str(exc), "USAGE_ERROR", command=cmd), as_json)
    except Exception as exc:
        return emit(Result.failure(str(exc), "BUSINESS_ERROR", command=cmd), as_json)

    parser.print_help()
    return EXIT_USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main())
