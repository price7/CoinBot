import requests
 
def post_message(token, channel, text):
    response = requests.post("https://slack.com/api/chat.postMessage",
        headers={"Authorization": "Bearer "+token},
        data={"channel": channel,"text": text}
    )
    print(response)
 
appToken = "xoxb-9186846597955-9191819770770-KuNtHaV729KJRGLVNU6zIx0V"
 
post_message(appToken,"#코인봇-테스트","test message!")