# 回滚演练记录（2026-10-31）

> **性质**：发布回滚预案首次真演练（P0 整改：评估报告 §12.3 Q1 / 门禁 R4）。
> **范围**：资产回退演练（仓库外临时目录）+ 代码类回滚演练（临时克隆）+ 运行态验证。
> **不触碰面**：主仓远端、tag、Release、`D:\VOSR2Lite` 已安装实例——全程零写操作。
> **依据**：`docs/release-governance.md` §5（判定/执行/回滚后验证）。
> **留痕路径说明**：原计划放 `docs/execution/`，因该目录属本地治理目录（入库需
> `GOVERNANCE-EXCLUSIONS.md` §七 白名单登记，本次未获授权改登记表），经裁定改放
> `docs/` 根目录（`git check-ignore` 验证可入库）。

## 0. 演练环境

| 项 | 值 |
|---|---|
| 主仓 HEAD | `d97a48b`（fix(installer): 统一嵌入名为 7za.exe），工作树干净 |
| 系统 | Windows 11（10.0.26300 x64），Git Bash |
| Python | 3.12.10（`C:\Python312`，系统级；torch 2.11.0+cu128，`cuda.is_available()=True`） |
| GPU | NVIDIA GeForce RTX 5070 Ti Laptop GPU（12,227 MiB，演练前空闲 11,944 MiB） |
| 权重 | `model/VOSR2/checkpoints/ema_model.safetensors`（5,576,385,424 B）本机在位 |
| 演练目录 | `%LOCALAPPDATA%\Temp\vosr2-rollback-drill-20261031\`（主仓外） |

环境补装（均为 `requirements.txt` 已声明项，系统 Python 缺装）：`loguru 0.7.3`、`timm 1.0.30`、`fairscale`。

## 1. 发布资产回退演练（模拟「用户按上一 tag 重装」）

**远端实况**（`gh release list/view`，只读）：

| Release | 状态 | 资产 |
|---|---|---|
| v0.2.1 | Latest，published 2026-10-28T04:03:03Z | zip 2,176,085 B + SHA256SUMS.txt 88 B + sbom 12,462 B |
| v0.2.0 | **无 Release（仅 tag）** | —（缺口，见 §5） |
| v0.1.0 | published 2026-10-25T04:55:26Z | zip 547,071 B + SHA256SUMS.txt + sbom |

**命令与过程**：

```
gh release download v0.2.1 --repo ReSerendipity/VOSR2-Lite --dir <演练目录>/assets
cd assets && sha256sum -c SHA256SUMS.txt          # 逐件校验
python -I（zipfile）解压到 <演练目录>/extracted/    # testzip CRC 抽检 + 结构核对 + 版本位断言
```

**SHA-256 对照表**：

| 资产 | 期望值（SHA256SUMS.txt / 实测下载件） | 结果 |
|---|---|---|
| `VOSR2-Lite-v0.2.1.zip` | 期望 `9e629e68d67f7ee94e3829ef50012fb65c89e32fe131e58c6fe72c4d4bd3484e`<br>实测 `9e629e68d67f7ee94e3829ef50012fb65c89e32fe131e58c6fe72c4d4bd3484e` | **一致**（`sha256sum -c` OK，exit=0） |
| `SHA256SUMS.txt`（自检参考，清单不覆盖自身） | 实测 `69dc7d9b5a18513721e0cd9be6870c6518e56d4e3318f5dcafa5c43b9394b815` | 信息性记录 |
| `VOSR2-Lite-v0.2.1-sbom.cdx.json`（清单不覆盖，§4 口径仅对 zip） | 实测 `5f225804e4e645bf9a6d7833781c05d0058bf05e284b6df528a24c8642475658` | 信息性记录 |

**解压核对**：zip 173 条目，`testzip` CRC 全过；顶层目录 `VOSR2-Lite-v0.2.1/`；
关键入口齐全：`version.json`、`pyproject.toml`、`start.bat`、`config.yaml`、
`requirements.txt`、`app/integrated_app/app_server.py`、`scripts/smoke_infer.py`、
`scripts/download_model.py`、`CHANGELOG.md`、`README.md`。

**版本号读取断言（回滚目标可用性）**：解压树内
`version.json.version == pyproject.toml project.version == 0.2.1`，channel `stable`，
CHANGELOG 含 `[0.2.1] - 2026-10-28` 发版节 → **PASS**。

## 2. 代码类回滚演练（临时克隆）

```
GIT_TERMINAL_PROMPT=0 git clone https://github.com/ReSerendipity/VOSR2-Lite.git <演练目录>/repo
git checkout -b drill/rollback-20261031
git revert --no-edit c7b7b16        # chore(release): v0.2.1——最近一笔版本位/发布提交
```

克隆 HEAD 与主仓一致（`d97a48b`，两笔本地领先提交均已推送）。

**revert 冲突面（如实记录）**：

| 文件 | 状态 | 冲突原因 |
|---|---|---|
| `pyproject.toml` | 自动合并成功 | 仅 version 行，后续提交未再改 |
| `version.json` | CONFLICT (UU) | `24c213b` 在 `c7b7b16` 之后新增 `release_date` / `minimum_shell_version` / `changelog` 字段，HEAD 侧与回滚侧结构分叉 |
| `CHANGELOG.md` | CONFLICT (UU) | 冲突块第 10–68 行：`c7b7b16` 之后 `982cacd`/`d97a48b` 持续向 `[Unreleased]` 区追加，回滚侧无这些内容 |

处置：记录冲突面 → `git revert --abort`（回到干净 `d97a48b`）→ **合成提交完成干净回滚**
（保留后续提交新增的壳元数据字段，仅回退 `version` 字段行；CHANGELOG 移除
`[0.2.1]` 发版节、正文条目折回 `[Unreleased]`；字节级编辑保留 CRLF）。

**演练分支提交**：`47ac387`（`drill/rollback-20261031`，`git commit -s`，
noreply 身份 = 全局已配置的 `259081123+ReSerendipity@users.noreply.github.com`，未改 git config）。

**回滚后断言**：

- `version.json == pyproject.toml == 0.2.0` → **PASS**
- CHANGELOG 无 `[0.2.1]` 发版节，CI 修复条目折回 `[Unreleased]` → **PASS**

## 3. 克隆内质量门禁

**ruff**：`python -m ruff check .` → `All checks passed!`，**exit=0**。

**pytest**（265 collected）：

| 轮次 | 命令 | 结果 |
|---|---|---|
| 首轮 | `pytest -q` | 环境缺 `loguru` 大面积 fixture ERROR（ModuleNotFoundError），未形成有效全量计数 → 补装后重跑 |
| 二轮 | `pytest -q` | **挂起**于 `test_app_smoke.py::test_sse_endpoint_connects`（第 10 例，~25 min 无进展）；单测 `timeout 90` → exit=124 |
| 对照 | 主仓同码单测同用例 | 同样挂起（exit=124）→ **环境特异性**（Windows + 系统 Python 的 TestClient SSE 流），与 revert 无关（revert 只改版本串与 CHANGELOG；CI ubuntu 全绿，`c7b7b16` 提交记录 pytest 256 passed） |
| 三轮 | `pytest -q --tb=short --deselect tests/test_app_smoke.py::test_sse_endpoint_connects` | **执行 264 例：263 passed / 1 failed**，exit=1 |

三轮唯一失败 `test_engine_unit.py::test_project_root_resolution`：断言
`os.path.basename(PROJECT_ROOT) == "VOSR2-Lite"`，而任务模板克隆目录名为 `repo`
→ 断言命中目录名伪故障；克隆目录改名 `VOSR2-Lite` 后单测复验 **PASS**（exit=0）。
非代码回归。（注：该轮日志因缓冲未落 pytest 统计行，264/263/1 由进度点计数
264 + short summary FAILED 1 行推得。）

## 4. 运行态验证（真实推理冒烟）

前置判定：显存空闲 11,944 MiB（≥ 需求）、权重 5.2 GB 本机在位 → **满足硬跑条件**。

```
python scripts/smoke_infer.py -i outputs/A_vosr2_681aff3a.png -o <演练目录>/smoke_out -u 2
```

| 项 | 值 |
|---|---|
| 试跑 1 / 2 | exit=1（分别缺 `timm` / `fairscale`，补装，均在 requirements.txt 清单内） |
| **正式跑** | **exit=0，墙钟 55 s（含模型加载），纯推理 18.4 s** |
| 输入 | `outputs/A_vosr2_681aff3a.png`（仓内既有，16,607 B） |
| 输出 | `smoke_out/A_vosr2_681aff3a_x2.png`（640×480，107,944 B） |
| 收尾核实 | 显存回落至 **0 MiB used / 11,944 MiB free**；无 CUDA 计算进程残留；无新起 python 进程 |

**说明**：smoke 从主仓 `d97a48b` 运行（权重不入库，演练克隆内无权重）；回滚态
`47ac387` 与 `d97a48b` 的 diff 仅版本串与 CHANGELOG（运行时代码逐字一致），该跑
等效覆盖回滚目标的推理链路。§5 的 `/api/system/health` 健康接口段未执行（见 §5 未覆盖项）。

## 5. 缺口与未覆盖项（如实登记）

1. **v0.2.0 无 Release 资产**：资产级回滚目标只有 **v0.2.1 / v0.1.0**。缓解：v0.2.0
   与 v0.2.1 运行时功能一致（`c7b7b16` 提交说明），回滚到 v0.2.1 资产即等效。
2. **`/api/system/health` 健康接口验证未执行**（§5 回滚后验证后半段）：本次任务
   范围限定为 smoke_infer，未启动 Web 服务。
3. **`test_sse_endpoint_connects` 本机挂起**：Windows + 系统 Python 的 TestClient
   SSE 流特异（CI ubuntu 覆盖），演练以 `--deselect` 排除并对照验证。
4. **安装器级回滚未演练**：本次覆盖 zip 资产 + 代码 revert；NSIS 重装路径未覆盖。
5. **远端面未触碰**：draft→published 流程、tag 保护规则实况不在本次范围（避免越权写操作）。

## 6. 耗时与执行摘要

| 阶段 | 耗时 |
|---|---|
| 资产下载（2.1 MB，网络慢） | ~3 min |
| 代码回滚（revert→冲突→abort→合成提交）+ 断言 | ~15 min |
| pytest 三轮（含两次挂起排查） | ~50 min |
| 真实推理冒烟（三次：22 s + 18 s + 55 s） | ~2 min |
| **合计（含等待）** | **~1.5 h** |

**结论**：回滚预案从「仅存文档」变为「双通道验证过」——资产通道（下载/校验/解压/
版本位断言）与代码通道（revert 冲突处置/合成回滚/门禁）全链路可执行；回滚后验证
的推理冒烟真实跑通。门禁 R4 的「演练/实操记录 0」自此消除。
