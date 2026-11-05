# 冻结评测集（2026-11-05 整改建立）

回应 2026-11-05 评估报告 P1 #1「模型质量零评测」。本目录是**冻结**的模型质量
评测集与基线，指标可复算。

## 布局

| 目录/文件 | 内容 |
|---|---|
| `hr/` | 参考图（256×256，16 张确定性合成图案，`seed=20261105`） |
| `lr/` | 引擎输入（64×64，HR 经 PIL LANCZOS 缩小 4×） |
| `bicubic_x4/` | 无模型参照基线（LR 经 PIL BICUBIC 放大 4×，逐字节可复现） |
| `baseline.json` | 实测基线（bicubic 参照 + VOSR2 引擎，含环境与逐图指标） |

## 复算

```bash
# 1) 引擎批量推理（一次加载，跑完 lr/ 全部 16 张）
python scripts/run_engine_eval.py --input-dir evalset/lr --output-dir outputs_eval

# 2) 对照参考图算 PSNR / SSIM
python scripts/eval_quality.py --ref-dir evalset/hr --out-dir outputs_eval

# 3) 参照基线复算（不需要 GPU）
python scripts/eval_quality.py --ref-dir evalset/hr --out-dir evalset/bicubic_x4
```

指标口径固定在 `scripts/eval_quality.py`（PSNR：RGB 全图 MSE；
SSIM：BT.601 亮度、11×11 高斯窗 σ=1.5、K1=0.01/K2=0.03）。**改动口径即破坏
基线可比性**，改前先重测 bicubic 与引擎两条基线并更新 `baseline.json`。

## 冻结纪律

- `hr/` 一经入库不得悄悄替换：重建必须 `scripts/build_eval_set.py --force`
  并在提交说明里写明原因；换集后必须同步重测 `baseline.json` 两条基线。
- 换真实图集：`python scripts/build_eval_set.py --from-images <目录> --force`
  （center-crop 方形后同管线），指标与基线格式不变。

## 已知局限（如实登记）

- 合成图案集≠自然图像：一步扩散模型以感知质量为目标，本集上其 PSNR 低于
  bicubic 参照（`baseline.json` 已记录解读）；基线的用途是**回归锚点**
  （引擎/权重/参数变更后复算对比），不是画质优劣证明。
- 建议属主后续用真实图集（如自摄照片）重建评测集并重测基线。
