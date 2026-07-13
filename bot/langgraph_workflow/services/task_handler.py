"""
任务指令处理器 — 全部通过 LLM 工具调用实现

将 create_task / query_tasks / update_task / cancel_task 作为工具注册给 LLM，
LLM 语义理解用户自然语言后选择调用哪个工具并填入参数。
兜底：当 LLM 无法确定意图时，给出友好提示引导用户。
"""

from typing import Optional
from common.log import logger
from bot.langgraph_workflow.services.task_scheduler import TaskScheduler
import re, json
from datetime import datetime, timedelta

# ====== 工具定义 ======

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "创建定时任务，到时间会在群里或个人提醒",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "任务内容, 去掉'提醒我'/'记一下'等前缀, 如'喝水'"},
                    "trigger_time": {"type": "string", "description": "触发时间, 格式YYYY-MM-DD HH:MM, 如'2026-07-13 17:20'"},
                    "assignee": {"type": "string", "description": "责任人, @某人 则填那人, 否则空"},
                    "repeat": {"type": "string", "description": "重复类型, once/daily/weekly/weekdays"},
                    "repeat_days": {"type": "array", "items": {"type": "integer"}, "description": "每周哪几天, 0=周一, 如[0,2,4]"},
                    "group_name": {"type": "string", "description": "推送到的群聊名称。在群聊中默认填当前群名, 除非用户说'私聊'"},
                },
                "required": ["content", "trigger_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_tasks",
            "description": "查询任务列表，支持按状态、按人、按时间、按关键词筛选",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "任务状态: 'active'(待办) / 'done'(已完成) / 'all'(全部)",
                        "enum": ["active", "done", "all"],
                    },
                    "user": {
                        "type": "string",
                        "description": "按创建人或责任人筛选, 如'刘银国', 空表示全部",
                    },
                    "date": {
                        "type": "string",
                        "description": "按日期筛选: 'today'(今天) / 'tomorrow'(明天) / 具体日期如'2026-07-13'",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "按任务内容关键词搜索, 如'吃饭'",
                    },
                    "upcoming": {
                        "type": "boolean",
                        "description": "是否只查近期即将到期的(前5条)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "修改/编辑已有任务。可以改内容、时间、责任人、周期、推送群。只填要改的字段，不填的保持不变",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "要修改的任务编号"},
                    "content": {"type": "string", "description": "新任务内容"},
                    "trigger_time": {"type": "string", "description": "新触发时间, 格式YYYY-MM-DD HH:MM"},
                    "assignee": {"type": "string", "description": "新责任人"},
                    "repeat": {"type": "string", "description": "新周期, once/daily/weekly/weekdays"},
                    "repeat_days": {"type": "array", "items": {"type": "integer"}, "description": "每周哪几天, 0=周一"},
                    "group_name": {"type": "string", "description": "新推送群名"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "取消/删除任务。按编号删单个，或按关键词+用户+索引删。默认按时间排序，索引1=最早",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务编号，有编号优先用编号"},
                    "keyword": {"type": "string", "description": "按内容关键词删除，如'打卡'。与task_id二选一"},
                    "user": {"type": "string", "description": "按责任人筛选，配合keyword使用"},
                    "task_index": {"type": "integer", "description": "删第几个：1=第一个，2=第二个，0或不填=全部"},
                },
            },
        },
    },
]

