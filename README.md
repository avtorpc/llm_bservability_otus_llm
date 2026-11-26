# AI SMM-отдел

В проекте два сервиса Docker Compose. `ollama` запускает модель `qwen3:4b`, а `python` запускает тестовые скрипты и обращается к Ollama по внутреннему адресу `http://ollama:11434`.

## Запуск на чистой машине

Понадобятся Docker и Docker Compose v2. Первый запуск требует доступа к реестрам образов и Ollama для загрузки модели объёмом около 2,5 ГБ. Веса сохраняются в томе `llm-observability_model_cache`. При следующем запуске Ollama использует их из тома.

Выполните эти команды из корня репозитория.

```sh
docker compose up --build -d --wait --wait-timeout 900
docker compose ps
docker compose exec -T python python scripts/check_ollama.py --url http://ollama:11434
```

Python-контейнер запускается после того, как Ollama подтвердит наличие модели. Скрипт пишет промежуточные сообщения в `stderr`, а JSON с замерами — в `stdout`. Вывод `docker compose logs -f ollama` показывает состояние загрузки модели.

При запуске теста из PyCharm на хосте используйте адрес `http://127.0.0.1:11435`, заданный в скрипте по умолчанию. После редактирования скриптов пересоберите Python-образ командой `docker compose up --build -d python`.

Команда `docker compose down` останавливает оба контейнера и сохраняет том с моделью.

Результаты первого теста и правило принятия решения редактором приведены в [TEST_LOCAL_LLM.md](TEST_LOCAL_LLM.md).
