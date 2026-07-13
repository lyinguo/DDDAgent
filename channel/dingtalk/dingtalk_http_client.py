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
        self._token_get_time = 0
        if not self.appKey or not self.appSecret:
            logger.error("钉钉 appKey 或 appSecret 未配置")
            raise ValueError("钉钉 appKey 或 appSecret 未配置")

    def get_access_token(self):
        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        headers = {"Content-Type": "application/json"}
        data = {"appKey": self.appKey, "appSecret": self.appSecret}
        try:
            response = requests.post(url, headers=headers, json=data)
            result = response.json()
            if response.status_code == 200 and "accessToken" in result:
                self._access_token = result.get("accessToken")
                self._token_expire_time = result.get("expireIn", 7200)
                self._token_get_time = time.time()
                return self._access_token
            else:
                logger.error(f"[DingtalkHttp] access_token 失败: {result.get('message', '未知错误')}")
                return None
        except Exception as e:
            logger.error(f"[DingtalkHttp] accessToken API 异常: {str(e)}")
            return None

    def is_token_expired(self):
        if not self._access_token or self._token_get_time == 0:
            return True
        elapsed = time.time() - self._token_get_time
        return elapsed >= (self._token_expire_time - 300)

    def ensure_access_token(self):
        if self.is_token_expired():
            return self.get_access_token()
        return self._access_token

    def get_user_info(self, userid, language="zh_CN"):
        access_token = self.ensure_access_token()
        if not access_token:
            return {"errcode": -1, "errmsg": "获取 access_token 失败"}
        url = "https://oapi.dingtalk.com/topapi/v2/user/get"
        params = {"access_token": access_token}
        data = {"userid": userid, "language": language}
        try:
            response = requests.post(url, params=params, json=data)
            result = response.json()
            if result.get("errcode") == 0:
                return result
            else:
                logger.error(f"[DingtalkHttp] 获取用户信息失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
                if result.get("errcode") in [40014, 42001, 50014]:
                    self._access_token = None
                    self._token_get_time = 0
                    access_token = self.ensure_access_token()
                    if access_token:
                        params["access_token"] = access_token
                        response = requests.post(url, params=params, json=data)
                        result = response.json()
                        if result.get("errcode") == 0:
                            return result
                return result
        except Exception as e:
            logger.error(f"[DingtalkHttp] 请求钉钉用户信息API异常: {str(e)}")
            return {"errcode": -1, "errmsg": f"请求异常: {str(e)}"}

    def get_user_title(self, userid):
        result = self.get_user_info(userid)
        if result.get("errcode") == 0:
            return result.get("result", {}).get("title")
        return None

    def fetch_recent_file_messages(self, conversation_id, max_results=5):
        """
        从钉钉群聊中拉取最近的文件消息
        需要机器人在钉钉开放平台配置了"读取消息"权限
        """
        access_token = self.ensure_access_token()
        if not access_token:
            logger.error("[DingtalkHttp] 无法获取 access_token")
            return []

        url = f"https://api.dingtalk.com/v1.0/im/conversations/{conversation_id}/messages/query"
        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": access_token,
        }
        payload = {"maxResults": max_results}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code != 200:
                logger.error(f"[DingtalkHttp] 查询消息失败: status={response.status_code}")
                return []

            data = response.json()
            raw_messages = data.get("messages", []) or data.get("result", []) or []
            file_msgs = []

            for msg in raw_messages:
                msg_type = msg.get("messageType", "")
                if msg_type == "file":
                    content = msg.get("content", {})
                    if isinstance(content, str):
                        try:
                            content = json.loads(content)
                        except:
                            continue
                    download_code = content.get("downloadCode") or msg.get("downloadCode", "")
                    file_name = content.get("fileName") or msg.get("fileName", "未知文件")
                    if download_code:
                        file_msgs.append({
                            "filename": file_name,
                            "download_code": download_code,
                            "msg_id": msg.get("id", ""),
                            "sender_id": msg.get("senderId", ""),
                            "create_at": msg.get("createAt", ""),
                        })

            logger.info(f"[DingtalkHttp] 拉取到 {len(file_msgs)} 个文件消息")
            return file_msgs

        except Exception as e:
            logger.exception(f"[DingtalkHttp] 查询消息异常: {e}")
            return []

    def send_group_message(self, conversation_id: str, content: str) -> bool:
        """
        向钉钉群聊发送文本消息（主动推送，不走机器人回复流程）
        :param conversation_id: 群会话ID
        :param content: 消息文本内容
        :return: True=发送成功, False=发送失败
        """
        access_token = self.ensure_access_token()
        if not access_token:
            logger.error("[DingtalkHttp] 无法获取 access_token，群消息发送失败")
            return False

        url = f"https://api.dingtalk.com/v1.0/im/conversations/{conversation_id}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": access_token,
        }
        payload = {
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": content}),
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                logger.info(f"[DingtalkHttp] 群消息发送成功: {conversation_id}")
                return True
            else:
                logger.warning(f"[DingtalkHttp] 群消息发送失败: status={response.status_code}, body={response.text}")
                return False
        except Exception as e:
            logger.error(f"[DingtalkHttp] 群消息发送异常: {e}")
            return False