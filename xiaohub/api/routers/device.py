"""设备控制路由"""

import asyncio
import urllib.parse

from fastapi import (
    APIRouter,
    Depends,
)

from xiaohub.api.dependencies import (
    app,
    log,
    verification,
)
from xiaohub.api.models import (
    Did,
    DidCmd,
    DidVolume,
)

router = APIRouter(dependencies=[Depends(verification)])


@router.get("/device_list")
async def device_list():
    """获取设备列表"""
    devices = await app.getalldevices()
    return {"devices": devices}


@router.get("/getvolume")
async def getvolume(did: str = ""):
    """获取音量"""
    if not app.did_exist(did):
        return {"volume": 0}

    volume = await app.get_volume(did=did)
    return {"volume": volume}


@router.get("/getplayerstatus")
async def getplayerstatus(did: str = ""):
    """获取完整播放状态

    返回小米音箱的完整播放状态，包括：
    - status: 播放状态 (0=停止, 1=播放)
    - volume: 音量
    - play_song_detail: 播放详情
        - position: 当前播放位置（毫秒）
        - duration: 总时长（毫秒）
    """
    if not app.did_exist(did):
        return {"status": 0, "volume": 0}

    return await app.get_player_status(did=did)


@router.post("/setvolume")
async def setvolume(data: DidVolume):
    """设置音量"""
    did = data.did
    volume = data.volume
    if not app.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"set_volume {did} {volume}")
    await app.set_volume(did=did, arg1=volume)
    return {"ret": "OK", "volume": volume}


@router.post("/cmd")
async def do_cmd(data: DidCmd):
    """执行命令"""
    did = data.did
    cmd = data.cmd
    log.info(f"docmd. did:{did} cmd:{cmd}")
    if not app.did_exist(did):
        return {"ret": "Did not exist"}

    if len(cmd) > 0:
        try:
            await app.cancel_all_tasks()
            task = asyncio.create_task(app.do_check_cmd(did=did, query=cmd))
            app.append_running_task(task)
        except Exception as e:
            log.warning(f"Execption {e}")
        return {"ret": "OK"}
    return {"ret": "Unknow cmd"}


@router.get("/cmdstatus")
async def cmd_status():
    """命令状态"""
    finish = await app.is_task_finish()
    if finish:
        return {"ret": "OK", "status": "finish"}
    return {"ret": "OK", "status": "running"}


@router.get("/playurl")
async def playurl(did: str, url: str):
    """播放 URL"""
    if not app.did_exist(did):
        return {"ret": "Did not exist"}
    decoded_url = urllib.parse.unquote(url)
    log.info(f"playurl did: {did} url: {decoded_url}")
    return await app.play_url(did=did, arg1=decoded_url)


@router.get("/playtts")
async def playtts(did: str, text: str):
    """播放 TTS"""
    if not app.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"tts {did} {text}")
    await app.do_tts(did=did, value=text)
    return {"ret": "OK"}


@router.post("/device/stop")
async def stop(data: Did):
    """关机"""
    did = data.did
    log.info(f"stop did:{did}")
    if not app.did_exist(did):
        return {"ret": "Did not exist"}

    try:
        await app.stop(did, "notts")
    except Exception as e:
        log.warning(f"Execption {e}")
    return {"ret": "OK"}
