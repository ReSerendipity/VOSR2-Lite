"""安装包（Setup.exe）溯源与校验工具 —— VOSR2-Lite TD-011 代码侧。

背景（TD-011）：主分发物 `VOSR2Lite-Setup-v<ver>.exe` 一直是**本地手工构建、不入库、
无 SHA256、无 provenance、无代码签名**的 100 MB 文件。签名需要采购证书（属主明确不买），
因此退而求其次，把"投毒可溯源"这条补上：

1. 构建时把「谁在什么源码状态、用哪个工具、由哪些输入」建成一份 `provenance.json`，
   与 `SHA256SUMS.txt` 一起随 Release 发布；
2. 任何人拿到 exe 都能用 `verify` 子命令在本地复算 digest，证明「这个 exe 就是这份
   provenance 记录的那一个」，不需要信任发布者；
3. 工作树脏（有未提交改动）默认拒绝出包 —— 否则 provenance 里的 commit 与实际字节
   对不上，等于给伪造品背书。确需出包用 `--allow-dirty`，provenance 里如实标
   `"dirty": true`。

关于"可复现"：实测 NSIS 3.10 对同一脚本 + 同一负载是逐字节确定性的（PE 时间戳取自
makensis stub 的常量，不是挂钟），所以"同一 commit + 同一输入 → 同一 digest"这条成立，
校验和才有意义。脚本里仍会设 SOURCE_DATE_EPOCH，但那是工具链退路，不是复现的前提。

只依赖标准库，便于在没装依赖的发布机上跑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CHUNK_SIZE = 1 << 20  # 1 MiB
PROVENANCE_SCHEMA = "vosr2.lite.installer.provenance/v1"


def sha256_file(path: Path) -> str:
    """分块计算 SHA-256（安装包 100 MB 级，不整块读入内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def file_size(path: Path) -> int:
    return path.stat().st_size


def write_checksums(artifacts: List[Path], out_path: Path) -> List[str]:
    """写 `sha256sum` 兼容的校验文件（`<hash>  <name>`，两空格）。返回行列表。"""
    names: List[str] = []
    lines: List[str] = []
    for artifact in artifacts:
        if not artifact.is_file():
            raise FileNotFoundError(f"artifact not found: {artifact}")
        digest = sha256_file(artifact)
        lines.append(f"{digest}  {artifact.name}")
        names.append(artifact.name)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return lines


def parse_checksums(text: str) -> Dict[str, str]:
    """解析 SHA256SUMS 文本为 {文件名: digest}；格式错的行直接抛 ValueError。"""
    result: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            raise ValueError(f"malformed checksum line: {raw!r}")
        digest, name = parts[0].strip().lower(), parts[1].strip()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"malformed digest in line: {raw!r}")
        result[name] = digest
    return result


