"""API 模块统一入口"""

from xiaohub.api.app import (
    HttpInit,
    app,
)

__all__ = ["app", "HttpInit"]
