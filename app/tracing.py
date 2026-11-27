import os
from contextlib import nullcontext

from langfuse import Langfuse


client = (
    Langfuse()
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
    else None
)


def start_run(task):
    if client is None:
        return None
    return client.start_observation(name="ai-smm.run", as_type="span", input=task)


def trace_url(trace_id):
    ui_url = os.getenv("LANGFUSE_UI_URL")
    project_id = os.getenv("LANGFUSE_PROJECT_ID")
    return f"{ui_url.rstrip('/')}/project/{project_id}/traces/{trace_id}" if ui_url and project_id else None


def agent_span(role, parent, version):
    if client is None or parent is None:
        return nullcontext()
    return client.start_as_current_observation(
        name=role,
        as_type="span",
        trace_context=parent,
        metadata={"draft_version": version},
    )


def model_generation(model, payload, attempt):
    if client is None or client.get_current_observation_id() is None:
        return nullcontext()
    return client.start_as_current_observation(
        name="ollama.chat",
        as_type="generation",
        model=model,
        input=payload["messages"],
        model_parameters={**payload["options"], "think": payload["think"]},
        metadata={"attempt": attempt},
    )
