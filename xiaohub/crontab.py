import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from xiaohub.utils.file_utils import clean_temp_dir


class Crontab:
    def __init__(self, log):
        self.log = log
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.start()

    def add_job(self, expression, job, coalesce=True):
        try:
            # 兼容旧格式：去掉 "# workday" 之类的注释，按普通 cron 执行
            base_expression = expression.split("#", 1)[0].strip()
            if "#" in expression and (
                "workday" in expression.lower() or "offday" in expression.lower()
            ):
                self.log.warning(
                    "workday/offday 定时任务已不再支持，按普通 cron 执行: %s", expression
                )
            trigger = CronTrigger.from_crontab(base_expression)

            # 添加任务配置：
            # coalesce: 如果任务错过了多次执行，是否只执行一次（默认True，适合播放类任务）
            # max_instances=30: 允许同时运行最多30个实例，支持多设备并发
            # misfire_grace_time=60: 任务延迟60秒内仍然执行
            self.scheduler.add_job(
                job, trigger, coalesce=coalesce, max_instances=30, misfire_grace_time=60
            )
        except ValueError as e:
            self.log.error(f"Invalid crontab expression {e}")
        except Exception as e:
            self.log.exception(f"Exception {e}")

    # 添加关机任务
    def add_job_stop(self, expression, app_instance, did, **kwargs):
        async def job():
            await app_instance.stop(did, "notts")

        self.add_job(expression, job)

    # 添加播放任务
    def add_job_play(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            await app_instance.play(did, arg1)

        self.add_job(expression, job)

    # 添加播放列表任务
    def add_job_play_music_list(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            await app_instance.play_music_list(did, arg1)

        self.add_job(expression, job)

    # 添加播放自定义列表任务
    def add_job_play_music_tmp_list(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            name = arg1 or "crontab_tmp_list"
            cron = kwargs["cron"]
            music_list = cron["music_list"]
            music_name = cron.get("first", "")
            ret = app_instance.music_library.play_list_update_music(name, music_list)
            if not ret:
                self.log.warning(f"crontb play_list_update_music failed name:{name}")
            await app_instance.do_play_music_list(did, name, music_name)

        self.add_job(expression, job)

    # 添加语音播放任务
    def add_job_tts(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            await app_instance.do_tts(did, arg1)

        self.add_job(expression, job)

    # 刷新播放列表任务
    def add_job_refresh_music_list(self, expression, app_instance, **kwargs):
        async def job():
            await app_instance.gen_music_list()

        self.add_job(expression, job)

    # 设置音量任务
    def add_job_set_volume(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            await app_instance.set_volume(did, arg1)

        self.add_job(expression, job)

    # 设置播放类型任务
    def add_job_set_play_type(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            play_type = int(arg1)
            await app_instance.set_play_type(did, play_type, False)

        self.add_job(expression, job)

    # 开启或关闭获取对话记录
    def add_job_set_pull_ask(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            if arg1 == "enable":
                app_instance.config.enable_pull_ask = True
            else:
                app_instance.config.enable_pull_ask = False

        self.add_job(expression, job)

    # 更新网络歌单
    def add_job_refresh_web_music_list(self, expression, app_instance, **kwargs):
        async def job():
            await app_instance.refresh_web_music_list()
            await app_instance.gen_music_list()

        self.add_job(expression, job)

    # 重新初始化
    def add_job_reinit(self, expression, app_instance, did, arg1, **kwargs):
        async def job():
            app_instance.reinit()

        self.add_job(expression, job)

    def add_job_cron(self, app_instance, cron):
        expression = cron["expression"]  # cron 计划格式
        name = cron["name"]  # stop, play, play_music_list, tts
        did = cron.get("did", "")
        arg1 = cron.get("arg1", "")
        jobname = f"add_job_{name}"
        func = getattr(self, jobname, None)
        if callable(func):
            func(expression, app_instance, did=did, arg1=arg1, cron=cron)
            self.log.info(
                f"crontab add_job_cron ok. did:{did}, name:{name}, arg1:{arg1} expression:{expression}"
            )
        else:
            self.log.error(
                f"'{self.__class__.__name__}' object has no attribute '{jobname}'"
            )

    # 清空任务
    def clear_jobs(self):
        for job in self.scheduler.get_jobs():
            try:
                job.remove()
            except Exception as e:
                self.log.exception(f"Execption {e}")

    # 重新加载计划任务
    def reload_config(self, app_instance):
        self.clear_jobs()

        crontab_json = app_instance.config.crontab_json
        if crontab_json:
            try:
                cron_list = json.loads(crontab_json)
                for cron in cron_list:
                    self.add_job_cron(app_instance, cron)
                self.log.info("crontab reload_config ok")
            except Exception as e:
                self.log.exception(f"Execption {e}")

        # 添加定时清理临时文件任务（即使没有自定义 crontab 也要生效）
        if app_instance.config.enable_auto_clean_temp:

            async def clean_temp_job():
                clean_temp_dir(app_instance.config)

            self.add_job("0 3 * * *", clean_temp_job)
            self.log.info("已添加每日凌晨3点定时清理临时文件任务")
