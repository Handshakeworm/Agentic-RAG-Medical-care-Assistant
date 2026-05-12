"""API 路由集中挂载点(DEV_SPEC §8.4 G1)。

`register_routers(app)` 是 G2-G6 的统一接入口。每个 G 阶段任务建好自己的 router
后,只需在本文件 import 并 `app.include_router(...)`,业务路由不直接污染
`src/api/app.py`(`create_app` 工厂保持纯净)。

当前状态(G1):本函数为空。等待:
- G2 → auth_router(注册/登录/token 校验)
- G4 → diagnosis_router(`POST /diagnose`)
- G5 → patient_router(患者 CRUD)
- G6 → admin_router(管理员接口)
- H8 → health_router(`/healthz` + `/readyz`,**G1 不实现**)
"""
from __future__ import annotations

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    """把所有业务 router 挂到 app。G2-G6 各自完成时往这里追加 import + include_router。"""
    from src.api.routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/auth", tags=["auth"])

    # G4: from src.api.routes.diagnosis import router as diagnosis_router
    #     app.include_router(diagnosis_router, tags=["diagnosis"])
    # G5: from src.api.routes.patient import router as patient_router
    #     app.include_router(patient_router, prefix="/patients", tags=["patient"])
    # G6: from src.api.routes.admin import router as admin_router
    #     app.include_router(admin_router, prefix="/admin", tags=["admin"])
    # H8: from src.api.routes.health import router as health_router
    #     app.include_router(health_router, tags=["health"])
