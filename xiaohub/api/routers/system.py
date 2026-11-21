"""系统管理路由"""

import asyncio
import base64
import io
import json
import os
import shutil
import tempfile
from dataclasses import (
    asdict,
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
)
from fastapi.openapi.utils import (
    get_openapi,
)
from fastapi.responses import (
    FileResponse,
)
from qrcode.main import QRCode
from starlette.background import (
    BackgroundTask,
)

from xiaohub import (
    __version__,
)
from xiaohub.api.dependencies import (
    app,
    config,
    log,
    verification,
)
from xiaohub.integrations.qrcode_login import MiJiaAPI
from xiaohub.utils.system_utils import (
    deepcopy_data_no_sensitive_info,
    get_latest_version,
    restart_xiaohub,
    update_version,
)

router = APIRouter(dependencies=[Depends(verification)])


def _mask_services(services):
    """把 services.*.token 换成掩码，避免设置页把密钥发到浏览器。"""
    masked = {}
    for name, opts in (services or {}).items():
        item = dict(opts) if isinstance(opts, dict) else opts
        if isinstance(item, dict) and item.get("token"):
            item["token"] = "******"
        masked[name] = item
    return masked


def _restore_services_tokens(new_services, old_services):
    """保存设置时，把掩码换回原来的 token，避免误清空。"""
    if not isinstance(new_services, dict):
        return new_services
    restored = {}
    for name, opts in new_services.items():
        item = dict(opts) if isinstance(opts, dict) else opts
        old = (old_services or {}).get(name) or {}
        if isinstance(item, dict) and item.get("token") == "******":
            item["token"] = old.get("token", "")
        restored[name] = item
    return restored

auth_data_path = config.conf_path if config.conf_path else None
mi_jia_api = MiJiaAPI(auth_data_path=auth_data_path)


@router.get("/")
async def read_index():
    """首页"""
    folder = os.path.dirname(
        os.path.dirname(os.path.dirname(__file__))
    )  # app 目录
    return FileResponse(f"{folder}/static/index.html")


@router.get("/api/get_qrcode")
async def get_qrcode():
    """生成小米账号扫码登录用二维码，返回 base64 图片 URL。"""
    try:
        qrcode_data = mi_jia_api.get_qrcode()
        # 已登录时 get_qrcode 返回 False，无需扫码
        if qrcode_data is False:
            return {
                "success": True,
                "already_logged_in": True,
                "qrcode_url": "",
                "message": "已登录，无需扫码",
            }

        # 优先使用小米返回的官方二维码图片 URL，与扫码内容一致且最可靠
        print(qrcode_data)
        if qrcode_data.get("qr"):
            qrcode_url = qrcode_data["qr"]
        else:
            # 无 qr 时用 loginUrl 本地生成二维码图
            qr = QRCode(version=1, box_size=8, border=2)
            qr.add_data(qrcode_data["loginUrl"])
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("ascii")
            qrcode_url = f"data:image/png;base64,{b64}"
        # 返回二维码的同时，在后台启动 get_logint_status，不阻塞本次响应
        asyncio.create_task(get_logint_status(qrcode_data["lp"]))
        return {
            "success": True,
            "qrcode_url": qrcode_url,
            "status_url": qrcode_data.get("lp", ""),
            "expire_seconds": config.qrcode_timeout,
        }
    except Exception as e:
        log.exception("get_qrcode failed: %s", e)
        return {"success": False, "message": str(e)}


async def get_logint_status(lp: str):
    """轮询获取扫码登录状态"""
    try:
        await asyncio.to_thread(mi_jia_api.get_logint_status, lp)
    except ValueError as e:
        log.exception("get_logint_status failed: %s", e)


@router.get("/getversion")
def getversion():
    """获取版本"""
    log.debug("getversion %s", __version__)
    return {"version": __version__}


@router.get("/getsetting")
async def getsetting(need_device_list: bool = False):
    """获取设置"""
    config_data = app.getconfig()
    data = asdict(config_data)
    data["password"] = "******"
    data["httpauth_password"] = "******"
    data["services"] = _mask_services(data.get("services"))
    if need_device_list:
        device_list = await app.getalldevices()
        log.info(f"getsetting device_list: {device_list}")
        data["device_list"] = device_list
    return data


@router.post("/savesetting")
async def savesetting(request: Request):
    """保存设置"""
    try:
        data_json = await request.body()
        data = json.loads(data_json.decode("utf-8"))
        debug_data = deepcopy_data_no_sensitive_info(data)
        log.info(f"saveconfig: {debug_data}")
        config_obj = app.getconfig()
        if data.get("password") == "******" or data.get("password", "") == "":
            data["password"] = config_obj.password
        if (
            data.get("httpauth_password") == "******"
            or data.get("httpauth_password", "") == ""
        ):
            data["httpauth_password"] = config_obj.httpauth_password
        if "services" in data:
            data["services"] = _restore_services_tokens(
                data.get("services"), config_obj.services
            )
        await app.saveconfig(data)

        # 重置 HTTP 服务器配置
        from xiaohub.api.app import app as fastapi_app
        from xiaohub.api.dependencies import reset_http_server

        reset_http_server(fastapi_app)

        return "save success"
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


