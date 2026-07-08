"""
统一模型配置加载模块

从 config.json 读取 llm_models 配置列表，按 name 引用模型。
支持多 provider 扩展，当前支持:
  - openai-compatible: 兼容 OpenAI 格式的任意服务

配置示例 (config.json):
  "llm_models": [
    {
      "name": "light",
      "provider": "openai-compatible",
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "model": "gpt-4o-mini",
      "temperature": 0.2,
      "max_tokens": 4096
    },
    {
      "name": "default",
      "provider": "openai-compatible",
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "model": "gpt-4o",
      "temperature": 0.7,
      "max_tokens": 8192
    }
  ]
"""

import copy
from typing import Optional, Dict, Any, List

from common.log import logger
from config import conf


# 默认模型配置，当 config.json 中未配置 llm_models 时使用
_DEFAULT_MODEL_CONFIG = {
    "name": "default",
    "provider": "openai-compatible",
    "api_base": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "max_tokens": 4096,
}


def get_all_model_configs() -> List[Dict[str, Any]]:
    """
    获取所有模型配置列表
    :return: 模型配置字典列表
    """
    configs = conf().get("llm_models", [])
    if not configs or not isinstance(configs, list):
        logger.warning("[ModelConfig] config.json 中未配置 llm_models，使用默认配置")
        return [_DEFAULT_MODEL_CONFIG]
    return configs


def get_model_config(name: str = "default") -> Optional[Dict[str, Any]]:
    """
    按名称获取模型配置
    :param name: 模型配置名称，对应 config.json 中 llm_models 数组项的 name 字段
    :return: 模型配置字典，未找到时返回 None
    """
    configs = get_all_model_configs()
    for cfg in configs:
        if cfg.get("name") == name:
            return copy.deepcopy(cfg)
    logger.warning(f"[ModelConfig] 未找到名为 '{name}' 的模型配置，可用配置: {[c.get('name') for c in configs]}")
    return None


def get_default_model_config() -> Dict[str, Any]:
    """
    获取默认模型配置（name='default'）
    若未配置，返回内置默认值
    """
    cfg = get_model_config("default")
    if cfg:
        return cfg
    return copy.deepcopy(_DEFAULT_MODEL_CONFIG)


def list_available_models() -> List[str]:
    """列出所有可用的模型配置名称"""
    configs = get_all_model_configs()
    return [cfg.get("name", "unnamed") for cfg in configs]


def get_proxy() -> str:
    """
    获取代理配置
    :return: 代理地址字符串，为空时表示不使用代理
    """
    return conf().get("proxy", "")


def get_bisheng_knowledge_config() -> Dict[str, Any]:
    """
    获取毕昇知识库配置
    :return: 知识库配置字典
    """
    return {
        "api_base": conf().get("bisheng_workflow_upload_file_url", ""),
        "knowledge_base_id": conf().get("langgraph_knowledge_base_id", 2),
    }


def get_bisheng_rag_config() -> Dict[str, Any]:
    """
    获取毕昇RAG检索配置
    :return: RAG配置字典
    """
    return {
        "api_base": conf().get("bisheng_rag_api_base", ""),
        "api_key": conf().get("bisheng_rag_api_key", ""),
        "knowledge_base_ids": conf().get("bisheng_rag_knowledge_ids", []),
    }