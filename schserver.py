import re
import json
import base64
import ddddocr
import logging
import rsa
import httpx
from bs4 import BeautifulSoup
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from datetime import datetime
from typing import Tuple, Optional
import asyncio
from chaojiying import Chaojiying

# 为了只初始化一次ocr, 所以定义成全局的来共用
OCR = ddddocr.DdddOcr()
OCR.set_ranges("0123456789+-x*/=")  # 文档说可以用这个方法来限制识别出的范围, 不过即使设置了他好像也会把 o 识别成 0
JWGL_URL = "https://jwgl.hnfnu.edu.cn:9080"
SFRZ_URL = "https://sfrz.hnfnu.edu.cn"

# 超级鹰客户端（全局单例）
CHAOJIYING_CLIENT: Optional[Chaojiying] = None


def init_chaojiying(username: str, password: str, soft_id: str) -> None:
    """
    初始化超级鹰客户端
    
    Args:
        username: 超级鹰用户名
        password: 超级鹰密码
        soft_id: 软件ID
    """
    global CHAOJIYING_CLIENT
    CHAOJIYING_CLIENT = Chaojiying(username, password, soft_id)
    logging.info("✅ 超级鹰客户端初始化成功")

async def sfrz_login(
        client: httpx.AsyncClient,
        captcha_uid: str,
        captcha_answer: str,
        username: str,
        password: str,
) -> Tuple[bool, str]:
    # RSA 密钥信息：在模块 11 中定义了 RSA 加密所需的公钥、私钥和 modulus（模数），用于后续加密计算：
    rsa_config = {
        "public_exponent": "010001",
        "modulus": "00b5eeb166e069920e80bebd1fea4829d3d1f3216f2aabe79b6c47a3c18dcee5fd22c2e7ac519cab59198ece036dcf289ea8201e2a0b9ded307f8fb704136eaeb670286f5ad44e691005ba9ea5af04ada5367cd724b5a26fdb5120cc95b6431604bd219c6b7d83a6f8f24b43918ea988a76f93c333aa5a20991493d4eb1117e7b1",
        "TAG": "lyasp"
    }

    def convert_password(pain_password: str) -> str:
        # ref: /lyuapServer/login 的 toLogin()
        #
        # function toLogin() {
        #     var thisPwd = document.getElementById("password").value;
        #     if (thisPwd.length != 256) {
        #         setMaxDigits(131);
        #         var key = new RSAKeyPair("010001", '', "00ab14000dddff4fe76335bc5fb6c662ba7fa862a59421dad3c0fc3c4385c9f8c55b2dcdc4879bc9ca64cc9b169bc5393861b014ca1d74da163b8967a7116dff28bee61a02043499e155b3da45e4c4d19841d99a97584ca802d2696207110aacd5318dbe4e1b515c4af661c78ee53178aecff939fa5e74d41cf209056c380ca22d");
        #         var result = encryptedString(key, encodeURIComponent(thisPwd));
        #         $("#password").val(result);
        #     }
        #     document.forms[0].submit();
        # }

        # 创建 RSA 公钥
        pubkey = rsa.PublicKey(
            int(rsa_config["modulus"], 16),
            int(rsa_config["public_exponent"], 16)
        )

        # 将密码转换为整数
        password_as_int = int.from_bytes(pain_password.encode(), 'little')

        # 对填充后的密码进行加密
        encrypted_password_as_int = pow(password_as_int, pubkey.e, pubkey.n)

        # 将加密密码的整数表示转换为十六进制字符串
        # - 使用hex()函数将整数转换为十六进制字符串。
        # - 使用[2:]去除十六进制表示中的 '0x' 前缀。
        # - 使用zfill(256)函数在前面填充零，确保十六进制字符串长度为256个字符。
        encrypted_password = hex(encrypted_password_as_int)[2:].zfill(256)

        return encrypted_password

    # 这个函数会结合当前时间戳和一些标识信息用 RSA 处理后生成一个登录令牌
    # 是 AI 逆向出来的, 把登录的vendor.js 给 ai 就写出来了
    def generate_login_user_token() -> str:
        ### loginUserToken的生成逻辑
        # 在模块 25 的请求拦截器中，loginUserToken通过以下步骤生成：
        # 1. 获取当前时间的时间戳（毫秒级），用于确保每次生成的 token 唯一性：
        # ```
        # var i = (new Date).getTime(); // 时间戳
        # ```
        # 2. 将固定标签TAG与时间戳拼接，形成原始字符串：
        # ```
        # var rawStr = u.a.TAG + i; // u.a.TAG即"lyasp"，拼接后如"lyasp1620000000000"
        # ```
        # 3. 使用模块 11 中定义的 RSA 公钥对拼接后的字符串进行加密：
        # // 1. 创建RSA实例（使用公钥指数和模数）
        # ```
        # var o = a.i(d.b)(u.a.public_exponent, "", u.a.modulus);
        # ```
        # // 2. 加密原始字符串，得到loginUserToken
        # ```
        # var loginUserToken = a.i(d.c)(o, rawStr);
        # ```
        # 生成的loginUserToken被添加到所有请求的请求头中，用于身份验证或请求合法性校验，覆盖delete、get、post等请求方法：
        # // 模块25的请求拦截器
        # ```
        # c.a.interceptors.request.use(function(e) {
        #     // ... 其他逻辑
        #     e.headers.delete.loginUserToken = loginUserToken;
        #     e.headers.get.loginUserToken = loginUserToken;
        #     e.headers.post.loginUserToken = loginUserToken;
        #     // ...
        #     return e;
        # });
        # ```
        exponent = int(rsa_config["public_exponent"], 16)
        modulus = int(rsa_config["modulus"], 16)
        public_key = RSA.construct((modulus, exponent))
        cipher = PKCS1_v1_5.new(public_key)
        timestamp = int(datetime.now().timestamp() * 1000)
        plaintext = f"{rsa_config['TAG']}{timestamp}"
        plaintext_bytes = plaintext.encode('utf-8')
        encrypted_bytes = cipher.encrypt(plaintext_bytes)
        login_user_token = base64.b64encode(encrypted_bytes).decode('utf-8')
        return login_user_token

    resp = (await client.post(
        url = SFRZ_URL + "/lyuapServer/v1/tickets",
        headers = {
            "loginUserToken": generate_login_user_token()
        },
        data={
            'username': username,
            'password': convert_password(password),
            "service": JWGL_URL,
            "loginType": "",
            "id": captcha_uid,  # 使用从验证码接口获取的uid
            "code": captcha_answer,  # 识别到的验证码
            "otpcode": ""
        }
    )).raise_for_status().json()

    print(resp)

    # 根据实际返回结果判断登录是否成功
    if "ticket" in resp:
        # {'tgt': 'TGT-170134-a73e4b15f9a7460bb7742d79af0ba6f8', 'ticket': 'ST-170134-dd1fbfb67ba44a9d84c1dce03cecd088'}
        print("登录成功!")
        return True, resp["ticket"]
    else:
        print("登录失败")
        # {'meta': {'success': True, 'statusCode': 200, 'message': 'ok'}, 'data': {'code': 'PASSERROR', 'data': '5,1'}}
        return False, resp["data"]["code"]


