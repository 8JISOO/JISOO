"""
超级鹰打码平台封装
官方文档: http://www.chaojiying.com/api-14.html
"""

import requests
import logging
from typing import List, Tuple


class Chaojiying:
    """超级鹰验证码识别客户端"""
    
    def __init__(self, username: str, password: str, soft_id: str):
        """
        初始化超级鹰客户端
        
        Args:
            username: 超级鹰用户名
            password: 超级鹰密码
            soft_id: 软件ID
        """
        self.username = username
        self.password = password
        self.soft_id = soft_id
        self.base_url = 'http://upload.chaojiying.net/Upload/Processing.php'
    
    def post_pic(self, image_bytes: bytes, code_type: int) -> dict:
        """
        上传图片进行识别
        
        Args:
            image_bytes: 图片字节数据
            code_type: 验证码类型
                - 1902: 1~4个坐标，10题分
                - 9004: 1~4个坐标，40题分
                - 9005: 5~8个坐标，50题分
                - 9006: 9~100个坐标，80题分
        
        Returns:
            识别结果字典，格式如：
            {
                'err_no': 0,
                'err_str': 'OK',
                'pic_id': '1234567890',
                'pic_str': '132,127|265,178',
                'md5': 'xxx'
            }
        """
        files = {'userfile': ('captcha.jpg', image_bytes)}
        data = {
            'user': self.username,
            'pass2': self.password,
            'softid': self.soft_id,
            'codetype': code_type,
        }
        
        try:
            response = requests.post(
                self.base_url,
                files=files,
                data=data,
                timeout=20,
                proxies={'http': None, 'https': None}  # 禁用代理
            )
            result = response.json()
            logging.debug(f"超级鹰返回: {result}")
            return result
        except Exception as e:
            logging.error(f"超级鹰请求失败: {e}")
            return {'err_no': -1, 'err_str': str(e)}
    
    def report_error(self, pic_id: str) -> dict:
        """
        报告识别错误
        
        Args:
            pic_id: 图片ID
        
        Returns:
            操作结果
        """
        data = {
            'user': self.username,
            'pass2': self.password,
            'softid': self.soft_id,
            'id': pic_id,
        }
        
        try:
            response = requests.post(
                'http://upload.chaojiying.net/Upload/ReportError.php',
                data=data,
                timeout=10,
                proxies={'http': None, 'https': None}  # 禁用代理
            )
            result = response.json()
            logging.debug(f"报告错误返回: {result}")
            return result
        except Exception as e:
            logging.error(f"报告错误失败: {e}")
            return {'err_no': -1, 'err_str': str(e)}
    
    def get_balance(self) -> int:
        """
        查询账户余额
        
        Returns:
            剩余题分，失败返回 -1
        """
        data = {
            'user': self.username,
            'pass2': self.password,
        }
        
        try:
            response = requests.post(
                'http://upload.chaojiying.net/Upload/GetScore.php',
                data=data,
                timeout=10,
                proxies={'http': None, 'https': None}  # 禁用代理
            )
            result = response.json()
            logging.debug(f"查询余额返回: {result}")
            
            # 返回格式: {'err_no': 0, 'err_str': 'OK', 'tifen': '9950', 'tifen_lock': '0'}
            if result.get('err_no') == 0:
                return int(result.get('tifen', -1))
            else:
                logging.error(f"查询余额失败: {result}")
                return -1
        except Exception as e:
            logging.error(f"查询余额失败: {e}")
            return -1
    
    @staticmethod
    def parse_coordinates(pic_str: str) -> List[Tuple[int, int]]:
        """
        解析坐标字符串
        
        Args:
            pic_str: 坐标字符串，格式如 "132,127|265,178"
        
        Returns:
            坐标列表 [(x1, y1), (x2, y2), ...]
        """
        if not pic_str:
            return []
        
        coords = []
        for coord_str in pic_str.split('|'):
            x, y = map(int, coord_str.split(','))
            coords.append((x, y))
        
        return coords
