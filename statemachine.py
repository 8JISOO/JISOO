import schserver

import os
import httpx
import logging
import asyncio
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from http.cookiejar import LWPCookieJar
import re
from typing import Tuple, Optional
from transitions.extensions.asyncio import AsyncMachine
import functools
import json

import setlogger

# logging.basicConfig(level=logging.WARN)

QIANG_TITLE = "通识拓展"

QIANG_SUCCESS_RECORDS_JSON = "抢课成功记录表.json"

STD_COUNT_LOG = open(
    "人数变化日志.log",
    mode='a',
    encoding='utf-8',
    newline='\n'
)

COURSE_INFO_LOG = open(
    "课程信息日志.log",
    mode='a',
    encoding='utf-8',
    newline='\n'
)

# 全局的 STD_COUNT 用来在获取到最新的人数以后其他用户能第一时间获取到最新人数
STD_COUNT = {}
ALL_ID = []
QUERY_STD_COUNT_URL = None


def auto_gen_courses_list(
        full_course_list: list,
        yishu_only: bool,
) -> list:
    def is_want_course(course: dict) -> bool:
        is_online = "超星" in course["teachers"] or "智慧树" in course["teachers"]
        high_credits = course["credits"] >= 2.0  # TODO 也许有的人并不在意学分而是只要有就行?所以可以这样: 把2学分排前面, 1学分排后面, 优先抢2学分的
        is_yishu = "艺术类" in course["name"]
        if yishu_only:
            return is_online and high_credits and is_yishu
        else:
            return is_online and high_credits

    auto_courses_list = [course["id"] for course in full_course_list if is_want_course(course)]
    return auto_courses_list


STATES = [
    "INIT",  # 系统初始化，准备启动流程
    "RECOVER_LOGIN",  # 尝试恢复登录
    "LOGIN",  # 执行登录操作（支持重试）
    "PREPARE",  # 登录后检查是否登录成功并获取各种信息
    "READY",  # 决定调度策略
    "WATCH_COUNT",  # 持续监控人数看是否有可抢占的课程
    "QIANG",  # 执行课程抢占操作
    "SUCCESS",  # 所有目标课程抢占完成
    "ERROR",  #
]

TRANSITIONS = [
    {"trigger": "start", "source": "INIT", "dest": "RECOVER_LOGIN"},
    {"trigger": "after_fatal_retry", "source": "*", "dest": "RECOVER_LOGIN"},
    # 恢复登录部分
    {"trigger": "recover_login_ok", "source": "RECOVER_LOGIN", "dest": "PREPARE"},
    {"trigger": "new_login", "source": "RECOVER_LOGIN", "dest": "LOGIN"},
    # 登录部分
    {"trigger": "re_login", "source": "*", "dest": "LOGIN"},
    {"trigger": "login_ok", "source": "LOGIN", "dest": "PREPARE"},
    {"trigger": "login_retry", "source": "LOGIN", "dest": "LOGIN"},
    # 准备部分
    {"trigger": "prepare_done", "source": "PREPARE", "dest": "READY"},
    # 开始
    {"trigger": "to_watch_count", "source": "READY", "dest": "WATCH_COUNT"},
    {"trigger": "to_qiang", "source": "READY", "dest": "QIANG"},
    # 看人数
    {"trigger": "continue_watch", "source": "WATCH_COUNT", "dest": "WATCH_COUNT"},
    # 抢课
    {"trigger": "continue_qiang", "source": "QIANG", "dest": "QIANG"},
    {"trigger": "fin_qiang_to_watch", "source": "QIANG", "dest": "WATCH_COUNT"},
    # 成功
    {"trigger": "qiang_success", "source": "QIANG", "dest": "SUCCESS"},
    {"trigger": "continue_qiang", "source": "SUCCESS", "dest": "QIANG"},
    # 无法恢复错误
    {"trigger": "fatal", "source": "*", "dest": "ERROR"},
]