@router.post("/api/system/modifiysetting")
async def modifiysetting(request: Request):
    """修改部分设置"""
    try:
        data_json = await request.body()
        data = json.loads(data_json.decode("utf-8"))
        debug_data = deepcopy_data_no_sensitive_info(data)
        log.info(f"modifiysetting: {debug_data}")

        config_obj = app.getconfig()

        # 处理密码字段，如果是 ****** 或空字符串则保持原值
        if "password" in data and (
            data["password"] == "******" or data["password"] == ""
        ):
            data["password"] = config_obj.password
        if "httpauth_password" in data and (
            data["httpauth_password"] == "******" or data["httpauth_password"] == ""
        ):
            data["httpauth_password"] = config_obj.httpauth_password

        if "services" in data:
            data["services"] = _restore_services_tokens(
                data.get("services"), config_obj.services
            )

        # 检查是否有HTTP服务器相关配置被修改
        has_http_config_changed = any(
            config_obj.is_http_server_config(key) for key in data.keys()
        )

        # 更新配置
        config_obj.update_config(data)

        # 如果有HTTP配置变更，重置HTTP服务器
        if has_http_config_changed:
            from xiaohub.api.app import app as fastapi_app
            from xiaohub.api.dependencies import reset_http_server

            reset_http_server(fastapi_app)
            log.info("HTTP server configuration has been reset")

        # 保存配置到文件
        app.save_cur_config()

        return {"success": True, "message": "Configuration updated successfully"}
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        log.error(f"Error updating configuration: {err}")
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.get("/downloadlog")
def downloadlog():
    """下载日志"""
    file_path = config.log_file
    if os.path.exists(file_path):
        # 创建一个临时文件来保存日志的快照
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        try:
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, temp_file)
            temp_file.close()

            # 使用BackgroundTask在响应发送完毕后删除临时文件
            def cleanup_temp_file(tmp_file_path):
                os.remove(tmp_file_path)

            background_task = BackgroundTask(cleanup_temp_file, temp_file.name)
            return FileResponse(
                temp_file.name,
                media_type="text/plain",
                filename="app.txt",
                background=background_task,
            )
        except Exception as e:
            os.remove(temp_file.name)
            raise HTTPException(
                status_code=500, detail="Error capturing log file"
            ) from e
    else:
        return {"message": "File not found."}


@router.get("/api/debug/simulate_token_expire")
async def simulate_token_expire():
    """模拟 token 过期（调试用）

    访问 http://IP:PORT/api/debug/simulate_token_expire 即可触发，
    模拟小米服务器端 serviceToken 过期后 mi_request 内部的行为：
    1. 清空内存 token (self.token = None)
    2. 删除 .mi.token 文件
    """
    auth = app.auth_manager

    if auth.mina_service is None:
        return {"ret": "FAIL", "msg": "mina_service 为空，请先正常启动并登录"}

    if auth.mina_service.account and auth.mina_service.account.token:
        old_keys = list(auth.mina_service.account.token.keys())
    else:
        old_keys = []

    auth.mina_service.account.token = None

    token_file = os.path.join(auth.config.conf_path, ".mi.token")
    token_deleted = False
    if os.path.exists(token_file):
        os.remove(token_file)
        token_deleted = True

    return {
        "ret": "OK",
        "msg": "已模拟 token 过期",
        "old_token_keys": old_keys,
        "token_now": "None",
        "mi_token_file_deleted": token_deleted,
        "hint": "现在去网页看设备列表，观察日志中的 [AUTH] 输出",
    }


@router.get("/latestversion")
async def latest_version():
    """获取最新版本"""
    version = await get_latest_version("xiaohub")
    if version:
        return {"ret": "OK", "version": version}
    else:
        return {"ret": "Fetch version failed"}


@router.post("/updateversion")
async def updateversion(version: str = "", lite: bool = True):
    """更新版本"""
    import asyncio

    ret = await update_version(version, lite)
    if ret != "OK":
        return {"ret": ret}

    asyncio.create_task(restart_xiaohub())
    return {"ret": "OK"}


@router.get("/docs", include_in_schema=False)
async def get_swagger_documentation():
    """Swagger 文档"""
    return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")


@router.get("/redoc", include_in_schema=False)
async def get_redoc_documentation():
    """ReDoc 文档"""
    return get_redoc_html(openapi_url="/openapi.json", title="docs")


@router.get("/openapi.json", include_in_schema=False)
async def openapi():
    """OpenAPI 规范"""

    return get_openapi(title=app.title, version=app.version, routes=app.routes)
