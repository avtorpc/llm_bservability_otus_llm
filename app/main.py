import json
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.workflow import DEFAULT_ROLES, RunInput, graph


WEB = Path(__file__).with_name("web")
app = FastAPI(title="AI SMM-отдел")
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/defaults")
def defaults():
    return DEFAULT_ROLES


@app.post("/api/runs", response_class=StreamingResponse)
def run(task: RunInput):
    def events():
        def line(event):
            return json.dumps(event, ensure_ascii=False) + "\n"

        start = perf_counter()
        state = {"task": task}
        yield line({"type": "start", "input": task.model_dump()})
        for update in graph.stream(state, stream_mode="updates"):
            for role, output in update.items():
                state.update(output)
                yield line({"type": "step", "role": role, "output": output})
        yield line({
            "type": "finish",
            "status": "completed" if state.get("ready") else "revision_limit",
            "elapsed_ms": round((perf_counter() - start) * 1000, 3),
            "revisions": state.get("version", 1) - 1,
        })

    return StreamingResponse(events(), media_type="application/x-ndjson")