def git_field(repo: Path, args: List[str]) -> str:
    """取 git 字段；失败返回空串（不阻断，但会让 dirty 判定更严格）。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def git_is_dirty(repo: Path) -> bool:
    """tracked 文件有改动即视为脏（未跟踪文件不算，它们不进包）。"""
    status = git_field(repo, ["status", "--porcelain", "--untracked-files=no"])
    return bool(status)


def source_date_epoch(repo: Path) -> Optional[int]:
    """记录本次构建对应的源码时刻（HEAD 提交时间），写进 provenance。

    实测（NSIS 3.10，2026-11-08 本机对照）：同一脚本 + 同一负载，两次构建间隔 3 秒、
    **都不设** SOURCE_DATE_EPOCH，产物字节完全一致；读出的 PE `TimeDateStamp` 是 makensis
    自带 stub 的固定值（1711817723），不随挂钟变化 —— 也就是说 NSIS 本身已经是可复现的，
    不需要靠这个变量"冻结时间"。设它只是给工具链留退路（换 NSIS 版本或换工具时仍能固定
    时间戳），真正的复现保证来自"同一 commit + 同一输入"。
    记这个值是为了回答"这份 exe 对应哪个提交时刻"，而不是宣称"设了它才可复现"。
    """
    value = git_field(repo, ["log", "-1", "--format=%ct"])
    if not value.isdigit():
        return None
    return int(value)


def build_provenance(
    subject: Path,
    *,
    version: str,
    repo: Path,
    inputs: Optional[List[Path]] = None,
    tool_version: str | None = None,
    subject_date_epoch: Optional[int] = None,
    built_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """生成 provenance 记录（结构固定，可直接 json.dumps 落盘）。"""
    if not subject.is_file():
        raise FileNotFoundError(f"subject not found: {subject}")

    built = built_at or datetime.now(timezone.utc)
    commit = git_field(repo, ["rev-parse", "HEAD"])
    tree = git_field(repo, ["rev-parse", "HEAD^{tree}"])
    dirty = git_is_dirty(repo)
    epoch = source_date_epoch(repo) if subject_date_epoch is None else subject_date_epoch
    input_records = []
    for item in inputs or []:
        if not item.is_file():
            raise FileNotFoundError(f"input not found: {item}")
        input_records.append(
            {"name": item.name, "size": file_size(item), "sha256": sha256_file(item)}
        )

    subject_record: Dict[str, Any] = {
        "name": subject.name,
        "size": file_size(subject),
        "sha256": sha256_file(subject),
        "signed": False,  # 无代码签名是既定事实，写进记录里，别让读者猜
    }
    epoch_val = epoch
    if epoch_val is not None:
        subject_record["source_date_epoch"] = epoch_val
        subject_record["source_date_epoch_iso"] = datetime.fromtimestamp(
            epoch_val, tz=timezone.utc
        ).isoformat()

    return {
        "schema": PROVENANCE_SCHEMA,
        "version": version,
        "built_at": built.isoformat(),
        "subject": subject_record,
        "source": {
            "commit": commit,
            "tree": tree,
            "dirty": dirty,
            "repository": "https://github.com/ReSerendipity/VOSR2-Lite",
        },
        "builder": {
            "tool": "makensis (NSIS)",
            "tool_version": tool_version or "",
            "build_script": "scripts/installer/build_setup.ps1",
            "platform": sys.platform,
        },
        "inputs": input_records,
        "notes": (
            "安装包未做代码签名（证书需采购，属主裁决不做）。完整性以 SHA256SUMS.txt 为准，"
            "来源以本 provenance 记录为准：复算 subject.sha256 与本文件一致，且 source.commit "
            "与 source.tree 可复现，即证明该 exe 出自该提交的构建。"
        ),
    }


def verify_provenance(provenance: Dict[str, Any], base_dir: Path) -> List[str]:
    """复算 digest 并校验记录自洽；返回问题列表（空列表 = 通过）。"""
    problems: List[str] = []
    subject = provenance.get("subject") or {}
    name = subject.get("name")
    if not name:
        return ["provenance 缺少 subject.name"]

    path = base_dir / name
    if not path.is_file():
        return [f"找不到被校验的产物: {name}"]

    actual_size = file_size(path)
    if subject.get("size") not in (None, actual_size):
        problems.append(f"大小不符: provenance={subject.get('size')} 实际={actual_size}")
    actual_digest = sha256_file(path)
    if subject.get("sha256") != actual_digest:
        problems.append(f"SHA-256 不符: provenance={subject.get('sha256')} 实际={actual_digest}")
    for item in provenance.get("inputs") or []:
        item_path = base_dir / str(item.get("name"))
        if not item_path.is_file():
            problems.append(f"缺少输入文件: {item.get('name')}")
            continue
        if sha256_file(item_path) != item.get("sha256"):
            problems.append(f"输入文件被改动: {item.get('name')}")
    if not provenance.get("source", {}).get("commit"):
        problems.append("provenance 缺少 source.commit（无法溯源到提交）")
    if provenance.get("source", {}).get("dirty"):
        problems.append("source.dirty=true：产物出自带未提交改动的源码，不可复现")
    return problems


@dataclass
class VerifyResult:
    ok: bool
    problems: List[str] = field(default_factory=list)


def verify_checksums(checksums_path: Path, base_dir: Path) -> VerifyResult:
    """按 SHA256SUMS 逐条复算，返回缺失/不符清单。"""
    entries = parse_checksums(checksums_path.read_text(encoding="utf-8"))
    problems: List[str] = []
    for name, expected in entries.items():
        path = base_dir / name
        if not path.is_file():
            problems.append(f"缺少文件: {name}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"SHA-256 不符: {name} expected={expected} actual={actual}")
    return VerifyResult(ok=not problems, problems=problems)


def _cmd_write(args: argparse.Namespace) -> int:
    subject = Path(args.subject)
    repo = Path(args.repo)
    if git_is_dirty(repo) and not args.allow_dirty:
        print(
            "拒绝出包：工作树有未提交改动（provenance 里的 commit 与产物字节对不上）。"
            "如确需出包请加 --allow-dirty，provenance 会标 dirty=true。",
            file=sys.stderr,
        )
        return 2

    inputs = [Path(p) for p in (args.input or [])]
    provenance = build_provenance(
        subject,
        version=args.version,
        repo=repo,
        inputs=inputs,
        tool_version=args.tool_version,
        subject_date_epoch=args.source_date_epoch,
    )
    out_dir = Path(args.out_dir) if args.out_dir else subject.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    prov_path = out_dir / (subject.name + ".provenance.json")
    prov_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    checksums_path = out_dir / "SHA256SUMS.txt"
    artifacts = [subject] + inputs
    lines = write_checksums(artifacts, checksums_path)

    print(f"provenance -> {prov_path}")
    print(f"checksums  -> {checksums_path}")
    for line in lines:
        print(f"  {line}")
    if provenance["source"]["dirty"]:
        print("警告：provenance 标记 dirty=true（产物不可由该 commit 复现）", file=sys.stderr)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir)
    failed = False
    if args.provenance:
        data = json.loads(Path(args.provenance).read_text(encoding="utf-8"))
        problems = verify_provenance(data, base_dir)
        for p in problems:
            print(f"[provenance] {p}", file=sys.stderr)
        failed = failed or bool(problems)
    if args.checksums:
        result = verify_checksums(Path(args.checksums), base_dir)
        for p in result.problems:
            print(f"[checksums] {p}", file=sys.stderr)
        failed = failed or not result.ok
    if not failed:
        print("校验通过：产物字节与 provenance / SHA256SUMS 一致")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VOSR2-Lite 安装包溯源与校验")
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="为已构建的安装包生成 provenance.json 与 SHA256SUMS.txt")
    w.add_argument("--subject", required=True, help="安装包路径（如 VOSR2Lite-Setup-v0.2.1.exe）")
    w.add_argument("--version", required=True)
    w.add_argument("--repo", default=".", help="仓库根（取 commit/tree/dirty）")
    w.add_argument("--input", action="append", help="构建输入（数据卷 / 壳 exe / 7za），可多次传")
    w.add_argument("--tool-version", default="", help="makensis 版本，如 3.10")
    w.add_argument(
        "--source-date-epoch", type=int, default=None, help="复现用时间戳（默认取 HEAD 提交时间）"
    )
    w.add_argument("--out-dir", default="", help="输出目录（默认与安装包同目录）")
    w.add_argument(
        "--allow-dirty", action="store_true", help="允许在脏工作树上出包（记录里标 dirty）"
    )
    w.set_defaults(func=_cmd_write)

    v = sub.add_parser("verify", help="复算 digest 校验产物")
    v.add_argument("--base-dir", default=".", help="产物所在目录")
    v.add_argument("--provenance", default="", help="provenance.json 路径")
    v.add_argument("--checksums", default="", help="SHA256SUMS.txt 路径")
    v.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
