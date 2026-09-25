"""k8s 访问层。"""

from .client import ClusterClient, ClusterError, cluster
from .registry import RESOURCES, get_spec, normalize, object_meta, serialize

__all__ = [
    "ClusterClient",
    "ClusterError",
    "cluster",
    "RESOURCES",
    "get_spec",
    "normalize",
    "object_meta",
    "serialize",
]
