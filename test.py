from channel.dingtalk.dingtalk_groups_manager import GroupManager
from plugins.daily_news.daily_news_subscribed_group_manager import SubscribedGroupManager

group = GroupManager()
subscribed = SubscribedGroupManager()

print("All Groups:", group.get_all_groups())
print("Subscribed Groups:", subscribed.get_all_subscribed_groups())
print("ID:", group.get_group_ids_by_names(subscribed.get_all_subscribed_groups()))