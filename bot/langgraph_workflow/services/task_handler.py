"""
任务指令处理器 — 关键词匹配(查看/删除) + LLM(创建任务)
"""

import re, json
from datetime import datetime
from typing import Optional
from common.log import logger
from bot.langgraph_workflow.services.task_scheduler import TaskScheduler

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": "创建定时任务",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "任务内容, 去掉'提醒我'/'记一下'等前缀, 如'喝水'"},
                "trigger_time": {"type": "string", "description": "触发时间, 格式YYYY-MM-DD HH:MM"},
                "assignee": {"type": "string", "description": "责任人, @某人 则填那人, 否则空"},
                "repeat": {"type": "string", "description": "重复类型, once/daily/weekly/weekdays"},
                "repeat_days": {"type": "array", "items": {"type": "integer"}, "description": "每周哪几天, 0=周一, 如[0,2,4]"},
                "group_name": {"type": "string", "description": "推送到的群聊名称, 如'测试群'。如果用户说'发到群里'/'在群里提醒'则填群名, 否则空"},
            },
            "required": ["content", "trigger_time"],
        }
    }
}

SYSTEM = """你是一个任务管理助手。理解用户的时间表达，调用 create_task 工具。

当前时间：{now}
当前群聊：{group_name}

时间规则：
- "三十秒后"、"半分钟后" → 当前时间+1分钟
- "X分钟后" → 当前时间+X分钟
- "九点01" → 如果在晚上，自动推断为21:01
- "今天晚上八点" → 今天20:00
- "明天9点" → 明天09:00
- 只说"X点" → 如果在晚上(18点后)且X<12，自动加12小时变成PM
- 如果计算出的时间已过 → 自动加一天

周期规则：
- "每天"、"每日" → repeat="daily"
- "每周一"、"每周一三五" → repeat="weekly", repeat_days=[0,2,4]
- "工作日" → repeat="weekdays"
- 不指定 → repeat="once"

内容规则：去掉"提醒我"、"记一下"、"帮我"等前缀，只保留核心内容。

群聊推送规则：
- 用户现在在群聊 {current_group} 中 → 默认 group_name 填 {current_group}（除非用户说"私聊"、"不发群里"）
- 当前不在群聊中 → group_name 留空

不解释，只调用工具。"""


def handle_task_command(query, user_name="", user_id="",
                        is_group=False, group_name="") -> Optional[str]:
    scheduler = TaskScheduler()
    text = query.strip()

    # ===== 关键词匹配（不调 LLM）=====
    if any(kw in text for kw in ["临近","最近","快到期","即将"]):
        tasks = scheduler.list_tasks()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        upcoming = sorted([t for t in tasks if t.trigger_time > now_str], key=lambda t: t.trigger_time)[:5]
        if not upcoming: return "近期没有待办任务。"
        return "近期任务：\n" + "\n".join([f"#{t.id} {t.trigger_time} {t.content}" for t in upcoming])

    if any(kw in text for kw in ["查看任务","我的任务","任务列表","任务有哪些","什么任务","任务是什么","还有任务","任务情况","显示任务","全部任务","所有任务"]):
        tasks = scheduler.list_tasks()
        if not tasks: return "当前没有待办任务。"
        return f"共 {len(tasks)} 个待办任务：\n" + "\n".join([f"#{t.id} {t.trigger_time} {t.content}" for t in tasks])

    m = re.match(r"删除任务\s*#?(\d+)", text)
    if m:
        ok = scheduler.cancel_task(int(m.group(1)))
        return f"已取消任务 #{m.group(1)}" if ok else f"未找到任务 #{m.group(1)}。"

    m = re.match(r"查询任务\s*#?(\d+)", text)
    if m:
        for t in scheduler.list_tasks():
            if t.id == int(m.group(1)):
                return f"#{t.id} {t.trigger_time} {t.content}"
        return f"未找到任务 #{m.group(1)}。"

    # ===== 创建任务 → 调 LLM =====
    if not re.search(r"(记一下|提醒|记住|添加任务|增加|新建)", text):
        return None

    logger.info(f"[Task] LLM: {text}")
    try:
        from bot.langgraph_workflow.services.llm_service import LLMServiceFactory
        service = LLMServiceFactory.create("light")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        # 当前群聊名称（为空则表示单聊）
        current_group = group_name if is_group and group_name else ""
        resp = service.client.chat.completions.create(
            model=service.model_name,
            messages=[
                {"role": "system", "content": SYSTEM.format(now=now_str, group_name=current_group, current_group=current_group)},
                {"role": "user", "content": text},
            ],
            tools=[TOOL_SCHEMA],
            tool_choice="auto", temperature=0.1,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.function.name == "create_task":
                    args = json.loads(tc.function.arguments)
                    content = args.get("content", "")
                    trigger_time = args.get("trigger_time", "")
                    assignee = args.get("assignee", user_name)
                    repeat = args.get("repeat", "once")
                    repeat_days = args.get("repeat_days", [])
                    group_name = args.get("group_name", "")
                    scheduler.add_task(
                        content=content, trigger_time=trigger_time,
                        creator_name=user_name, creator_id=user_id,
                        assignee_name=assignee or user_name, assignee_id=user_id,
                        schedule_type=repeat, schedule_weekdays=repeat_days,
                        group_name=group_name,
                    )
                    rpt = {"once":"","daily":"(每天)","weekly":"(每周)","weekdays":"(工作日)"}.get(repeat, "")
                    grp_info = f" → 群:{group_name}" if group_name else ""
                    result = f"已记录：{content}（{trigger_time}{rpt}{grp_info}）"
                    logger.info(f"[Task] 创建: {result}")
                    return result
    except Exception as e:
        logger.error(f"[Task] LLM 异常: {e}")

    return "没理解时间，请说：提醒我明天9点开会"