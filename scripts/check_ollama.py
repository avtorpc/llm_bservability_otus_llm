"""Measure Qwen3 generation with a small Russian SMM brief."""

import argparse
import json
import sys
from time import perf_counter
from urllib.request import Request, urlopen


# Общий бриф связывает черновик с редакторской проверкой. Повторный вызов
# копирайтера использует те же сообщения, чтобы сравнить одинаковые запросы.
BRIEF = (
    "Московская кофейня «Зерно». Пост для VK о сезонном сливочном рафе. "
    "Аудитория — местные жители 25–40 лет. Тон живой и спокойный. "
    "Не придумывай цены и адреса. Призыв — зайти на чашку рафа."
)

# Схема передаётся Ollama в поле format и задаёт форму ответа модели.
# required требует указанные поля, additionalProperties запрещает лишние.
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"post": {"type": "string", "minLength": 1}},
    "required": ["post"],
    "additionalProperties": False,
}

# Это схема исходного замера из TEST_LOCAL_LLM.md. Она требует замечание при
# любом значении approved и позволяет воспроизвести обнаруженное противоречие.
# Строгий контракт будущего графа описан в отчёте отдельно.
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "comment": {"type": "string", "minLength": 1},
    },
    "required": ["approved", "comment"],
    "additionalProperties": False,
}


def request(base_url, path, payload=None):
    # urllib выбирает GET при отсутствии тела и POST при наличии JSON.
    # Тайм-аут задаётся для блокирующих операций сокета, включая чтение потока.
    body = None if payload is None else json.dumps(payload).encode()
    return urlopen(
        Request(f"{base_url}{path}", data=body, headers={"Content-Type": "application/json"}),
        timeout=300,
    )


def metadata(base_url, path):
    # Служебные эндпоинты возвращают один JSON-документ. Контекстный менеджер
    # закрывает HTTP-ответ после чтения, в том числе при исключении.
    with request(base_url, path) as response:
        return json.load(response)


def seconds(event, field):
    # Ollama сообщает длительности в наносекундах. Отсутствующее поле
    # сохраняется как None, чтобы в отчёте отличать его от замеренного нуля.
    value = event.get(field)
    return None if value is None else round(value / 1e9, 4)


def measure(base_url, model, name, messages, schema):
    # Поток позволяет измерить время до первого текста. Thinking отключён,
    # а keep_alive удерживает модель в памяти между запросами. Одинаковые
    # параметры генерации используются для всех трёх замеров.
    payload = {
        "model": model,
        "messages": messages,
        "format": schema,
        "stream": True,
        "think": False,
        "keep_alive": "10m",
        # seed и температура обеспечивают воспроизводимые условия. num_ctx
        # задаёт размер контекста, num_predict ограничивает выходные токены.
        "options": {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 200},
    }
    chunks = []
    first_text = None
    final = None
    # Монотонный таймер клиента включает отправку HTTP-запроса и ожидание
    # ответа. Изменение системного времени не влияет на разницу показаний.
    start = perf_counter()
    with request(base_url, "/api/chat", payload) as response:
        # В потоковом ответе каждая строка содержит отдельное JSON-событие.
        # Поле content может содержать часть JSON, формируемого моделью.
        for line in response:
            event = json.loads(line)
            if "error" in event:
                raise RuntimeError(event["error"])
            content = event.get("message", {}).get("content", "")
            if content:
                # Отсчитываем время до первого непустого фрагмента content.
                # Фрагмент может содержать несколько токенов или символ JSON.
                if first_text is None:
                    first_text = round(perf_counter() - start, 4)
                chunks.append(content)
            # Завершающее событие содержит счётчики токенов и тайминги Ollama.
            if event.get("done"):
                final = event
                break
    elapsed = round(perf_counter() - start, 4)
    if final is None:
        raise RuntimeError("Ollama closed the stream before sending final metrics")

    # Склеиваем фрагменты без разделителя и разбираем JSON модели отдельно
    # от JSON-событий HTTP-потока. Проверяем точный набор полей, их типы
    # и наличие текста после удаления пробелов по краям.
    raw = "".join(chunks)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name}: invalid JSON: {raw!r}") from error
    if schema is DRAFT_SCHEMA:
        valid = isinstance(result, dict) and result.keys() == {"post"} and isinstance(result["post"], str) and bool(result["post"].strip())
    else:
        valid = isinstance(result, dict) and result.keys() == {"approved", "comment"} and isinstance(result["approved"], bool) and isinstance(result["comment"], str) and bool(result["comment"].strip())
    if not valid:
        raise ValueError(f"{name}: response failed schema checks: {result!r}")

    # eval_count и eval_duration относятся к генерации выходных токенов.
    # Скорость считается по чистому времени генерации. Клиентская задержка
    # и время загрузки модели в этот расчёт не входят.
    generated = final.get("eval_count")
    generation_ns = final.get("eval_duration")
    report = {
        "case": name,
        "response": result,
        "done_reason": final.get("done_reason"),
        "client_elapsed_s": elapsed,
        "first_text_s": first_text,
        "server_total_s": seconds(final, "total_duration"),
        "model_load_s": seconds(final, "load_duration"),
        "prompt_eval_s": seconds(final, "prompt_eval_duration"),
        "generation_s": seconds(final, "eval_duration"),
        "input_tokens": final.get("prompt_eval_count"),
        # Версия сервера или конкретный ответ могут не содержать счётчик
        # кешированных токенов. В итоговом JSON это поле будет равно null.
        "cached_input_tokens": final.get("prompt_eval_cached_count"),
        "output_tokens": generated,
        "output_tokens_per_s": round(generated * 1e9 / generation_ns, 2) if generated is not None and generation_ns else None,
    }
    # stderr показывает прогресс сразу после каждого запроса. stdout
    # остаётся пригодным для перенаправления итогового отчёта в JSON-файл.
    print(f"{name}: {elapsed:.2f}s, {report['input_tokens']} in / {generated} out", file=sys.stderr, flush=True)
    return report


