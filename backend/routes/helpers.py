"""统一响应格式与通用工具。"""

from __future__ import annotations

from flask import jsonify


def ok(data=None, message: str = "ok"):
    return jsonify({"code": 0, "message": message, "data": data})


def fail(message: str, code: int = 500, data=None):
    return jsonify({"code": code, "message": message, "data": data}), code