async def sfrz_captcha(
        client: httpx.AsyncClient,
        uid: str,
) ->  Tuple[str, str]:  # uid, answer
    resp = (await client.get(
        url=SFRZ_URL + "/lyuapServer/kaptcha",
        params={"uid": uid},
    )).raise_for_status().json()

    uid = resp['uid']

    image_base64 = resp['content'].split(',')[1]
    image_bytes = base64.b64decode(image_base64)
    ocr_result = OCR.classification(image_bytes)
    print("识别到的验证码: ", ocr_result)

    # 之前服务器在返回含0的验证码的时候, 由于服务器的计算结果是错的导致怎么算都一直错误,
    # 所以当时遇到含0的验证码的时候就跳过重新试,
    # 不过现在不用了, 现在好像修好了
    # if "0" in ocr_result or "o" in ocr_result:
    #     return await sfrz_captcha(client, uid)

    def is_avaliable_ocr(result: str) -> bool:
        # 四位长
        len_eq_4 = len(result) == 4
        # 检查第一个和第三个字符是否为数字
        first_and_third_is_number = result[0].isdigit() and result[2].isdigit()
        # 检查第二个字符是否为常见运算符
        second_is_operator = result[1] in '+-×*÷/'
        # 检查第四个字符是否为等号
        fourth_is_equal = result[3] == '='
        return len_eq_4 and first_and_third_is_number and second_is_operator and fourth_is_equal

    if not is_avaliable_ocr(ocr_result):
        return await sfrz_captcha(client, uid)

    answer = eval(ocr_result[:-1], {'o': 0, '三': "="})  # 因为ocr经常把0识别成o, 不过天才如我， 只要定义o=0就好啦
    print(f"计算答案: {answer}")

    return uid, answer


