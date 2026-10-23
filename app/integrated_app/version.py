#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""版本号读取（version.json 为唯一事实来源）。"""

import json
import os

from app.integrated_app.config import PROJECT_ROOT


def get_app_version() -> str:
    path = os.path.join(PROJECT_ROOT, "version.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("version", "0.0.0"))
    except (OSError, json.JSONDecodeError):
        return "0.0.0"
