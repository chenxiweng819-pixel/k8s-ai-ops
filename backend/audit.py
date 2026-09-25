"""审计日志：所有写操作（含 AI 触发的）都落盘，便于事后追责。

格式为 JSONL，一行一条，字段固定，方便直接被日志系统采集。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import config

log = logging.getLogger("audit")
_lock = threading.Lock()


def record(action, resource=None, name=None, namespace=None, actor="user",
           source="api", success=True, message="", detail=None):
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "actor": actor,          # user / ai
        "source": source,        # api / ai-agent
        "action": action,        # delete / scale / restart / apply / exec ...
        "resource": resource,
        "name": name,
        "namespace": namespace,
        "success": bool(success),
        "message": (message or "")[:1000],
        "detail": detail or {},
    }
    line = json.dumps(entry, ensure_ascii=False)
    log.info(line)
    try:
        with _lock:
            with open(config.AUDIT_LOG, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError as exc:  # 审计写入失败不应影响主流程，但必须留下痕迹
        log.error("审计日志写入失败: %s", exc)
    return entry


def tail(limit=200, actor=None):
    """读取最近若干条审计记录（倒序）。"""
    if not os.path.isfile(config.AUDIT_LOG):
        return []
    try:
        with open(config.AUDIT_LOG, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if actor and entry.get("actor") != actor:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out
