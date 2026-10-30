# ADR-0005: 安装包瘦身为「不含环境与模型」，改由程序内下载管理页按需获取

- **状态**: Accepted
- **日期**: 2026-10-31
- **决策者**: 项目维护者
- **关联**: ADR-0004（Tauri v2 桌面壳）、`desktop/README.md`、`scripts/build_desktop_bundle.ps1`

## 背景与问题

ADR-0004 定下的分发形态是「安装器 + 7z 分卷数据卷」，数据卷里塞了
**应用代码 + 便携 Python 运行时（含 torch cu128）+ 全部模型权重**。实测下来这条路的成本已经压不下去：

1. **体积**：本机 `model/` 12 GB（VOSR2 5.2G + VOSR_1.4B_ms 5.2G + VAE 171M + torch_cache 1.2G），
   加上 torch 运行时约需 **8 个 1900MB 分卷**。每次发版都要重打数 GB。
2. **浪费**：`config.yaml` 登记了 5 个 checkpoint，用户通常只用其中 1 个，其余权重属于「下载即闲置」。
3. **换模型成本高**：想试另一个 checkpoint 要重下整个数据卷，而不是增量拿一份权重。
4. **配额与带宽**：几 GB 资产长期堆在 Release；若将来把模型也放自己的服务器，
   ECS 按 ¥0.8/GB 计费，个人重度使用即可烧穿余额告警线。

## 评估的备选方案

| 方案 | 结论 | 理由 |
|---|---|---|
| **瘦包 + 程序内下载页（选定）** | 采纳 | 安装包降到几十 MB；用户只下自己要的；换模型不必重装；模型走 HF/ModelScope 不占自有带宽 |
| 维持全量数据卷 | 否决 | 上述四点无解，且每次发版构建成本高 |
| 瘦包 + 用户自装系统 Python + torch | 否决 | RTX 50 系必须 cu128、国内直连 `download.pytorch.org` 常失败，普通用户基本劝退 |
| 下载页放在 GitHub Pages / 独立站点 | 否决 | 用户在网页里下完还要自己找安装目录解压；且大文件经自有站点会触发流量计费 |
| 环境包另带一份完整 Python 解释器 | 否决（实现中优化掉） | 环境包的本质只是 torch wheels（~2.5 GB），再带一份 WinPython 纯属重复，且会形成两套 runtime 不一致 |

## 决策

1. **安装包只带**：Tauri 壳 + 应用代码 + **引导解释器** `app/bootstrap/`
   （Python  embeddable/WinPython + 仅 Web 依赖，**不含 torch**）。
   实测载荷 66.8 MB → 7z **20.0 MB 单卷**（对比旧数据卷 8×1900MB）。
2. **程序内下载管理页**（`/downloads`，与首页/设置同级的后端页面）是安装环境与模型的唯一入口：
   - **运行环境**：预构建的 torch/torchvision cu128 wheels 包（自己托管在 Release，
     因为从 HF 取 torch 不便、国内直连 PyTorch 源常失败）。
     离线装进 `bootstrap/` **自身的** site-packages（目录名不变），不另起一份解释器。
   - **模型权重**：不自己托管，复用 `scripts/download_model.py` 的
     hf-mirror → HuggingFace → ModelScope 多源回退与 `.part` + Range 断点续传。
3. **就绪判定不看目录名**：装完环境后 `bootstrap/` 名字没变，壳靠
   `bootstrap/Lib/site-packages/torch` 是否存在判定「已是完整运行时」，据此把入口导回首页。
   比启动子进程 `import torch`（数秒）便宜得多。
4. **不保留全量包构建**（用户裁定）。瘦包是唯一分发形态；离线/内网场景由用户自行预置
   `app/model/` 与 `app/bootstrap/` 的 torch。
5. **环境包清单随应用分发**：`app/downloads_catalog.json` 记录版本/base_url/分卷 sha256。
   换环境包必须同步发新版应用，否则页面找不到下载地址。

## 为什么这条路走得通（关键实测）

原以为「环境没装 → 后端起不来 → 下载页打不开」是个死结。实测推翻了这个假设：

- 后端在**无 torch** 环境下能完整启动：`create_app()` 成功，
  `/`、`/settings`、`/history`、`/api/system/health` 全 200。
- 原因是 `app_server` 已有**路由级容错**：单个路由模块导入失败
  （`routes.restore.upload` 缺 torch）只打一行日志并跳过，不拖垮整个 app。

由此得出一条硬约束，写进相关文件头：**下载管理相关模块禁止顶层 `import torch`**，
否则用户会陷入「装了软件却没法装环境」的死局。

## 后果与新约束

- **首次使用链路变长**：安装 → 装环境（数 GB）→ 装模型（数 GB）→ 重启 → 可用。
  因此引导态必须明确提示，而不是让用户面对一个功能残缺的首页。
- **三处 `bootstrap` 耦合**：`python_process.rs::detect_python_mode`、
  后端 `_runtime_dir()`、打包脚本 `$BootstrapDirName`。改名必须三处同步。
- **环境包版本兼容**：`downloads_catalog.json` 声明 `min_app_version`，
  状态接口据此算 `compatible`，避免老应用装上新环境包。
- **两个归档根约定不变**（ADR-0004 已确立）：数据卷根为 `app/`；增量包根为 `app/` 的内容。
