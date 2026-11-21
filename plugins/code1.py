async def code1(arg1):
    global log, app
    log.info(f"code1:{arg1}")
    did = app.get_cur_did()
    await app.do_tts(did, "你好，我是自定义的测试口令")

    query = app.command_handler.last_cmd.strip()
    await app.do_tts(did, f"你说的是: {query}")