SYSTEM = """你是一个任务管理助手。根据用户的自然语言，理解用户意图后调用合适的工具。

当前时间：{now}
当前群聊：{group_name}

可用的工具（增删改查）：
1. create_task  — 创建新任务
2. query_tasks  — 查询任务列表
3. update_task  — 修改已有任务
4. cancel_task  — 取消/删除任务

判断用户意图的指引：
- 用户说"提醒我/记一下/添加/新建" → 创建任务 → create_task
- 用户说"有什么任务/有哪些/查一下/看看" → 查询任务 → query_tasks
- 用户说"改成/修改/编辑/换个时间/改为" → 修改任务 → update_task
- 用户说"删除/取消/删掉/清除/不要了" → 删除任务 → cancel_task
- 用户说"第一个/第二个/最后一个"且涉及删除 → cancel_task 的 task_index 参数
- 用户说"我的任务" → query_tasks(user="用户名")
- 用户说"今天/明天的任务" → query_tasks(date="today"/"tomorrow")
- 用户说"已完成/已取消的任务" → query_tasks(status="done"/"all")

时间解析规则（创建/修改时用）：
- "X分钟后" → 当前时间+X分钟
- "今天晚上八点" → 今天20:00
- "明天9点" → 明天09:00
- 如果计算出的时间已过 → 自动加一天

周期规则：
- "每天/每日" → repeat="daily"
- "每周一/每周一三五" → repeat="weekly", repeat_days=[0,2,4]
- "工作日" → repeat="weekdays"
- 不指定 → repeat="once"

内容规则：去掉"提醒我"、"记一下"、"帮我"等前缀，只保留核心内容。

如果用户说的话和任务无关，不要调用任何工具，直接回复"请说具体的任务指令，例如：提醒我明天9点开会"。
直接调用工具，不要解释。"""


def handle_task_command(query, user_name="", user_id="",
                        is_group=False, group_name="") -> Optional[str]:
    """
    处理任务相关指令 — LLM 语义理解 + 工具调用
    """
    scheduler = TaskScheduler()
    text = query.strip()

    # ===== 快速通道 =====
    m = re.match(r"删除任务\s*#?(\d+)", text)
    if m:
        ok = scheduler.cancel_task(int(m.group(1)))
        return f"✅ 已取消任务 #{m.group(1)}" if ok else f"❌ 未找到任务 #{m.group(1)}。"

    m = re.match(r"查询任务\s*#?(\d+)", text)
    if m:
        for t in scheduler.list_tasks():
            if t.id == int(m.group(1)):
                return _format_task_detail(t)
        return f"❌ 未找到任务 #{m.group(1)}。"

    # ===== LLM 工具调用 =====
    task_keywords = ["任务", "提醒", "记一下", "记住", "添加", "新建", "增加",
                     "取消", "删除", "删掉", "清除", "修改", "编辑", "改成", "改为",
                     "查一下", "看看", "有哪些"]
    if not any(kw in text for kw in task_keywords):
        return None

    logger.info(f"[Task] LLM: {text}")
    try:
        from bot.langgraph_workflow.services.llm_service import LLMServiceFactory
        service = LLMServiceFactory.create("light")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        current_group = group_name if is_group and group_name else ""

        resp = service.client.chat.completions.create(
            model=service.model_name,
            messages=[
                {"role": "system", "content": SYSTEM.format(now=now_str, group_name=current_group)},
                {"role": "user", "content": text},
            ],
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.1,
        )
        msg = resp.choices[0].message

        if msg.tool_calls:
            for tc in msg.tool_calls:
                fn = tc.function.name
                args = json.loads(tc.function.arguments)

                if fn == "create_task":
                    return _handle_create(args, scheduler, user_name, user_id)
                elif fn == "query_tasks":
                    return _handle_query(args, scheduler)
                elif fn == "update_task":
                    return _handle_update(args, scheduler)
                elif fn == "cancel_task":
                    return _handle_cancel(args, scheduler)

        # LLM 没调工具但有文字回复（兜底）
        if msg.content and msg.content.strip():
            return msg.content.strip()

    except Exception as e:
        logger.error(f"[Task] 异常: {e}")

    return "请说具体的任务指令，例如：\n- 提醒我明天9点开会\n- 有哪些任务\n- 删除打卡任务"


