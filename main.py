import statemachine
import schserver
import logging

import asyncio

accounts = [

    # {"username": "23414010208", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": [  'TSWL2520.01',  'TSWL2521.01',  'TSWL2264.01'], "password": "luo041127"},
    # {"username": "24407040117", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": ['TSWL2506.01', 'TSWL2029.01', 'TSWL2042.01', 'TSWL2033.01', 'TSWL2034.01',  'TSWL2037.01', 'TSWL2038.01', 'TSWL2042.01', 'TSWL2040.01', 'TSWL2043.01',  'TSWL2045.01', 'TSWL2046.01', 'TSWL2503.01', 'TSWL2504.01',  'TSWL2509.01',  'TSWL2521.01',  ], "password": "5ibeiwu1006."},
    # {"username": "22602097719", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": [  'TSWL2025.01'], "password": "wang080184"},
    # {"username": "23208010510", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": ['TSWL2506.01', 'TSWL2029.01', 'TSWL2042.01', 'TSWL2033.01', 'TSWL2034.01', 'TSWL2035.01', 'TSWL2037.01', 'TSWL2038.01', 'TSWL2042.01', 'TSWL2040.01', 'TSWL2043.01',  'TSWL2045.01', 'TSWL2046.01', 'TSWL2503.01', 'TSWL2504.01', 'TSWL2505.01',  'TSWL2509.01',  'TSWL2521.01',  ], "password": "Zjl200427@"},
    # {"username": "22601097507", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
     #"想抢的课": ['TSWL2506.01', 'TSWL2029.01', 'TSWL2042.01', 'TSWL2033.01', 'TSWL2034.01', 'TSWL2035.01', 'TSWL2037.01', 'TSWL2038.01', 'TSWL2042.01', 'TSWL2040.01', 'TSWL2043.01',  'TSWL2045.01', 'TSWL2046.01', 'TSWL2503.01',  'TSWL2505.01',  'TSWL2509.01',  ], "password": "@lrs071012"},
    # {"username": "22602097720", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": ['TSWL2027.01', 'TSWL2028.01', 'TSWL2029.01', 'TSWL2040.01', 'TSWL2043.01', 'TSWL2045.01', 'TSWL2019.01', 'TSWL2025.01', 'TSWL2026.01','TSWL2006.01','TSWL2007.01','TSWL2009.01','TSWL2019.01', 'TSWL2025.01', 'TSWL2026.01','TSWL2506.01', 'TSWL2029.01', 'TSWL2042.01', 'TSWL2033.01',  'TSWL2035.01',
    #               'TSWL2037.01', 'TSWL2038.01', 'TSWL2042.01', 'TSWL2040.01', 'TSWL2043.01', 'TSWL2045.01',
    #               'TSWL2046.01', 'TSWL2503.01', 'TSWL2505.01', 'TSWL2509.01'], "password": "170026"},
    {"username": "24406030318", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
     "想抢的课": ['TSWL2019.01', 'TSWL2026.01',
                  'TSWL2027.01', 'TSWL2028.01', 'TSWL2029.01', 'TSWL2040.01', 'TSWL2043.01', 'TSWL2045.01',
                  'TSWL2046.01', 'TSWL2503.01', 'TSWL2505.01', 'TSWL2509.01',], "password": "a101317230"},
    # {"username": "22601097515", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": ['TSWX2121.01','TSWL2006.01','TSWL2007.01','TSWL2017.01', 'TSWL2019.01', 'TSWL2025.01', 'TSWL2026.01','TSWL2506.01', 'TSWL2029.01', 'TSWL2042.01', 'TSWL2033.01',
    #               'TSWL2037.01', 'TSWL2038.01', 'TSWL2042.01', 'TSWL2040.01', 'TSWL2043.01',
    #               'TSWL2046.01', 'TSWL2503.01', 'TSWL2505.01', ], "password": "OYjm17700569059"},
   # {"username": "22605098840", "需要抢课": True, "自动抢课": False, "只抢艺术": False,
    #  "想抢的课": ['TSWX2121.01','TSWL2005.01','TSWL2006.01','TSWL2007.01','TSWL2009.01','TSWL2017.01', 'TSWL2019.01', 'TSWL2025.01', 'TSWL2026.01','TSWL2506.01', 'TSWL2029.01', 'TSWL2042.01', 'TSWL2033.01', 'TSWL2034.01', 'TSWL2035.01',
    #               'TSWL2037.01', 'TSWL2038.01', 'TSWL2042.01', 'TSWL2040.01', 'TSWL2043.01',
    #               'TSWL2046.01', 'TSWL2503.01', 'TSWL2505.01', 'TSWL2509.01', ], "password": "260068"},
]


async def main():
    # 初始化超级鹰
    schserver.init_chaojiying(
        username="JISOO666",
        password="01yef1ak",
        soft_id="977492"
    )
    
    # ===== 新增：测试超级鹰连接 =====
    try:
        balance = schserver.CHAOJIYING_CLIENT.get_balance()
        if balance < 0:
            logging.error("❌ 超级鹰连接失败，请检查网络或账号")
            return
        logging.info(f"✅ 超级鹰连接成功，余额: {balance} 题分")
    except Exception as e:
        logging.error(f"❌ 超级鹰初始化失败: {e}")
        return
    
    tasks = []
    for account in accounts:
        model = statemachine.QiangModel(
            username = account["username"],
            password = account["password"],
            will_qiang = account["需要抢课"],
            want_auto_choose_target_courses=account["自动抢课"],
            yishu_only = account["只抢艺术"] if account["只抢艺术"] else False,
            target_course_no_list = account["想抢的课"] if account["想抢的课"] else [],
        )
        # 创建任务：从 INIT 开始触发 start 事件（这是 coroutine）
        # 注意：await model.start() 会启动整个状态机序列（因为 on_enter_* 回调会触发后续 transitions）
        tasks.append(asyncio.create_task(model.start()))

    # 等待所有账号完成（DONE 或 ERROR）
    # return_exceptions=False 时任务异常会立即传播并取消其他任务
    # return_exceptions=True 时异常作为结果返回且不中断所有任务
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())


# async def multi_user_login(users_list):
#     """处理多个用户登录"""
#     async def safe_login(user):
#         try:
#             return await statemachine.new_login(user["username"], user["password"])
#         except Exception as e:
#             print(f"用户 {user['username']} 遇到错误: {e}")
#             raise e
#             # return None  # 或返回特定的错误标识
#
#     # 创建所有用户的安全登录任务
#     tasks = [safe_login(user) for user in users_list]
#     # 并发执行所有登录任务
#     results = await asyncio.gather(*tasks)
#     return results
#
# asyncio.run(multi_user_login(users))