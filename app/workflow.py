from time import perf_counter
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, StringConstraints, model_validator


Role = Literal["strategist", "copywriter", "editor", "publisher"]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Brief = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=8000)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
DEFAULT_ROLES = {
    "strategist": "Составь контент-план по брифу и выбери темы для готовых постов.",
    "copywriter": "Напиши черновики по ТЗ. При возврате исправь посты, указанные редактором.",
    "editor": "Проверь комплект по брифу. Верни «Принято» или «Не принято» с обязательными правками.",
    "publisher": "Оформи одобренные тексты для VK. Сохрани утверждённые факты и призыв.",
}


class RunInput(BaseModel):
    topic: Topic
    brief: Brief
    days: Annotated[int, Field(ge=1, le=14)] = 7
    post_count: Annotated[int, Field(ge=1, le=7)] = 3
    max_revisions: Annotated[int, Field(ge=0, le=5)] = 3
    roles: dict[Role, Prompt]

    @model_validator(mode="after")
    def validate_settings(self):
        if self.post_count > self.days:
            raise ValueError("Число постов должно быть не больше числа дней")
        if self.roles.keys() != DEFAULT_ROLES.keys():
            raise ValueError("Укажите инструкции всех четырёх ролей")
        return self


class PlanItem(TypedDict):
    post_id: int
    title: str
    task: str
    selected: bool


class Post(TypedDict):
    post_id: int
    title: str
    text: str


class Edit(TypedDict):
    post_id: int
    instruction: str


class Review(TypedDict):
    approved: bool
    edits: list[Edit]


class RunState(TypedDict, total=False):
    task: RunInput
    plan: list[PlanItem]
    drafts: list[Post]
    version: int
    review: Review
    ready: list[Post]
    duration_ms: float


def strategist(state: RunState):
    task = state["task"]
    formats = ("Знакомство", "История", "Совет", "Закулисье", "Вопрос", "Подборка", "Итоги")
    plan = [
        {
            "post_id": day,
            "title": f"{formats[(day - 1) % len(formats)]} · {task.topic}",
            "task": f"Подготовить пост для VK по теме «{task.topic}» с учётом брифа.",
            "selected": day <= task.post_count,
        }
        for day in range(1, task.days + 1)
    ]
    return {"plan": plan}


def copywriter(state: RunState):
    task = state["task"]
    if "drafts" in state:
        edits = {edit["post_id"]: edit for edit in state["review"]["edits"]}
        drafts = [
            {**draft, "text": draft["text"].replace("Не упустите шанс!", "Загляните к нам, когда будет удобно.")}
            if draft["post_id"] in edits else draft
            for draft in state["drafts"]
        ]
    else:
        drafts = [
            {
                "post_id": item["post_id"],
                "title": item["title"],
                "text": f"{item['title']}. {task.brief}\n\n"
                + ("Не упустите шанс!" if item["post_id"] == 1 else "Поделитесь своим мнением в комментариях."),
            }
            for item in state["plan"] if item["selected"]
        ]
    return {"drafts": drafts, "version": state.get("version", 0) + 1}


def editor(state: RunState):
    edits = [
        {"post_id": post["post_id"], "instruction": "Замени рекламный призыв на спокойное приглашение."}
        for post in state["drafts"] if post["text"].endswith("Не упустите шанс!")
    ]
    return {"review": {"approved": not edits, "edits": edits}}


def publisher(state: RunState):
    return {"ready": [{**post, "text": f"{post['text']}\n\n#ПостДляVK"} for post in state["drafts"]]}


def after_editor(state: RunState) -> Literal["publisher", "copywriter", "__end__"]:
    if state["review"]["approved"]:
        return "publisher"
    if state["version"] - 1 < state["task"].max_revisions:
        return "copywriter"
    return END


def timed(node):
    def step(state: RunState):
        start = perf_counter()
        output = node(state)
        return {**output, "duration_ms": round((perf_counter() - start) * 1000, 3)}

    return step


builder = StateGraph(RunState)
builder.add_node("strategist", timed(strategist))
builder.add_node("copywriter", timed(copywriter))
builder.add_node("editor", timed(editor))
builder.add_node("publisher", timed(publisher))
builder.add_edge(START, "strategist")
builder.add_edge("strategist", "copywriter")
builder.add_edge("copywriter", "editor")
builder.add_conditional_edges("editor", after_editor)
builder.add_edge("publisher", END)
graph = builder.compile()
