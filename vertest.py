def check_permissions(api_key, folder_id):
    """Проверка доступности Yandex GPT API"""
    import requests

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json"
    }

    data = {
        "modelUri": f"gpt://{folder_id}/yandexgpt/latest",
        "messages": [{"role": "user", "text": "test"}],
        "completionOptions": {"maxTokens": 10}
    }

    response = requests.post(url, headers=headers, json=data, timeout=10)

    if response.status_code == 200:
        return "✅ Права есть! API доступно"
    elif response.status_code == 403:
        return "❌ Нет прав. Нужна роль: ai.languageModels.user"
    elif response.status_code == 401:
        return "❌ Неверный API ключ"
    else:
        return f"⚠️ Другая ошибка: {response.status_code}"