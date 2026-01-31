import logging
import textwrap
from datetime import datetime
import os

# from tools.EncryptedFileHandler import EncryptedFileHandler

# ========== HTTP请求记录器 ==========
logger = logging.getLogger('httplogger')

def logRoundtrip(response, *args, **kwargs):
    extra = {'req': response.request, 'res': response}
    logger.debug('HTTP roundtrip', extra=extra)

class HttpFormatter(logging.Formatter):
    def _formatHeaders(self, d):
        return '\n'.join(f'{k}: {v}' for k, v in d.items())

    def formatMessage(self, record):
        result = super().formatMessage(record)
        if record.name == 'httplogger':
            result += textwrap.dedent('''
                ==========================================
                Duration: {duration}
                ---------------- request ----------------
                {req.method} {req.url}
                {reqhdrs}

                {req.body}
                ---------------- response ----------------
                {res.status_code} {res.reason} {res.url}
                {reshdrs}

                {res.text}
                ==========================================
            ''').format(
                req=record.req,
                res=record.res,
                reqhdrs=self._formatHeaders(record.req.headers),
                reshdrs=self._formatHeaders(record.res.headers),
                duration=f"{record.res.elapsed.total_seconds():.3f}s",
            )

        return result

# ========== HTTP请求记录器 ==========
# 配置根日志记录器
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)  # 设置最低级别为DEBUG

# 创建控制台处理器（WARN级别）
console_handler = logging.StreamHandler()

console_handler.setLevel(logging.WARN)

console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
root_logger.addHandler(console_handler)

# 创建文件处理器（DEBUG级别）
os.makedirs("抢课程序日志", exist_ok=True)

log_filename = f"抢课程序日志/{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
# file_handler = EncryptedFileHandler(log_filename, encoding='utf-8', delay=False)
file_handler = logging.FileHandler(log_filename, encoding='utf-8', delay=False)

file_handler.setLevel(logging.DEBUG)

file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
# file_handler.setFormatter(HttpFormatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
root_logger.addHandler(file_handler)

# 配置 httpx 库的日志设置
for lib in ['httpx']:
    lib_logger = logging.getLogger(lib)
    lib_logger.setLevel(logging.DEBUG)  # 设置库日志级别为DEBUG
    lib_logger.propagate = True  # 确保日志传播到根日志记录器

logging.info("日志初始化")
logging.debug("日志初始化")

# ----- 配置日志记录器结束 -----