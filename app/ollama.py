import json
import os
from time import perf_counter
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError


MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
BASE_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11435").rstrip("/")


class ModelCall(TypedDict, total=False):
    model: str
    duration_ms: float
    input_tokens: int | None
    output_tokens: int | None
    load_ms: float | None
    prompt_ms: float | None
    generation_ms: float | None
    error: str


class ModelError(RuntimeError):
    def __init__(self, role, message, calls):
        super().__init__(message)
        self.role = role
        self.calls = calls


def milliseconds(value):
    return round(value / 1_000_000, 3) if value is not None else None


def generate(role, instruction, data, result_type, schema, validate):
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "format": schema,
        "options": {"temperature": 0.2, "num_ctx": 12288, "num_predict": 3500},
    }
    calls: list[ModelCall] = []
    for attempt in range(2):
        started = perf_counter()
        try:
            request = Request(
                f"{BASE_URL}/api/chat",
                json.dumps(payload, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=600) as response:
                reply = json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            message = f"Ollama недоступна или вернула ошибку ({error})."
            calls.append({"model": MODEL, "duration_ms": round((perf_counter() - started) * 1000, 3), "error": message})
            raise ModelError(role, message, calls) from error

        call: ModelCall = {
            "model": reply.get("model", MODEL),
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "input_tokens": reply.get("prompt_eval_count"),
            "output_tokens": reply.get("eval_count"),
            "load_ms": milliseconds(reply.get("load_duration")),
            "prompt_ms": milliseconds(reply.get("prompt_eval_duration")),
            "generation_ms": milliseconds(reply.get("eval_duration")),
        }
        calls.append(call)
        try:
            if reply.get("done_reason") == "length":
                raise ValueError("ответ превысил лимит выходных токенов")
            result = result_type.model_validate_json(reply.get("message", {}).get("content", ""))
            validate(result)
            return result, calls
        except ValidationError as error:
            issue = error.errors(include_input=False, include_context=False)[0]
            location = ".".join(str(part) for part in issue["loc"])
            call["error"] = f"{location}: {issue['msg']}" if location else issue["msg"]
        except ValueError as error:
            call["error"] = str(error)
        if attempt == 0:
            messages.append({
                "role": "user",
                "content": f"Исправь ошибку предыдущей попытки: {call['error']}. Верни только полный JSON по схеме.",
            })
    raise ModelError(role, f"{MODEL} дважды вернула некорректный результат. {calls[-1]['error']}", calls)
