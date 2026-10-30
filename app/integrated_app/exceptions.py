#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""自定义异常。"""

from __future__ import annotations


class TaskQueueFullError(Exception):
    """任务队列已满（快速拒绝语义）。"""


class ModelNotReadyError(Exception):
    """模型未加载或权重缺失。"""


class InferenceCancelled(Exception):
    """推理过程中收到取消请求（协作式中断，由进度回调抛出）。"""