async def sfrz_captcha_click(
        client: httpx.AsyncClient,
        uid: str,
        retry_count: int = 0,
) -> Tuple[str, str]:
    """
    获取并识别点选验证码（也支持计算类型验证码）
    
    Args:
        client: httpx客户端
        uid: 验证码UID（可以为空字符串）
        retry_count: 重试计数
    
    Returns:
        (uid, 验证码答案/坐标字符串)
    """
    # 添加重试限制
    if retry_count >= 3:
        raise RuntimeError("❌ 验证码识别失败次数过多（3次），请检查超级鹰配置或余额")
    
    if CHAOJIYING_CLIENT is None:
        raise RuntimeError("请先调用 init_chaojiying() 初始化超级鹰客户端")
    
    # 1. 获取验证码信息
    resp = (await client.get(
        url=SFRZ_URL + "/lyuapServer/kaptcha",
        params={"uid": uid},
    )).raise_for_status().json()
    
    uid = resp['uid']
    captcha_type = resp.get('type', 'COMPUTE')
    
    logging.info(f"📸 验证码 UID: {uid}, 类型: {captcha_type}")
    
    # 如果是计算类型，使用原来的方法
    if captcha_type == 'COMPUTE':
        captcha_uid, answer = await sfrz_captcha(client, uid)
        return captcha_uid, str(answer)
    
    # 2. 获取点选验证码的图片
    # 背景图（需要点击的图片）
    bg_content = resp.get('content', '')
    if bg_content:
        bg_base64 = bg_content.split(',')[1] if ',' in bg_content else bg_content
        bg_bytes = base64.b64decode(bg_base64)
    else:
        logging.error("❌ 未获取到背景图")
        await asyncio.sleep(1)
        return await sfrz_captcha_click(client, "", retry_count + 1)
    
    # 模板图（提示文字）
    tpl_content = resp.get('tpl', '')
    tpl_bytes = None
    tpl_height = 0
    if tpl_content:
        tpl_base64 = tpl_content.split(',')[1] if ',' in tpl_content else tpl_content
        tpl_bytes = base64.b64decode(tpl_base64)
    
    # 3. 合成图片（把提示文字图放在背景图上方）
    try:
        from PIL import Image, ImageDraw
        import io
        
        bg_img = Image.open(io.BytesIO(bg_bytes))
        
        if tpl_bytes:
            tpl_img = Image.open(io.BytesIO(tpl_bytes))
            
            # 记录尺寸（用于后续坐标调整）
            bg_width, bg_height = bg_img.size
            tpl_width, tpl_height = tpl_img.size
            
            # 创建新画布：宽度取最大，高度相加
            new_width = max(bg_width, tpl_width)
            new_height = tpl_height + bg_height
            
            combined = Image.new('RGB', (new_width, new_height), (255, 255, 255))
            
            # 粘贴模板图（提示文字）在顶部，居中
            x_offset = (new_width - tpl_width) // 2
            combined.paste(tpl_img, (x_offset, 0))
            
            # 粘贴背景图在底部，居中
            x_offset = (new_width - bg_width) // 2
            combined.paste(bg_img, (x_offset, tpl_height))
            
            # 转换为字节
            output = io.BytesIO()
            combined.save(output, format='JPEG', quality=95)
            final_image_bytes = output.getvalue()
            
            logging.info(f"✅ 已合成图片")
            logging.info(f"   背景图尺寸: {bg_width}x{bg_height}")
            logging.info(f"   模板图尺寸: {tpl_width}x{tpl_height}")
            logging.info(f"   合成图尺寸: {new_width}x{new_height}")
        else:
            final_image_bytes = bg_bytes
            bg_width, bg_height = bg_img.size
            tpl_height = 0
            logging.warning("⚠️ 没有模板图，直接使用背景图")

    except ImportError:
        logging.error("❌ 需要安装 Pillow: pip install pillow")
        raise RuntimeError("请先运行: pip install pillow")
    
    # 4. 保存合成图片（用于调试）
    import os
    os.makedirs("captcha_debug", exist_ok=True)
    with open(f"captcha_debug/{uid}_combined.jpg", "wb") as f:
        f.write(final_image_bytes)
    logging.info(f"💾 合成图片已保存: captcha_debug/{uid}_combined.jpg")
    
    # 5. 调用超级鹰识别
    result = CHAOJIYING_CLIENT.post_pic(
        image_bytes=final_image_bytes,
        code_type=9004  # 1~4个坐标，40题分
    )
    
    # 6. 处理识别结果和坐标调整
    pic_id = result.get('pic_id')
    pic_str = result.get('pic_str')
    
    if not pic_str:
        logging.error(f"❌ 超级鹰未返回坐标: {result}")
        await asyncio.sleep(1)
        return await sfrz_captcha_click(client, "", retry_count + 1)
    
    # 解析原始坐标
    coords = Chaojiying.parse_coordinates(pic_str)
    logging.info(f"🔍 超级鹰原始坐标: {coords}")
    
    # 调整坐标（减去模板图高度）
    if tpl_bytes and tpl_height > 0:
        adjusted_coords = []
        for x, y in coords:
            adj_y = y - tpl_height
            
            # 验证坐标是否在有效范围内
            if not (0 <= x <= bg_width and 0 <= adj_y <= bg_height):
                logging.error(f"❌ 坐标 ({x}, {adj_y}) 超出背景图范围 ({bg_width}x{bg_height})")
                await asyncio.sleep(1)
                return await sfrz_captcha_click(client, "", retry_count + 1)
            
            adjusted_coords.append((x, adj_y))
        
        adjusted_pic_str = '|'.join([f"{x},{y}" for x, y in adjusted_coords])
        
        logging.info(f"📊 坐标调整信息:")
        logging.info(f"   模板图高度: {tpl_height}px")
        logging.info(f"   调整后坐标: {adjusted_coords}")
        
        # 生成标注图片用于调试
        try:
            debug_img = bg_img.copy()
            draw = ImageDraw.Draw(debug_img)
            
            for idx, (x, y) in enumerate(adjusted_coords, 1):
                # 画红色圆圈
                radius = 10
                draw.ellipse([x-radius, y-radius, x+radius, y+radius], 
                            outline='red', width=3)
                # 标注序号
                draw.text((x+15, y-15), str(idx), fill='red')
            
            # 保存标注图
            debug_img.save(f"captcha_debug/{uid}_marked.jpg")
            logging.info(f"💾 标注图已保存: captcha_debug/{uid}_marked.jpg")
        except Exception as e:
            logging.warning(f"⚠️ 生成标注图失败: {e}")
        
        return uid, adjusted_pic_str
    else:
        logging.info(f"✅ 识别成功: pic_id={pic_id}, 坐标={coords}")
        return uid, pic_str


