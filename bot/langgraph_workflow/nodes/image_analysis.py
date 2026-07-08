"""
图片分析节点

分析用户上传的图片内容。
对应原毕昇工作流的"分析图片"节点。

使用多模态模型（multimodal）进行图片分析。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """请判断这张图片的内容类型：
- 如果图片包含文档、文字、表格或图表，请提炼其核心主题、关键信息和主要结论，并用清晰的要点列出
- 如果不是文档类内容，请客观、详细地描述图片中的主要元素、场景和关键信息

直接输出分析结果，不要输出思考过程。"""


def image_analysis(state: WorkflowState):
    """
    分析图片内容
    """
    image_url_list = state.get("image_url_list", [])

    if not image_url_list:
        return {"final_output": "未检测到图片，请重新发送。"}

    try:
        service = LLMServiceFactory.create("multimodal")

        # 构建多模态消息
        content = [{"type": "text", "text": SYSTEM_PROMPT}]
        for url in image_url_list:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })

        messages = [
            {"role": "user", "content": content},
        ]
        result = service.chat_with_images(messages)
        final_output = result.strip()
        logger.debug(
            f"[ImageAnalysis] 图片分析成功，长度={len(final_output)}, "
            f"图片数={len(image_url_list)}"
        )
        return {"final_output": final_output}
    except Exception as e:
        logger.exception(f"[ImageAnalysis] 图片分析异常: {e}")
        return {"final_output": "抱歉，图片分析过程中出现错误，请稍后重试。"}