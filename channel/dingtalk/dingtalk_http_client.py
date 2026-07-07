"""
钉钉自定义 API 接口实现
用于实现官方 SDK 中未提供的接口
"""

import requests
from common.log import logger
import json
import time

from config import conf


class DingtalkHttp:
    """钉钉 HTTP API 封装类"""
    
    def __init__(self):
        self.appKey = conf().get("dingtalk_client_id", None)
        self.appSecret = conf().get("dingtalk_client_secret", None)
        self._access_token = None
        self._token_expire_time = 0
        self._token_get_time = 0  # 记录获取 token 的时间戳
        
        if not self.appKey or not self.appSecret:
            logger.error("钉钉 appKey 或 appSecret 未配置")
            raise ValueError("钉钉 appKey 或 appSecret 未配置")

    def get_access_token(self):
        """
        获取钉钉 access_token
        
        Returns:
            str: access_token，获取失败返回 None
        """
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        
        headers = {
            "Content-Type": "application/json"
        }
        
        data = {
            "appKey": self.appKey,
            "appSecret": self.appSecret
        }
        
        try:
            response = requests.post(url, headers=headers, json=data)
            result = response.json()
            
            if response.status_code == 200 and "accessToken" in result:
                self._access_token = result.get("accessToken")
                self._token_expire_time = result.get("expireIn", 7200)
                self._token_get_time = time.time()
                logger.debug(f"[DingtalkHttp] access_token 成功，有效期：{self._token_expire_time}秒")
                return self._access_token
            else:
                error_msg = result.get("message", "未知错误")
                logger.error(f"[DingtalkHttp] access_token 失败: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"[DingtalkHttp] accessToken API 异常: {str(e)}")
            return None

    def is_token_expired(self):
        """
        检查 access_token 是否过期
        提前 5 分钟认为 token 过期，避免在使用时刚好过期
        
        Returns:
            bool: True 表示已过期或即将过期，False 表示仍然有效
        """
        if not self._access_token or self._token_get_time == 0:
            return True
        
        elapsed_time = time.time() - self._token_get_time
        
        buffer_time = 300
        
        if elapsed_time >= (self._token_expire_time - buffer_time):
            logger.debug(f"[DingtalkHttp] access_token 已过期或即将过期（已使用 {elapsed_time:.0f}秒/{self._token_expire_time}秒）")
            return True
        
        return False

    def ensure_access_token(self):
        """
        确保 access_token 有效
        如果 token 不存在或已过期，则重新获取
        
        Returns:
            str: access_token，获取失败返回 None
        """
        if self.is_token_expired():
            logger.debug("[DingtalkHttp] access_token 无效，正在重新获取...")
            return self.get_access_token()
        
        logger.debug("[DingtalkHttp] 使用缓存的 access_token")
        return self._access_token

    def get_user_info(self, userid, language="zh_CN"):
        """
        根据用户ID获取用户信息（包含职位）
        
        Args:
            userid (str): 用户的userId
            language (str): 通讯录语言，zh_CN（中文）或 en_US（英文），默认zh_CN
        
        Returns:
            dict: 返回用户信息，包含职位等字段
        """
        access_token = self.ensure_access_token()
        if not access_token:
            return {
                "errcode": -1,
                "errmsg": "获取 access_token 失败"
            }
        
        url = "https://oapi.dingtalk.com/topapi/v2/user/get"
        params = {
            "access_token": access_token
        }

        data = {
            "userid": userid,
            "language": language
        }
        
        try:
            response = requests.post(url, params=params, json=data)
            result = response.json()
            
            if result.get("errcode") == 0:
                logger.debug(f"[DingtalkHttp] 获取用户 {userid} 信息成功")
                return result
            else:
                errcode = result.get("errcode")
                errmsg = result.get("errmsg")
                logger.error(f"[DingtalkHttp] 获取用户信息失败: errcode={errcode}, errmsg={errmsg}")
                
                if errcode in [40014, 42001, 50014]:
                    logger.warning("[DingtalkHttp] 检测到 token 可能失效，尝试刷新 token 后重试")
                    self._access_token = None
                    self._token_get_time = 0
                    
                    access_token = self.ensure_access_token()
                    if access_token:
                        params["access_token"] = access_token
                        response = requests.post(url, params=params, json=data)
                        result = response.json()
                        
                        if result.get("errcode") == 0:
                            logger.debug(f"[DingtalkHttp] 重试成功，获取用户 {userid} 信息成功")
                            return result
                
                return result
                
        except Exception as e:
            logger.error(f"[DingtalkHttp] 请求钉钉用户信息API异常: {str(e)}")
            return {
                "errcode": -1,
                "errmsg": f"[DingtalkHttp] 请求异常: {str(e)}"
            }

    def get_user_title(self, userid):
        """
        获取用户的职位
        
        Args:
            userid (str): 用户的userId
        
        Returns:
            str: 用户职位，如果获取失败返回None
        """
        result = self.get_user_info(userid)
        
        if result.get("errcode") == 0:
            title = result.get("result", {}).get("title")
            logger.debug(f"[DingtalkHttp] 获取用户 {userid} 职位: {title}")
            return title
        
        logger.warning(f"[DingtalkHttp] 获取用户 {userid} 职位失败")
        return None