# async def qiang(client, course_map, course_code, profile_id) -> str:
async def qiang(
        client: httpx.AsyncClient,
        course_id: str,
        profile_id: str,
) -> str:
    # <table  width="100%" align="center">
    # 	<tr style="padding-left:20%">
    # 		<td style="text-align:center;">
    # 			<div style="width:85%;color:red;text-align:left;margin:auto;">
    # 				操作 失败:当前选课不开放</br>
    # 			</div>
    # 		</td>
    # 			<script type="text/javascript">
    # 			if(window.electCourseTable){
    # 			}
    # 			</script>
    # 	</tr>
    # 	<tr align="center">
    # 		<td id="timeElapsed"></td>
    # 	</tr>
    # </table>

    resp = (await client.post(
        url=JWGL_URL + "/eams/stdElectCourse!batchOperator.action",
        params={
            "profileId": profile_id
        },
        data={
            "optype": "true",  # optional?
            "operator0": f"{course_id}:true:0",
            "lesson0": course_id,  # optional?
            f"schLessonGroup_{course_id}": "undefined",  # optional?
        },
    )).raise_for_status().text

    return BeautifulSoup(resp, 'html.parser').find('div').get_text(strip=True)


async def jwgl_info_page(
        client: httpx.AsyncClient,
        ticket: Optional[str] = None,
) -> str:
    resp = (await client.get(
        url=JWGL_URL + "/eams/stdElectCourse.action",
        params={'ticket': ticket} if ticket else {},
        follow_redirects=True if ticket else False,
    )).raise_for_status().text
    return resp


