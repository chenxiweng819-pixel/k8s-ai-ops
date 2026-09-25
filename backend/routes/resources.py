"""通用资源接口：一套 CRUD + YAML 读写，覆盖注册表里的所有资源类型。

URL 约定：
  GET    /api/resources/<res>                 列表
  POST   /api/resources/<res>                 创建（body 为完整对象）
  GET    /api/resources/<res>/<name>          详情
  PUT    /api/resources/<res>/<name>          整体替换（body 为完整对象）
  PATCH  /api/resources/<res>/<name>          局部更新（body 为 merge patch）
  DELETE /api/resources/<res>/<name>          删除
  GET    /api/resources/<res>/<name>/yaml     读取 YAML
  PUT    /api/resources/<res>/<name>/yaml     用 YAML 更新
"""

from __future__ import annotations

import yaml
from flask import Blueprint, request

from k8s import ClusterError, cluster, get_spec, object_meta
from .helpers import fail, ok

bp = Blueprint("resources", __name__, url_prefix="/api/resources")


def _ns() -> str | None:
    return request.args.get("namespace") or None


@bp.get("/registry")
def registry():
    """把支持操作的资源清单告诉前端，用于动态渲染菜单/表格列。"""
    from k8s.registry import RESOURCES

    return ok(
        [
            {
                "name": spec.name,
                "kind": spec.kind,
                "namespaced": spec.namespaced,
                "api": spec.api,
                "label": spec.label,
            }
            for spec in RESOURCES.values()
        ]
    )


@bp.get("/<res>")
def list_resources(res: str):
    if get_spec(res) is None:
        return fail(f"不支持的资源类型：{res}", 400)
    try:
        items = cluster.list_objects(res, _ns(), request.args.get("labelSelector") or None)
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    items.sort(key=lambda o: str(object_meta(o).get("name") or ""))
    return ok(items)


@bp.post("/<res>")
def create_resource(res: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("请求体必须是合法的 JSON 对象", 400)
    try:
        return ok(cluster.create_object(res, body, _ns()))
    except ClusterError as exc:
        return fail(exc.message, exc.status)


@bp.get("/<res>/<name>")
def get_resource(res: str, name: str):
    if get_spec(res) is None:
        return fail(f"不支持的资源类型：{res}", 400)
    try:
        obj = cluster.get_object(res, name, _ns())
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    if obj is None:
        return fail(f"未找到 {res}/{name}", 404)
    return ok(obj)


@bp.put("/<res>/<name>")
def replace_resource(res: str, name: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("请求体必须是合法的 JSON 对象", 400)
    try:
        return ok(cluster.replace_object(res, name, body, _ns()))
    except ClusterError as exc:
        return fail(exc.message, exc.status)


@bp.patch("/<res>/<name>")
def patch_resource(res: str, name: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("请求体必须是合法的 JSON 对象", 400)
    try:
        return ok(cluster.patch_object(res, name, body, _ns()))
    except ClusterError as exc:
        return fail(exc.message, exc.status)


@bp.delete("/<res>/<name>")
def delete_resource(res: str, name: str):
    try:
        deleted = cluster.delete_object(res, name, _ns())
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    if not deleted:
        return fail(f"未找到 {res}/{name}", 404)
    return ok({"deleted": True})


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------
@bp.get("/<res>/<name>/yaml")
def get_yaml(res: str, name: str):
    try:
        obj = cluster.get_object(res, name, _ns())
    except ClusterError as exc:
        return fail(exc.message, exc.status)
    if obj is None:
        return fail(f"未找到 {res}/{name}", 404)
    # 详情页不需要 status，去掉可让 YAML 更干净、也避免 update 时冲突
    obj.pop("status", None)
    return ok(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, default_flow_style=False))


@bp.put("/<res>/<name>/yaml")
def update_yaml(res: str, name: str):
    raw = request.get_json(silent=True) or {}
    text = raw.get("yaml")
    if not isinstance(text, str) or not text.strip():
        return fail("请提供 yaml 字段", 400)
    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return fail(f"YAML 解析失败：{exc}", 400)
    if not isinstance(body, dict):
        return fail("YAML 内容必须是一个对象", 400)

    namespace = _ns() or object_meta(body).get("namespace")
    try:
        return ok(cluster.replace_object(res, name, body, namespace))
    except ClusterError as exc:
        return fail(exc.message, exc.status)


@bp.post("/apply")
def apply_yaml():
    """kubectl apply 的简化版：不存在则创建，存在则替换。"""
    raw = request.get_json(silent=True) or {}
    text = raw.get("yaml")
    if not isinstance(text, str) or not text.strip():
        return fail("请提供 yaml 字段", 400)
    try:
        body = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return fail(f"YAML 解析失败：{exc}", 400)
    if not isinstance(body, dict):
        return fail("YAML 内容必须是一个对象", 400)

    kind = body.get("kind", "")
    # 由 kind 反查资源名
    from k8s.registry import RESOURCES

    res = next((spec.name for spec in RESOURCES.values() if spec.kind.lower() == str(kind).lower()), None)
    if res is None:
        return fail(f"暂不支持 apply 的 Kind：{kind}", 400)

    name = object_meta(body).get("name")
    namespace = object_meta(body).get("namespace") or _ns()
    if not name:
        return fail("YAML 缺少 metadata.name", 400)

    try:
        existing = cluster.get_object(res, name, namespace)
        if existing:
            return ok(cluster.replace_object(res, name, body, namespace), "已更新")
        return ok(cluster.create_object(res, body, namespace), "已创建")
    except ClusterError as exc:
        return fail(exc.message, exc.status)