def _handle_create(args, scheduler, user_name, user_id) -> str:
    """处理创建任务"""
    content = args.get("content", "")
    trigger_time = args.get("trigger_time", "")
    assignee = args.get("assignee", user_name)
    repeat = args.get("repeat", "once")
    repeat_days = args.get("repeat_days", [])
    group_name = args.get("group_name", "")

    if not content or not trigger_time:
        return "❌ 任务内容或时间不能为空。"

    scheduler.add_task(
        content=content, trigger_time=trigger_time,
        creator_name=user_name, creator_id=user_id,
        assignee_name=assignee or user_name, assignee_id=user_id,
        schedule_type=repeat, schedule_weekdays=repeat_days,
        group_name=group_name,
    )

    rpt_map = {"once": "", "daily": " (每天)", "weekly": " (每周)", "weekdays": " (工作日)"}
    rpt = rpt_map.get(repeat, "")
    grp = f" 📢{group_name}" if group_name else ""
    result = f"✅ 已记录：{content}（{trigger_time}{rpt}{grp}）"
    logger.info(f"[Task] 创建: {result}")
    return result


def _handle_query(args, scheduler) -> str:
    """处理查询任务"""
    status_filter = args.get("status", "active")
    user_filter = args.get("user", "")
    date_filter = args.get("date", "")
    keyword = args.get("keyword", "")
    upcoming_only = args.get("upcoming", False)

    all_tasks = scheduler.list_tasks()

    if status_filter == "active":
        tasks = [t for t in all_tasks if t.status == "active"]
    elif status_filter == "done":
        tasks = [t for t in all_tasks if t.status in ("done", "cancelled")]
    else:
        tasks = list(all_tasks)

    if user_filter:
        tasks = [t for t in tasks if user_filter in t.creator_name or user_filter in t.assignee_name]

    if keyword:
        tasks = [t for t in tasks if keyword in t.content]

    today_str = datetime.now().strftime("%Y-%m-%d")
    if date_filter == "today":
        tasks = [t for t in tasks if t.trigger_time and t.trigger_time.startswith(today_str)]
    elif date_filter == "tomorrow":
        tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        tasks = [t for t in tasks if t.trigger_time and t.trigger_time.startswith(tomorrow_str)]
    elif date_filter and date_filter != "":
        tasks = [t for t in tasks if t.trigger_time and t.trigger_time.startswith(date_filter)]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if upcoming_only:
        tasks = sorted([t for t in tasks if t.trigger_time > now_str], key=lambda t: t.trigger_time)[:5]

    tasks.sort(key=lambda t: t.trigger_time or "")

    if not tasks:
        return "没有找到符合条件的任务。"

    lines = [f"**任务列表（共 {len(tasks)} 个）**"]
    for t in tasks:
        lines.append("- " + _format_task_line(t))

    return "\n".join(lines)


def _handle_update(args, scheduler) -> str:
    """处理修改任务"""
    task_id = args.get("task_id", 0)
    if not task_id:
        return "❌ 请指定要修改的任务编号。"

    content = args.get("content")
    trigger_time = args.get("trigger_time")
    assignee = args.get("assignee")
    repeat = args.get("repeat")
    repeat_days = args.get("repeat_days")
    group_name = args.get("group_name")

    ok = scheduler.update_task(
        task_id=task_id,
        content=content,
        trigger_time=trigger_time,
        assignee_name=assignee,
        schedule_type=repeat,
        schedule_weekdays=repeat_days,
        group_name=group_name,
    )

    if not ok:
        return f"❌ 未找到任务 #{task_id} 或任务已取消。"

    # 用改后的内容做反馈
    updated = scheduler._get_task_by_id(task_id)
    if updated:
        return f"✅ 已更新任务 #{task_id}：{updated.content}（{updated.trigger_time}）"
    return f"✅ 已更新任务 #{task_id}。"