async def jwgl_default_page(
        client: httpx.AsyncClient,
        profile_id: str,
) -> str:
    resp = (await client.get(
        url=JWGL_URL + "/eams/stdElectCourse!defaultPage.action",
        params={'electionProfile.id': profile_id},
    )).raise_for_status().text
    return resp


async def jwgl_course_info(
        client: httpx.AsyncClient,
        profile_id: str,
) -> list[dict]:
    # var lessonJSONs = [{
    #     id: 209196,
    #     no: 'JS022053.01',
    #     name: '商用密码应用技术',
    #     code: 'JS022053',
    #     credits: 2.0,
    #     courseId: 21807,
    #     startWeek: 1,
    #     endWeek: 16,
    #     courseTypeId: 103,
    #     courseTypeName: '专业方向课三/限选课',
    #     courseTypeCode: 'ZYFX03',
    #     scheduled: false,
    #     hasTextBook: false,
    #     period: 32,
    #     weekHour: 2,
    #     withdrawable: true,
    #     textbooks: '',
    #     teachers: '张灿',
    #     teacherIds: '5590948',
    #     campusCode: '1',
    #     campusName: '东方红',
    #     remark: '',
    #     arrangeInfo: [],
    #     schLessonGroups: []
    # }, {
    #     id: 209203,
    #     no: 'JS022056.01',
    #     name: 'openEuler国产操作系统',
    #     code: 'JS022056',
    #     credits: 3.0,
    #     courseId: 21942,
    #     startWeek: 1,
    #     endWeek: 16,
    #     courseTypeId: 281,
    #     courseTypeName: '专业方向课四/限选课',
    #     courseTypeCode: 'ZYFX04',
    #     scheduled: false,
    #     hasTextBook: false,
    #     period: 48,
    #     weekHour: 3,
    #     withdrawable: true,
    #     textbooks: '',
    #     teachers: '',
    #     teacherIds: '',
    #     campusCode: '1',
    #     campusName: '东方红',
    #     remark: '',
    #     arrangeInfo: [],
    #     schLessonGroups: []
    # }];
    resp = (await client.get(
        url=JWGL_URL + "/eams/stdElectCourse!data.action",
        params={'profileId': profile_id},
    )).raise_for_status().text

    # 使用字符串切片获取数组内容
    start = resp.find('[')
    end = resp.rfind(']') + 1  # +1是为了包含右括号本身
    course_info = resp[start:end]

    # 调整格式以将其作为 JSON 处理
    course_info = course_info.replace("'", '"')  # 替换单引号为双引号
    course_info = re.sub(r'(\w+):', r'"\1":', course_info)  # 将键名加上双引号

    return json.loads(course_info)


async def jwgl_std_count(
        client: httpx.AsyncClient,
        queryStdCount_url: str,
) -> dict[str, dict[str, int | dict]]:
    # /*sc 当前人数, lc 人数上限*/
    # window.lessonId2Counts = {
    #     '206976': {
    #         sc: 100,
    #         lc: 100
    #     },
    #     '213927': {
    #         sc: 0,
    #         lc: 84
    #     },
    #     '213949': {
    #         sc: 50,
    #         lc: 50,
    #         schLessonGroups: {
    #             '4678': {
    #                 indexNo: 2,
    #                 stdCount: 26,
    #                 stdCountLimit: 26
    #             },
    #             '4677': {
    #                 indexNo: 1,
    #                 stdCount: 24,
    #                 stdCountLimit: 24
    #             }
    #         }
    #     },
    #     '215528': {
    #         sc: 0,
    #         lc: 45
    #     }
    # }

    resp = (await client.get(
        url=JWGL_URL + queryStdCount_url,  # /eams/stdElectCourse!queryStdCount.action?projectId=1&semesterId=309
    )).raise_for_status().text

    # 使用字符串切片获取数组内容
    start = resp.find('{')
    end = resp.rfind('}') + 1  # +1是为了包含右括号本身
    count_data = resp[start:end]

    # 调整格式以将其作为 JSON 处理
    count_data = count_data.replace("'", '"')  # 替换单引号为双引号
    count_data = re.sub(r'(\w+):', r'"\1":', count_data)  # 将键名加上双引号

    return json.loads(count_data)
