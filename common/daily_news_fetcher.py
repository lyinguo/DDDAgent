# encoding: utf-8
"""
AI行业新闻获取工具模块
"""

import requests
from bs4 import BeautifulSoup
from common.log import logger


def fetch_ai_news(news_count: int = 10) -> list:
    """
    从 IT之家 next 获取 AI 新闻
    """
    url = "https://next.ithome.com/ai"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "utf-8"
        html = resp.text

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("#list .bl > li")
        news_list = []
        for li in items:
            a = li.select_one("h2 a.title")
            if not a:
                continue

            title = a.get_text(strip=True)
            link = a["href"]

            summary_tag = li.select_one(".m")
            summary = summary_tag.get_text(strip=True) if summary_tag else ""

            c_div = li.select_one(".c")
            publish_time = c_div["data-ot"] if c_div and c_div.has_attr("data-ot") else ""

            news_list.append({
                "title": title,
                "url": link,
                "summary": summary,
                "publish_time": publish_time
            })

        return news_list[:news_count]

    except Exception as e:
        logger.error(f"[NewsFetcher] 获取新闻失败: {e}")
        return []


def format_news_content(news_list: list) -> str:
    if not news_list:
        return "今日AI行业新闻获取失败，请稍后重试。"

    news_text_parts = []
    for idx, news in enumerate(news_list, 1):
        news_text_parts.append(
            f"{idx}. 标题：{news['title']}\n"
            f"   摘要：{news['summary']}\n"
            f"   链接：{news['url']}\n"
            f"   时间：{news['publish_time']}"
        )

    return "\n\n".join(news_text_parts)


def get_industry_news(news_count: int = 10) -> str:
    news_list = fetch_ai_news(news_count)
    return format_news_content(news_list)