def _handle_cancel(args, scheduler) -> str:
    """处理取消任务，支持按ID、按关键词+用户+索引、批量"""
    task_id = args.get("task_id", 0)
    keyword = (args.get("keyword") or "").strip()
    user_filter = (args.get("user") or "").strip()
    task_index = args.get("task_index", 0)

    if task_id and task_id > 0:
        ok = scheduler.cancel_task(task_id)
        return f"✅ 已取消任务 #{task_id}" if ok else f"❌ 未找到任务 #{task_id}。"

    if not keyword:
        return "❌ 请指定要删除的任务编号或内容关键词。"

    all_tasks = scheduler.list_tasks()
    matched = []
    for t in all_tasks:
        if t.status != "active":
            continue
        if keyword not in t.content:
            continue
        if user_filter and user_filter not in t.creator_name and user_filter not in t.assignee_name:
            continue
        matched.append(t)

    if not matched:
        parts = [f"内容包含'{keyword}'"]
        if user_filter:
            parts.append(f"责任人是'{user_filter}'")
        return f"❌ 未找到{'且'.join(parts)}的待办任务。"

    matched.sort(key=lambda t: t.trigger_time or "")

    if task_index and task_index > 0:
        if task_index > len(matched):
            return f"❌ 只找到 {len(matched)} 个，没有第 {task_index} 个。"
        t = matched[task_index - 1]
        scheduler.cancel_task(t.id)
        return f"✅ 已取消第 {task_index} 个任务 #{t.id}（{t.content}）"

    cancelled_ids = []
    for t in matched:
        if scheduler.cancel_task(t.id):
            cancelled_ids.append(t.id)

    if len(cancelled_ids) == 1:
        return f"✅ 已取消任务 #{cancelled_ids[0]}（{matched[0].content}）"
    else:
        return f"✅ 共取消 {len(cancelled_ids)} 个任务：{', '.join('#' + str(i) for i in cancelled_ids)}"


def _format_schedule(task) -> str:
    """格式化周期信息为可读文本"""
    rpt_map = {"once": "单次", "daily": "每天", "weekdays": "工作日(周一到周五)", "weekly": "每周"}
    base = rpt_map.get(task.schedule_type, task.schedule_type)
    if task.schedule_type == "weekly" and task.schedule_weekdays:
        wd_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        days = []
        for d_str in task.schedule_weekdays.split(","):
            d_str = d_str.strip()
            if d_str.isdigit():
                idx = int(d_str)
                if 0 <= idx <= 6:
                    days.append(wd_map[idx])
        if days:
            base = "每周" + "、".join(days)
    return base


def _format_task_line(task) -> str:
    """格式化单行任务"""
    if task.status == "done":
        icon = "✅"
    elif task.status == "cancelled":
        icon = "❌"
    elif task.trigger_time and task.trigger_time[:16] <= datetime.now().strftime("%Y-%m-%d %H:%M"):
        icon = "⚠️"
    else:
        icon = "⏳"
    try:
        short_time = task.trigger_time[5:16] if task.trigger_time and len(task.trigger_time) >= 16 else (task.trigger_time or "未设置")
    except:
        short_time = task.trigger_time or "未设置"
    assignee = f" 👤{task.assignee_name}" if task.assignee_name else ""
    group = f" 📢{task.group_name}" if task.group_name else ""
    schedule = f" 🔄{_format_schedule(task)}"
    return f"{icon} #{task.id} {short_time} {task.content}{assignee}{group}{schedule}"


def _format_task_detail(task) -> str:
    """格式化任务详情"""
    if task.status == "done":
        status_str = "✅ 已完成"
    elif task.status == "cancelled":
        status_str = "❌ 已取消"
    elif task.trigger_time and task.trigger_time[:16] <= datetime.now().strftime("%Y-%m-%d %H:%M"):
        status_str = "⚠️ 已过期"
    else:
        status_str = "⏳ 待办"

    lines = [
        f"📋 **任务 #{task.id}**",
        f"内容：{task.content}",
        f"状态：{status_str}",
        f"时间：{task.trigger_time}",
        f"周期：{_format_schedule(task)}",
    ]
    if task.creator_name:
        lines.append(f"创建人：👤 {task.creator_name}")
    if task.assignee_name:
        lines.append(f"责任人：👤 {task.assignee_name}")
    if task.group_name:
        lines.append(f"推送群：📢 {task.group_name}")
    if task.last_triggered:
        lines.append(f"上次触发：{task.last_triggered}")

    return "\n".join(lines)