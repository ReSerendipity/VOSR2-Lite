#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""flow matching 采样：一步蒸馏与多步（含 CFG / weak-condition）。

以 mixin 形式提供，宿主类需提供 ``self.vosr``（上游 VOSR 封装）。
"""

from __future__ import annotations

from app.integrated_app.engines._core import ProgressCb
from app.integrated_app.exceptions import InferenceCancelled


class SamplingMixin:
    """采样方法集合（宿主类提供 self.vosr）。"""

    def _sample(self, lq_latent, z_fea, args, cb=None):
        """flow matching 采样。

        one-step（infer_steps=1）走 VOSR.sample_onestep；多步走上游
        sample_multistep_fm（含 CFG 与 weak-condition 逻辑，保持与官方一致）。
        """
        steps = max(1, int(args.infer_steps))
        if steps == 1:
            return self.vosr.sample_onestep(self.dit, lq_latent, venc_fea=z_fea, n_steps=1)
        self._report(cb, "sample", 0.45)
        result = self.vosr.sample_multistep_fm(self.dit, lq_latent, venc_fea=z_fea, n_steps=steps)
        self._report(cb, "sample", 0.85)
        return result

    @staticmethod
    def _report(cb: ProgressCb | None, stage: str, ratio: float) -> None:
        if cb:
            try:
                cb(stage, ratio)
            except InferenceCancelled:
                raise
            except Exception:
                pass
