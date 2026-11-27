import json
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.ollama import MODEL, ModelError
from app.tracing import start_run, trace_url
from app.workflow import RunInput, graph


APP_DIR = Path(__file__).parent
WEB = APP_DIR / "web"
DEFAULT_TASK = RunInput.model_validate_json((APP_DIR / "default_task.json").read_text(encoding="utf-8"))
app = FastAPI(title="AI SMM-отдел")
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/", include_in_schema=False)
def index():
    template = (WEB / "index.html").read_text(encoding="utf-8")
    task_json = DEFAULT_TASK.model_dump_json().replace("<", r"\u003c")
    return HTMLResponse(template.replace("__DEFAULT_TASK__", task_json, 1), headers={"Cache-Control": "no-store"})


@app.get("/api/defaults")
def defaults():
    return DEFAULT_TASK.roles


@app.post("/api/runs", response_class=StreamingResponse)
def run(task: RunInput):
    def events():
        def line(event):
            return json.dumps(event, ensure_ascii=False) + "\n"

        start = perf_counter()
        state = {"task": task}
        input_data = task.model_dump()
        observation = start_run(input_data)
        parent = {"trace_id": observation.trace_id, "parent_span_id": observation.id} if observation else None
        url = trace_url(observation.trace_id) if observation else None
        try:
            yield line({"type": "start", "input": input_data, "model": MODEL, "trace_url": url})
            for update in graph.stream(state, stream_mode="updates", context={"trace_parent": parent}):
                for role, output in update.items():
                    state.update(output)
                    yield line({"type": "step", "role": role, "output": output})
            status = "revision_limit"
            if state.get("revision_unchanged"):
                status = "no_progress"
            elif state.get("ready"):
                status = "completed"
            summary = {
                "status": status,
                "elapsed_ms": round((perf_counter() - start) * 1000, 3),
                "revisions": state.get("version", 1) - 1,
            }
            if observation is not None:
                observation.update(output=summary)
            yield line({"type": "finish", **summary})
        except ModelError as error:
            if observation is not None:
                observation.update(level="ERROR", status_message=str(error), output={"failed_role": error.role})
            yield line({
                "type": "error", "role": error.role, "message": str(error), "calls": error.calls,
                "elapsed_ms": round((perf_counter() - start) * 1000, 3),
            })
        except Exception as error:
            if observation is not None:
                observation.update(level="ERROR", status_message=type(error).__name__)
            raise
        finally:
            if observation is not None:
                observation.end()

    return StreamingResponse(events(), media_type="application/x-ndjson")
