import requests
import json

url = "http://localhost:3001/api/v2/workflow/invoke"

payload = json.dumps({
   "workflow_id": "fa54d6df5cf24fa1a760bfad6f0d6f3c",
   "stream": False, # 是否请求流式返回工作流事件，默认为 True。本示例为了直观展示返回结果，所以改为False 使用非流式请求，真实业务场景中为了用户体验建议请求流式返回。
})

headers = {
   'Content-Type': 'application/json'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)# 输出工作流的响应