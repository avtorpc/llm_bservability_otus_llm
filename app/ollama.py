import json
import os
from time import perf_counter
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.tracing import model_generation


MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
BASE_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11435").rstrip("/")
READ_TIMEOUT_SECONDS = 600


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


def read_reply(payload):
    request = Request(
        f"{BASE_URL}/api/chat",
        json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    chunks = []
    with urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
        for line in response:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("error"):
                raise ValueError(event["error"])
            chunks.append(event.get("message", {}).get("content", ""))
            if event.get("done"):
                return event, "".join(chunks)
    raise ValueError("поток закрылся до итогового события Ollama")


def generate(role, instruction, data, result_type, schema, validate, *, max_tokens=3500):
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": "10m",
        "format": schema,
        "options": {"temperature": 0.2, "num_ctx": 12288, "num_predict": max_tokens},
    }
    calls: list[ModelCall] = []
    for attempt in range(2):
        with model_generation(MODEL, payload, attempt + 1) as observation:
            started = perf_counter()
            try:
                reply, content = read_reply(payload)
            except (HTTPError, URLError, TimeoutError, ValueError) as error:
                reason = error.reason if isinstance(error, URLError) else error
                if isinstance(reason, TimeoutError):
                    message = f"Ollama не присылала данные {READ_TIMEOUT_SECONDS} секунд."
                else:
                    message = f"Ollama недоступна или вернула ошибку ({error})."
                calls.append({"model": MODEL, "duration_ms": round((perf_counter() - started) * 1000, 3), "error": message})
                if observation is not None:
                    observation.update(level="ERROR", status_message=message)
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
            done_reason = reply.get("done_reason")
            if observation is not None:
                usage = {key: value for key, value in (('input', call['input_tokens']), ('output', call['output_tokens'])) if value is not None}
                observation.update(
                    model=call["model"], output=content, usage_details=usage, cost_details={"total": 0.0},
                    metadata={key: call[key] for key in ("load_ms", "prompt_ms", "generation_ms", "duration_ms")}
                    | {"done_reason": done_reason, "attempt": attempt + 1},
                )
            try:
                if done_reason == "length":
                    raise ValueError("ответ превысил лимит выходных токенов")
                result = result_type.model_validate_json(content)
                validate(result)
                return result, calls
            except ValidationError as error:
                issue = error.errors(include_input=False, include_context=False)[0]
                location = ".".join(str(part) for part in issue["loc"])
                call["error"] = f"{location}: {issue['msg']}" if location else issue["msg"]
            except ValueError as error:
                call["error"] = str(error)
            if observation is not None:
                observation.update(level="WARNING" if attempt == 0 else "ERROR", status_message=call["error"])
        if attempt == 0:
            messages.append({
                "role": "user",
                "content": f"Исправь ошибку предыдущей попытки: {call['error']}. Верни только полный JSON по схеме.",
            })
    raise ModelError(role, f"{MODEL} дважды вернула некорректный результат. {calls[-1]['error']}", calls)