def catch_to_fatal():
    """装饰器：捕获异常并触发 model.fatal()"""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            try:
                return await fn(self, *args, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                logging.error(f"[{self.username}] 服务器连接超时或连接异常, 尝试延长超时时间 {e}", exc_info=True)
                self.handle_timeout()  # 自定义超时处理方法
                await self.after_fatal_retry()  # TO PRE_LOGIN
                return
            except httpx.HTTPError as e:
                logging.error(f"[{self.username}] {e}", exc_info=True)
                if e.response.is_redirect:
                    logging.error(f"[{self.username}] 发生重定向异常, 尝试重新登录 {str(e)}")
                    await self.re_login()  # TO LOGIN
                    return
                if e.response.is_server_error:
                    logging.error(f"[{self.username}] 服务器错误, 尝试从恢复登录继续 {str(e)}")
                    await self.after_fatal_retry()  # TO PRE_LOGIN
                    return
                if e.response.is_client_error:
                    pass
                logging.critical(f"[{self.username}] {e}", exc_info=True)
                await self.fatal()
                return
            except Exception as e:
                logging.critical(f"[{self.username}] {e}", exc_info=True)
                await self.fatal()
                return

        return wrapper

    return deco


class QiangModel:
    def __init__(self,
                 username: str,
                 password: str,
                 will_qiang: bool,
                 want_auto_choose_target_courses: Optional[bool] = True,
                 yishu_only: Optional[bool] = False,
                 target_course_no_list: Optional[list] = [],
                 # sem: Optional[asyncio.Semaphore] = None,  # AI: 未来如果需要扩展锁
                 ):
        # 学号密码
        self.username = username
        self.password = password

        # will_QIANG 
        # 决定了是否需要抢课, 
        # 如果是纯盯着人数的话就不用抢课了纯打辅助
        # otherwise
        # 决定了是否需要监视人数, 
        # 如果是选修课这种所有用户共用一个人数列表的情况, 
        # 只要有辅助就可以闷着头抢不用管其他用户
        self.will_QIANG = will_qiang

        # 自动抢还是手动抢
        self.want_auto_choose_target_courses = want_auto_choose_target_courses
        # 自动抢课要不要艺术
        self.yishu_ONLY = yishu_only
        # 手动指定抢课的代码列表
        self.target_course_id_list = []
        self.target_course_no_list = target_course_no_list

        # 认证需要记录的状态
        self.captcha_uid = ""
        self.ticket = None

        # 抢课需要记录的信息
        self.start_time = datetime.now()
        self.profile_id = None
        self.id_to_info = {}

        # 抢到的课
        self.owned_courses = []

        # cookie 用于恢复登录
        self.client = None
        self.cookies_file = f'cookies/{self.username}.cookies.lwp'
        self.cookiejar = LWPCookieJar(filename=self.cookies_file)

        self.timeout_seconds = 5  # 可控超时时间, 如果在之后超时了可以适当延长超时时间

        # self.sem = sem  # 未来如果需要锁

        # AI:
        # 简单重试计数器，按需要调整或改成指数回退
        # self.max_login_retries = 3
        # self.login_retries = 0
        # self.max_grab_retries = 10
        # self.grab_attempts = 0

        # AI:
        # 构建 AsyncMachine，把自己作为 model，这样 trigger 成为实例 coroutine 方法
        self.machine = AsyncMachine(
            model=self,
            states=STATES,
            transitions=TRANSITIONS,
            initial="INIT",
            queued=True  # 保证状态切换按序列化（推荐 async 场景）
        )

    # ---------- 状态进入回调（AsyncMachine 会 await） ----------
    async def on_enter_RECOVER_LOGIN(self):
        logging.info(f"[{self.username}] on_enter_RECOVER_LOGIN")
        # 我们尝试恢复登录, 通过判断是否存在cookie并直接加载完成登录
        # 后续会在 PREPARE 状态验证是否有效,
        is_recover_login = False
        if os.path.exists(self.cookies_file):
            try:
                self.cookiejar.load(ignore_discard=True, ignore_expires=True)
                is_recover_login = True
            except Exception as e:
                logging.warning(f"[{self.username}] 加载Cookie时出错")
                os.remove(self.cookies_file)

        # 给每个用户一个clinet方便身份认证cookie之类的, 人数不是很多也暂时也不用优化tcp调度
        self.client = httpx.AsyncClient(
            # 启用代理以抓包, 但是抢课的时候不要用代理因为会影响tcp连接池的调度
            # proxy="http://127.0.0.1:8083",

            # 禁用 ssl 证书验证, 这样的话抓包排查就可以忽略证书问题避免报错了
            verify=False,

            # 强制用 IPv4, 教务系统用 IPv6 会很慢 (2024年测试)
            # 方法参考了 https://github.com/encode/httpx/discussions/2664
            # https://github.com/encode/httpx/pull/3052
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),

            headers={
                # chrome Mac UA
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.79 Safari/537.36',
                "X-Requested-With": "XMLHttpRequest",  # 用 ajax 省点大小
            },
            cookies=self.cookiejar,
            timeout=5
        )
        # client.hooks['response'].append(logRoundtrip)

        if is_recover_login:
            logging.warning(f"[{self.username}] 尝试恢复登录")
            await self.recover_login_ok()  # TO PREPARE
        else:
            logging.warning(f"[{self.username}] 需要新登录")
            await self.new_login()  # TO LOGIN

    @catch_to_fatal()
    async def on_enter_LOGIN(self):
        logging.info(f"[{self.username}] on_enter_LOGIN")
        # 进入这个状态说明登录肯定失败了或者出错了
        # 这种情况就应该全新登录并把现有的cookie清干净
        if os.path.exists(self.cookies_file):
            os.remove(self.cookies_file)

        logging.warning(f"[{self.username}] 开始登录")

        self.captcha_uid, captcha_code = await schserver.sfrz_captcha(self.client, self.captcha_uid)

        login_success, ticket_or_errcode = await schserver.sfrz_login(self.client, self.captcha_uid, captcha_code,
                                                                      self.username, self.password)
        if not login_success:
            errcode = ticket_or_errcode
            if errcode == "CODEFALSE":
                logging.warning(f"[{self.username}] 验证码错误, 重新尝试登录")
                await self.login_retry()  # TO LOGIN
                return
            if errcode == "PASSERROR":
                logging.fatal(f"[{self.username}] 密码错误")
                exit()
            if errcode == "USERLOCK":
                logging.fatal(f"[{self.username}] 已被锁定")
                exit()
            if errcode == "NOUSER":
                logging.fatal(f"[{self.username}] 用户不存在")
                exit()
            else:
                logging.warning(f"[{self.username}] 登录遇到其他错误 {errcode}")
                raise "需要处理其他登录失败情况"

        self.ticket = ticket_or_errcode
        await self.login_ok()  # TO PREPARE

    @catch_to_fatal()
    async def on_enter_PREPARE(self):
        logging.info(f"[{self.username}] on_enter_PREPARE")

        resp = await schserver.jwgl_info_page(self.client, self.ticket)  # 如果第一次运行尝试恢复登录, 那么 ticket 自然是 None

        if self.will_QIANG:
            # 检查是否登录成功, 并获取 profile_id 和 开始时间
            self.profile_id, self.start_time = from_info_page_get_profile_id_and_start_time(resp)
            logging.info(f"[{self.username}] profile_id start_time {self.profile_id, self.start_time}")

            # 从 default page 获取查询人数url
            resp = await schserver.jwgl_default_page(self.client, profile_id=self.profile_id)
            queryStdCount_url, teach_class_map = from_default_page_get_queryStdCount_url(resp)
            logging.warning(f"[{self.username}] queryStdCount_url {queryStdCount_url}")

            global QUERY_STD_COUNT_URL
            if QUERY_STD_COUNT_URL is None:
                QUERY_STD_COUNT_URL = queryStdCount_url
            else:
                assert QUERY_STD_COUNT_URL == queryStdCount_url

            # 生成课程信息映射表,
            # 这样我们就可以以id为内部标准了,
            # 在打印消息时转换为 name
            all_course_list = await schserver.jwgl_course_info(self.client, profile_id=self.profile_id)
            self.id_to_info = {str(course["id"]): course for course in all_course_list}

            for key, value in teach_class_map.items():
                # 只处理在course_info中存在的key
                if key in self.id_to_info:
                    self.id_to_info[key]['teach_class'] = value

            json.dump(self.id_to_info, COURSE_INFO_LOG, ensure_ascii=False)
            COURSE_INFO_LOG.write('\n')
            COURSE_INFO_LOG.flush()  # 强制刷新缓冲区

            logging.debug(f"[{self.username}] id_to_info {self.id_to_info}")

            # 使用list[:] = ...的切片赋值方式，这样可以在原地修改原列表，而不是创建一个新列表。
            ALL_ID[:] = list(set(ALL_ID) | set(self.id_to_info))

            # 确定用户抢课目标课程列表
            if self.want_auto_choose_target_courses:
                self.target_course_id_list = auto_gen_courses_list(all_course_list, True)
            else:
                no_to_id = {course["no"]: course["id"] for course in all_course_list}
                self.target_course_id_list = [str(no_to_id[no]) for no in self.target_course_no_list]
            logging.warning(
                f"[{self.username}] 准备抢的课程列表 {[self.id_to_info[_id]['name'] for _id in self.target_course_id_list]}")

            # 开始恢复owned list
            if os.path.exists(QIANG_SUCCESS_RECORDS_JSON):
                with open(QIANG_SUCCESS_RECORDS_JSON, 'r', encoding='utf-8') as f:
                    try:
                        data = json.load(f)
                        if self.username in data:
                            self.owned_courses = list(data[self.username].keys())
                            logging.warning(f"[{self.username}] 已恢复成功记录: {self.owned_courses}")
                    except json.JSONDecodeError:
                        # 如果文件损坏，初始化空字典
                        json.dump({}, f, ensure_ascii=False)

        # 保存cookie
        os.makedirs("cookies", exist_ok=True)
        self.cookiejar.save(ignore_discard=True, ignore_expires=True)

        logging.warning(f"[{self.username}] 登录完成")

        await self.prepare_done()  # TO READY

    @catch_to_fatal()
    async def on_enter_READY(self):
        logging.info(f"[{self.username}] on_enter_READY")

        if self.will_QIANG:
            # 纯抢课, 由助攻来监视人数
            await self.to_qiang()  # TO QIANG
            return
        else:
            # 助攻, 纯监视人数, 并把人数共享给其他人
            await self.to_watch_count()
            return

    @catch_to_fatal()
    async def on_enter_WATCH_COUNT(self):
        logging.warning(f"[{self.username}] 尝试获取人数")

        if QUERY_STD_COUNT_URL is not None:
            req_time = datetime.now().isoformat()
            std_count = await schserver.jwgl_std_count(self.client, QUERY_STD_COUNT_URL)

            STD_COUNT.clear()
            STD_COUNT.update(std_count)

            json.dump({
                "timestamp": req_time,
                "countdata": {k: v for k, v in std_count.items() if k in ALL_ID},
            }, STD_COUNT_LOG, ensure_ascii=False)
            STD_COUNT_LOG.write('\n')
            STD_COUNT_LOG.flush()  # 强制刷新缓冲区

            logging.warning(f"[{self.username}] 获取人数 成功")

        await self.continue_watch()  # TO WATCH COUNT

    async def on_exit_WATCH_COUNT(self):
        logging.info(f"[{self.username}] on_exit_WATCH_COUNT")
        await asyncio.sleep(0.5)

    # @catch_to_fatal()
    # async def on_enter_QIANG(self):
    #     #logging.warning(f"[{self.username}] 进入 QIANG 状态，目标课程: {self.target_course_id_list}，已抢: {self.owned_courses}")
    #     logging.info(f"[{self.username}] qiang")
    #     #logging.warning(f"[{self.username}] 仍在运行中，当前时间 {datetime.now()}")
    #
    #
    #     couse_can_qiang_list = fuck(self.target_course_id_list, self.owned_courses)
    #     if not couse_can_qiang_list:
    #         # 在选课开始前，强制打一枪探测
    #
    #             test_id = self.target_course_id_list[0]
    #             logging.warning(f"[{self.username}] 测试发起抢课请求: {self.id_to_info[test_id]['name']}")
    #             msg = await schserver.qiang(self.client, test_id, self.profile_id)
    #             logging.warning(f"[{self.username}] 抢课返回结果: {msg}")
    #
    #     if couse_can_qiang_list:
    #         logging.warning(f"[{self.username}] >>> 进入可抢逻辑，课程列表: {couse_can_qiang_list}")
    #         logging.warning(f"[{self.username}] 仍在运行中，当前时间 {datetime.now()}")
    #         couse_can_qiang = couse_can_qiang_list[0]  # 抢课程列表里最靠前的
    #
    #         logging.warning(f"[{self.username}] 尝试抢 {self.id_to_info[couse_can_qiang]['name']}")
    #         logging.warning(f"[{self.username}] 正在发起抢课请求: {self.id_to_info[couse_can_qiang]['name']}")
    #         msg = await schserver.qiang(self.client, couse_can_qiang, self.profile_id)
    #         logging.warning(f"[{self.username}] {msg}")
    #         logging.warning(f"[{self.username}] 抢课返回结果: {msg}")
    #         if "人数已满" in msg:
    #             pass
    #         elif "登录" in msg:  # TODO 有这个情况吗?
    #             await self.re_login()
    #             return
    #         # elif "过快" in msg:  # TODO 速度控制
    #         #     self.delay += 0.2
    #         elif "成功" in msg or "你已经选过" in msg:
    #             if couse_can_qiang not in self.owned_courses:
    #                 self.owned_courses.append(couse_can_qiang)
    #                 await self.qiang_success()  # TO SUCCESS
    #             return
    #         elif "本轮次已选课程数量已经达到上限" in msg:
    #             self.will_QIANG = False  # 就不用抢了,一直盯着人数就行
    #             await self.fin_qiang_to_watch()  # TO WATCH_COUNT
    #             return
    #         elif "当前选课不开放" in msg:
    #             logging.warning(f"[{self.username}] {msg}")  # 打印完整返回，方便调试
    #             # if datetime.now() < self.start_time - timedelta(minutes=3):
    #             #     await asyncio.sleep(10)
    #     # else:
    #     #     # ✅ 新增：条件不满足时也打印日志
    #     #     for cid in self.target_course_id_list:
    #     #         course = self.id_to_info.get(cid, {})
    #     #         name = course.get("name", "未知课程")
    #     #         sc = STD_COUNT.get(cid, {}).get("sc", "?")
    #     #         lc = STD_COUNT.get(cid, {}).get("lc", "?")
    #     #         logging.warning(f"[{self.username}] 未能抢 {name} (人数 {sc}/{lc})，等待机会...")
    #     await self.continue_qiang()  # TO QIANG
    @catch_to_fatal()
    async def on_enter_QIANG(self):
        logging.info(f"[{self.username}] 进入 QIANG 状态，准备执行抢课逻辑")

        couse_can_qiang_list = fuck(self.target_course_id_list, self.owned_courses)

        # -----------------
        # ✅ 总是发请求
        # -----------------
        if couse_can_qiang_list:
            logging.info(f"[{self.username}] 进入 QIANG 状态，准备执行抢课逻辑")
            couse_can_qiang = couse_can_qiang_list[0]

            logging.warning(f"[{self.username}] >>> 有可抢课程，尝试抢 {self.id_to_info[couse_can_qiang]['name']}")
        else:
            # couse_can_qiang = self.target_course_id_list[0]
            # logging.warning(
            #     f"[{self.username}] >>> 没有可抢课程，仍然发起请求 {self.id_to_info[couse_can_qiang]['name']}")
            for cid in self.target_course_id_list:
                logging.warning(f"[{self.username}] >>> 没有可抢课程，仍然发起请求 {self.id_to_info[cid]['name']}")
                msg = await schserver.qiang(self.client, cid, self.profile_id)
                logging.warning(f"[{self.username}] 抢课返回结果: {msg}")

                # 如果返回提示有意义，可以在这里提前 break，比如遇到“成功”或“已经选过”就停
                if "成功" in msg or "你已经选过" in msg:
                    if cid not in self.owned_courses:
                        self.owned_courses.append(cid)
                        await self.qiang_success()
                    return
                # if "请不要过快点击" in msg:
                #     logging.warning(f"[{self.username}] 检测到过快点击，延迟 1 秒")
                #     await asyncio.sleep(1)

        # 发起请求
        msg = await schserver.qiang(self.client, couse_can_qiang, self.profile_id)
        logging.warning(f"[{self.username}] 抢课返回结果: {msg}")

        # -----------------
        # ✅ 根据返回结果处理
        # -----------------
        if "人数已满" in msg:
            pass
        elif "登录" in msg:
            await self.re_login()
            return
        elif "成功" in msg or "你已经选过" in msg:
            if couse_can_qiang not in self.owned_courses:
                self.owned_courses.append(couse_can_qiang)
                await self.qiang_success()
            return
        elif "本轮次已选课程数量已经达到上限" in msg:
            self.will_QIANG = False
            await self.fin_qiang_to_watch()
            return
        elif "当前选课不开放" in msg:
            logging.warning(f"[{self.username}] 返回提示：当前选课不开放")

        # -----------------
        # ✅ 循环继续
        # -----------------
        await self.continue_qiang()

    async def on_exit_QIANG(self):
        logging.info(f"[{self.username}] on_exit_QIANG")
        await asyncio.sleep(0.5)

    async def on_enter_SUCCESS(self):
        logging.warning(f"[{self.username}] on_exit_SUCCESS")

        # 确保文件存在，如果不存在则创建空文件
        if not os.path.exists(QIANG_SUCCESS_RECORDS_JSON):
            with open(QIANG_SUCCESS_RECORDS_JSON, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False)

        # 读取现有JSON数据
        with open(QIANG_SUCCESS_RECORDS_JSON, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                # 如果文件损坏，初始化空字典
                data = {}

        owned_courses_dict = {
            course: f"{self.id_to_info[course]['name']}  {self.id_to_info[course]['teach_class']}"
            for course in self.owned_courses
        }

        if self.username in data:
            # 合并两个字典，若有重复的课程id，以owned_courses_dict为准
            data[self.username] = {**data[self.username], **owned_courses_dict}
        else:
            data[self.username] = owned_courses_dict

        # 保存更新后的数据
        with open(QIANG_SUCCESS_RECORDS_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        await self.continue_qiang()  # TO QIANG

    async def on_enter_ERROR(self):
        logging.warning(f"[{self.username}] 状态 ERROR（发生致命错误）")

        # cookie 清干净, 等一小会后尝试重新开始
        if os.path.exists(self.cookies_file):
            os.remove(self.cookies_file)

        await asyncio.sleep(1)
        await self.after_fatal_retry()  # TO PRE_LOGIN

    def handle_timeout(self):
        self.timeout_seconds = min(self.timeout_seconds + 5, 20)
        self.client.timeout = httpx.Timeout(self.timeout_seconds)
        logging.warning(f"[{self.username}] 超时时间已调整为: {self.timeout_seconds}秒")


def from_info_page_get_profile_id_and_start_time(
        resp: str,
) -> Tuple[str, datetime]:
    # <div style="text-align:center;width:100%;margin-auto;" class="ajax_container">
    #     <div id="electIndexNotice0" style="width:80%;text-align:center;margin:2px auto;padding:5px;clear:both;font-size:14px;text-align:left;padding:20px 1%;" class="ajax_container">
    #         <h2 style="text-align:center;margin-top:5px;margin-bottom:2px;">2025-2026学年1学期 23级计算机科学与技术专业方向选修课</h2>
    #         <div style="float:left;width:100%;margin-bottom:6px;text-align:center;">
    #             选课轮次 3 开放于 2025-06-16 09:00
    #             <br/> 选课开放时间: 2025-07-02 10:00 - 2025-07-07 17:40
    #             <br> 退课开放时间: 2025-07-02 10:00 - 2025-07-07 17:40
    #             <br>
    #         </div>
    #         <div style="float:left;left;width:100%;vertical-align:top;">
    #             <h3 style="clear:left;font-size:15px;float:left;margin-left:20px;margin-top:0px;width:20%">选课限制</h3>
    #             <div style="float:left;margin-left:20px;width:70%">
    #                 不开放重修（不要和只开放重修一起使用） ,&nbsp; 教学班选课限制 ,&nbsp; 选课时间冲突检查 ,&nbsp; 检查本轮次已选课程门数上限
    #                 <br> 指定课程不能退课 ,&nbsp; 只能退当前轮次选的课
    #                 <br>
    #             </div>
    #             <h3 style="clear:left;font-size:15px;float:left;margin-left:20px;margin-top:5px;width:20%">注意事项</h3>
    #             <div style="float:left;margin-left:20px;width:70%;table-layout:fixed; word-break: break-all; overflow:hidden; ">请同学们查看本人计划完成情况后，按通知要求选课</div>
    #         </div>
    #         <div style="clear:both;text-align:center;width:100%;">
    #             <a href="/eams/stdElectCourse!defaultPage.action?electionProfile.id=1469" style="margin:auto;font-size:16px;" target="elect_page">进入选课>>>></a>
    #         </div>
    #     </div>
    #
    #     <img height="2" width="100%" alt="keyline" style="clear:both;margin-top:4px;" src="/eams/static/themes/default/icons/16x16/actions/keyline.png">
    #     <div id="electIndexNotice1" style="width:80%;text-align:center;margin:2px auto;padding:5px;clear:both;font-size:14px;text-align:left;padding:20px 1%;" class="ajax_container">
    #         <h2 style="text-align:center;margin-top:5px;margin-bottom:2px;">2025-2026学年1学期 容器化部署选课压力测试-勿删</h2>
    #         <div style="float:left;width:100%;margin-bottom:6px;text-align:center;">
    #             选课轮次 99 开放于 2025-08-26 07:00
    #             <br/> 选课开放时间: 2025-08-26 08:00 - 2025-08-27 10:54
    #             <br> 退课开放时间: 2025-08-26 08:00 - 2025-08-27 10:54
    #             <br>
    #         </div>
    #         <div style="float:left;left;width:100%;vertical-align:top;">
    #             <h3 style="clear:left;font-size:15px;float:left;margin-left:20px;margin-top:0px;width:20%">选课限制</h3>
    #             <div style="float:left;margin-left:20px;width:70%">
    #                 指定课程不能退课 ,&nbsp; 只能退当前轮次选的课
    #                 <br>
    #             </div>
    #             <h3 style="clear:left;font-size:15px;float:left;margin-left:20px;margin-top:5px;width:20%">注意事项</h3>
    #             <div style="float:left;margin-left:20px;width:70%;table-layout:fixed; word-break: break-all; overflow:hidden; ">容器化部署选课压力测试-请勿操作</div>
    #         </div>
    #         <div style="clear:both;text-align:center;width:100%;">
    #             <a href="/eams/stdElectCourse!defaultPage.action?electionProfile.id=1509" style="margin:auto;font-size:16px;" target="elect_page">进入选课>>>></a>
    #         </div>
    #     </div>
    #
    # </div>
    soup = BeautifulSoup(resp, 'html.parser')

    notice_divs = soup.find_all('div', id=lambda x: x and x.startswith('electIndexNotice'))

    # if not notice_divs:
    #     logging.warning(f"[{datetime.now().isoformat()}] 登录失败因为 a.herf 没找到 {resp}")
    #     return False, "", datetime.now()

    # 寻找标题包含"体育"的div
    target_div = None
    for div in notice_divs:
        # 获取div中的标题标签
        title_tag = div.find('h2')
        if title_tag and QIANG_TITLE in title_tag.get_text():
            target_div = div
            break  # 找到第一个匹配的就停止
    # 检查是否找到目标div
    # if not target_div:
    #     logging.warning(f"[{datetime.now().isoformat()}] 未找到标题包含'体育'的选课通知div")
    #     return False, "", datetime.now()

    a_tags = target_div.find_all('a')
    # if not a_tags:
    #     logging.warning(f"[{datetime.now().isoformat()}] div中未找到a标签 {target_div}")
    #     return False, "", datetime.now()

    # 获取最后一个a标签的href（通常是"进入选课"链接）
    href = a_tags[-1].get('href')  # "/eams/stdElectCourse!defaultPage.action?electionProfile.id=1469"
    params = parse_qs(urlparse(href).query)
    # if 'electionProfile.id' not in params:
    #     logging.warning(f"[{datetime.now().isoformat()}] 未从href中找到electionProfile.id {href}")
    #     return False, "", datetime.now()
    profile_id = params['electionProfile.id'][0]

    match = re.search(
        r'选课开放时间: (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) - \d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        target_div.get_text()
    )
    # if not match:
    #     logging.warning(f"[{datetime.now().isoformat()}] 未找到选课开放时间 {target_div}")
    #     return False, "", datetime.now()
    date_str = f"{match.group(1)} {match.group(2)}"
    start_time = datetime.strptime(date_str, '%Y-%m-%d %H:%M')

    return profile_id, start_time


def from_default_page_get_queryStdCount_url(
        resp: str,
) -> Tuple[str, dict]:
    # html at module/jwgl_default_page.html
    soup = BeautifulSoup(resp, 'html.parser')
    qr_script = soup.find('script', id='qr_script')
    queryStdCount_url = qr_script['src']

    # 正则表达式匹配teachClassMap的定义和所有键值对
    # 匹配模式：teachClassMap["键"] = "值";
    pattern = r'teachClassMap\["(\w+)"\]\s*=\s*"([^"]+)"'

    # 查找所有匹配项
    matches = re.findall(pattern, resp)

    # 转换为字典
    teach_class_map = {}
    for key, value in matches:
        teach_class_map[key] = value

    return queryStdCount_url, teach_class_map


def fuck(
        target_course_id_list: list,
        owned_courses: list,
) -> list:
    # if not STD_COUNT or profile_id not in STD_COUNT or not STD_COUNT[profile_id]:
    if not STD_COUNT:
        return []
    # count = STD_COUNT[profile_id]
    # count = STD_COUNT

    not_owned_courses = list(
        set(target_course_id_list) - set(owned_courses)
    )

    valid_course_ids = [cid for cid in not_owned_courses if STD_COUNT[cid]['sc'] < STD_COUNT[cid]['lc']]
    return valid_course_ids
# def fuck(
#     target_course_id_list: list,
#     owned_courses: list,
# id_to_info=None) -> list:
#     if not STD_COUNT:
#         logging.warning("STD_COUNT 暂无数据，返回空列表")
#         return []
#
#     not_owned_courses = list(set(target_course_id_list) - set(owned_courses))
#     valid_course_ids = []
#
#     for cid in not_owned_courses:
#         sc = STD_COUNT.get(cid, {}).get("sc", "?")
#         lc = STD_COUNT.get(cid, {}).get("lc", "?")
#         name = (
#             id_to_info[cid]["name"]
#             if "id_to_info" in globals() and cid in id_to_info
#             else str(cid)
#         )
#         logging.warning(f"检测课程 {name} (id={cid}) 人数 {sc}/{lc}")
#
#         # 只有当人数未满才加入
#         if isinstance(sc, int) and isinstance(lc, int) and sc < lc:
#             valid_course_ids.append(cid)
#
#     if not valid_course_ids:
#         logging.warning("当前没有人数未满的课程")
#     else:
#         logging.warning(f"可抢课程列表: {valid_course_ids}")
#
#     return valid_course_ids


# 如果用到锁:
# 外部调用:
# sem = asyncio.Semaphore(GLOBAL_MAX_CONCURRENT_REQUESTS) if GLOBAL_MAX_CONCURRENT_REQUESTS else None
# 函数内部:
# if self.sem:
#     sync with self.sem:
#         result = await do_something()
