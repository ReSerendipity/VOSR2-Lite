"""TD-011 安装包溯源工具单测。

覆盖：
① SHA-256 / 大小计算与 `sha256sum -c` 兼容格式；
② 校验文件解析拒绝畸形行（防止"看着有校验其实解析不了"）；
③ provenance 记录包含 commit / tree / dirty / subject digest / 输入哈希，且 unsigned 如实为 false；
④ verify 通过 / 篡改产物失败 / 篡改输入失败 / 缺文件失败 / dirty 记判失败；
⑤ CLI：脏工作树默认拒绝出包（rc=2），--allow-dirty 才放行且记录 dirty=true；verify 通过 rc=0。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "installer"))

import provenance as prov  # noqa: E402


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """一个真实的小 git 仓库（脏/净、commit 时间都可控）。"""
    root = tmp_path / "repo"
    root.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "td011",
        "GIT_AUTHOR_EMAIL": "td011@example.invalid",
        "GIT_COMMITTER_NAME": "td011",
        "GIT_COMMITTER_EMAIL": "td011@example.invalid",
        "GIT_AUTHOR_DATE": "2026-11-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2026-11-01T00:00:00+0000",
    }
    import os

    merged = {**os.environ, **env}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=merged)
    (root / "version.json").write_text('{"version": "0.2.1"}', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "version.json"], check=True, env=merged)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True, env=merged)
    return root


def test_sha256_and_size(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello world")
    assert prov.sha256_file(f) == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    assert prov.file_size(f) == 11


def test_checksums_format_is_sha256sum_compatible(tmp_path: Path) -> None:
    a = tmp_path / "setup.exe"
    a.write_bytes(b"\x00" * 16)
    b = tmp_path / "data.7z.001"
    b.write_bytes(b"\x01" * 8)
    out = tmp_path / "SHA256SUMS.txt"
    lines = prov.write_checksums([a, b], out)

    text = out.read_text(encoding="utf-8")
    assert all("  " in line for line in lines)  # 两空格分隔，sha256sum 认
    assert text.endswith("\n")
    parsed = prov.parse_checksums(text)
    assert set(parsed) == {"setup.exe", "data.7z.001"}
    assert parsed["setup.exe"] == prov.sha256_file(a)


@pytest.mark.parametrize(
    "bad",
    [
        "abc  setup.exe",  # digest 长度不对
        "z" * 64 + " setup.exe",  # 分隔符不是两空格
        "g" * 64 + "  setup.exe",  # 非 hex
    ],
)
def test_parse_checksums_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        prov.parse_checksums(bad)


def test_build_provenance_records_source_and_inputs(tmp_path: Path, repo: Path) -> None:
    subject = tmp_path / "VOSR2Lite-Setup-v0.2.1.exe"
    subject.write_bytes(b"setup-bytes")
    data = tmp_path / "VOSR2Lite-Data.7z.001"
    data.write_bytes(b"data-bytes")

    record = prov.build_provenance(
        subject, version="0.2.1", repo=repo, inputs=[data], tool_version="3.10"
    )

    assert record["schema"] == prov.PROVENANCE_SCHEMA
    assert record["subject"]["name"] == subject.name
    assert record["subject"]["sha256"] == prov.sha256_file(subject)
    assert record["subject"]["size"] == 11
    assert record["subject"]["signed"] is False  # 无签名是事实，必须如实
    head_epoch = int(
        subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert record["subject"]["source_date_epoch"] == head_epoch  # 复现时间戳来自 HEAD 提交时间
    assert len(record["source"]["commit"]) == 40
    assert len(record["source"]["tree"]) == 40
    assert record["source"]["dirty"] is False
    assert record["inputs"][0]["name"] == data.name
    assert record["inputs"][0]["sha256"] == prov.sha256_file(data)
    assert record["builder"]["tool_version"] == "3.10"


def test_provenance_marks_dirty_worktree(tmp_path: Path, repo: Path) -> None:
    (repo / "version.json").write_text('{"version": "0.2.2"}', encoding="utf-8")
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"x")
    record = prov.build_provenance(subject, version="0.2.1", repo=repo)
    assert record["source"]["dirty"] is True


def test_verify_provenance_ok(tmp_path: Path, repo: Path) -> None:
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"bytes")
    record = prov.build_provenance(subject, version="0.2.1", repo=repo)
    assert prov.verify_provenance(record, tmp_path) == []


def test_verify_provenance_detects_tampered_subject(tmp_path: Path, repo: Path) -> None:
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"bytes")
    record = prov.build_provenance(subject, version="0.2.1", repo=repo)
    subject.write_bytes(b"tampered")
    problems = prov.verify_provenance(record, tmp_path)
    assert any("SHA-256 不符" in p for p in problems)
    assert any("大小不符" in p for p in problems)


def test_verify_provenance_detects_tampered_input(tmp_path: Path, repo: Path) -> None:
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"bytes")
    data = tmp_path / "data.7z.001"
    data.write_bytes(b"payload")
    record = prov.build_provenance(subject, version="0.2.1", repo=repo, inputs=[data])
    data.write_bytes(b"swapped")
    assert any("输入文件被改动" in p for p in prov.verify_provenance(record, tmp_path))


def test_verify_provenance_flags_dirty_source(tmp_path: Path, repo: Path) -> None:
    (repo / "version.json").write_text('{"version": "9.9.9"}', encoding="utf-8")
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"bytes")
    record = prov.build_provenance(subject, version="0.2.1", repo=repo)
    assert any("dirty" in p for p in prov.verify_provenance(record, tmp_path))


def test_cli_write_refuses_dirty_and_allow_dirty_marks_it(tmp_path: Path, repo: Path) -> None:
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"bytes")
    (repo / "version.json").write_text('{"version": "0.2.2"}', encoding="utf-8")

    rc = prov.main(["write", "--subject", str(subject), "--version", "0.2.1", "--repo", str(repo)])
    assert rc == 2  # 脏工作树拒绝出包
    assert not (tmp_path / "setup.exe.provenance.json").exists()

    rc = prov.main(
        [
            "write",
            "--subject",
            str(subject),
            "--version",
            "0.2.1",
            "--repo",
            str(repo),
            "--allow-dirty",
        ]
    )
    assert rc == 0
    record = json.loads((tmp_path / "setup.exe.provenance.json").read_text(encoding="utf-8"))
    assert record["source"]["dirty"] is True
    assert (tmp_path / "SHA256SUMS.txt").is_file()


def test_cli_verify_end_to_end(tmp_path: Path, repo: Path) -> None:
    subject = tmp_path / "setup.exe"
    subject.write_bytes(b"bytes")
    assert (
        prov.main(["write", "--subject", str(subject), "--version", "0.2.1", "--repo", str(repo)])
        == 0
    )

    rc = prov.main(
        [
            "verify",
            "--base-dir",
            str(tmp_path),
            "--provenance",
            str(tmp_path / "setup.exe.provenance.json"),
            "--checksums",
            str(tmp_path / "SHA256SUMS.txt"),
        ]
    )
    assert rc == 0

    subject.write_bytes(b"tampered")  # 篡改后必须非零
    rc = prov.main(
        [
            "verify",
            "--base-dir",
            str(tmp_path),
            "--provenance",
            str(tmp_path / "setup.exe.provenance.json"),
            "--checksums",
            str(tmp_path / "SHA256SUMS.txt"),
        ]
    )
    assert rc == 1
