import requests

def leave_chat(chat_id, bot_token):
    method = 'leaveChat'
    url = f'https://api.telegram.org/bot{bot_token}/{method}'
    params = {'chat_id': chat_id}
    response = requests.post(url, json=params)
    return response.json()

chat_id = 123456789  # ID do grupo que você deseja que o bot saia
bot_token = 'your_bot_token'  # Token do seu bot

result = leave_chat(chat_id, bot_token)
print(result)