def main():
    # Значения по умолчанию соответствуют compose.yaml и модели из тома.
    # Аргументы позволяют проверить тот же сценарий на другом сервере Ollama.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:11435")
    parser.add_argument("--model", default="qwen3:4b")
    args = parser.parse_args()
    base_url = args.url.rstrip("/")
    # /api/tags перечисляет модели, доступные на диске. /api/ps показывает
    # модели в памяти и позволяет установить, был ли первый вызов холодным.
    version = metadata(base_url, "/api/version")["version"]
    models = metadata(base_url, "/api/tags")["models"]
    model = next((item for item in models if item["name"] == args.model), None)
    if model is None:
        parser.error(f"model {args.model!r} is absent from the Ollama model cache")
    loaded_before = any(item["name"] == args.model for item in metadata(base_url, "/api/ps")["models"])
    # Два одинаковых вызова копирайтера идут подряд. Время повторного вызова
    # может отражать и прогретую модель, и кеш обработки того же промпта.
    draft_messages = [
        {"role": "system", "content": "Ты копирайтер. Напиши две короткие фразы для поста. Верни JSON с полем post."},
        {"role": "user", "content": BRIEF},
    ]
    first = measure(base_url, args.model, "copywriter_first", draft_messages, DRAFT_SCHEMA)
    repeated = measure(base_url, args.model, "copywriter_repeat", draft_messages, DRAFT_SCHEMA)
    # Редактор получает исходный бриф и фактический черновик второго вызова.
    # Его замер показывает обработку нового промпта на загруженной модели.
    review_messages = [
        {"role": "system", "content": "Ты редактор. Проверь бриф и черновик. Верни approved и одно короткое конкретное замечание в comment."},
        {"role": "user", "content": f"Бриф: {BRIEF}\nЧерновик: {repeated['response']['post']}"},
    ]
    review = measure(base_url, args.model, "editor", review_messages, REVIEW_SCHEMA)
    # В отчёте сохраняем версию Ollama, метаданные модели, результаты трёх
    # вызовов и состояние загруженных моделей в конце теста.
    print(json.dumps({
        "ollama_version": version,
        "model": model,
        "model_loaded_before_first_call": loaded_before,
        "runs": [first, repeated, review],
        "loaded_models_after_test": metadata(base_url, "/api/ps")["models"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
