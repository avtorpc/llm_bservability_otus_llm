import re
from time import perf_counter
from typing import Annotated, Literal, TypedDict, get_args

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langfuse.types import TraceContext
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ollama import ModelCall, generate
from app.tracing import agent_span


Role = Literal["strategist", "copywriter", "editor", "publisher"]
MIN_POST_WORDS = 30
MAX_POST_WORDS = 70
POST_WORDS = re.compile(r"[^\W_]+(?:[-‑][^\W_]+)*")
HASHTAG = re.compile(r"(?<!\w)#\w+")
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Brief = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=8000)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


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
        if self.roles.keys() != set(get_args(Role)):
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
    revision_unchanged: bool
    review: Review
    ready: list[Post]
    duration_ms: float
    calls: list[ModelCall]


class RunContext(TypedDict):
    trace_parent: TraceContext | None


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
PostText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1800)]
PostId = Annotated[int, Field(strict=True, ge=1, le=14)]


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DayPlan(Output):
    title: ShortText
    task: ShortText


class Strategy(Output):
    days: list[DayPlan]
    selected_days: list[PostId]


class Draft(Output):
    post_id: PostId
    text: PostText


class Drafts(Output):
    posts: list[Draft]


class RequiredEdit(Output):
    post_id: PostId
    instruction: ShortText


class EditorialDecision(Output):
    approved: Annotated[bool, Field(strict=True)]
    edits: list[RequiredEdit]

    @model_validator(mode="after")
    def validate_decision(self):
        if self.approved == bool(self.edits):
            raise ValueError("при approved=true edits должны быть пустыми, при approved=false нужна обязательная правка")
        return self


class Tags(Output):
    post_id: PostId
    hashtags: Annotated[list[ShortText], Field(min_length=1, max_length=3)]


class Publication(Output):
    posts: list[Tags]


def exact_count(schema, field, count):
    schema["properties"][field].update(minItems=count, maxItems=count)


def require_ids(items, expected):
    if sorted(item.post_id for item in items) != sorted(expected):
        raise ValueError(f"укажи каждый post_id из списка {sorted(expected)} ровно один раз")


def body_words(text):
    return POST_WORDS.findall(HASHTAG.sub("", text))


def ask(state, role, contract, data, result_type, schema, validate, *, max_tokens=3500):
    task = state["task"]
    instruction = f"{task.roles[role]}\n\nОбязательный формат: {contract} Ответь только JSON по заданной схеме."
    return generate(
        role, instruction, {"тема": task.topic, "бриф": task.brief, **data}, result_type, schema, validate,
        max_tokens=max_tokens,
    )


def strategist(state: RunState):
    task = state["task"]
    schema = Strategy.model_json_schema()
    exact_count(schema, "days", task.days)
    exact_count(schema, "selected_days", task.post_count)

    def validate(result):
        if len(result.days) != task.days or len(result.selected_days) != task.post_count:
            raise ValueError(f"нужно ровно {task.days} дней и {task.post_count} выбранных тем")
        if len(set(result.selected_days)) != task.post_count or max(result.selected_days) > task.days:
            raise ValueError(f"selected_days содержит уникальные номера дней от 1 до {task.days}")

    result, calls = ask(
        state, "strategist",
        f"Верни ровно {task.days} элементов days по порядку. В selected_days укажи {task.post_count} разных номеров с 1. "
        "Для каждого дня дай только заголовок до 6 слов и ТЗ одним предложением до 15 слов, без пояснений.",
        {"дни": task.days, "посты": task.post_count}, Strategy, schema, validate,
        max_tokens=160 + 100 * task.days,
    )
    plan = [
        {**day.model_dump(), "post_id": number, "selected": number in result.selected_days}
        for number, day in enumerate(result.days, start=1)
    ]
    return {"plan": plan, "calls": calls}


def copywriter(state: RunState):
    titles = {item["post_id"]: item["title"] for item in state["plan"] if item["selected"]}
    drafts = {post["post_id"]: post for post in state.get("drafts", [])}
    edits = state.get("review", {}).get("edits", [])
    expected = {edit["post_id"] for edit in edits} if drafts else set(titles)
    schema = Drafts.model_json_schema()
    exact_count(schema, "posts", len(expected))
    contract = f"Верни тексты только для post_id {sorted(expected)}, каждый один раз. Примени все переданные обязательные правки."
    if edits:
        contract += (
            " Обязательные правки относятся к новой версии. Выполни каждую из них. "
            "При правке длины напиши текст заново по ТЗ и брифу. Не копируй в пост инструкции из брифа."
        )
    result, calls = ask(
        state, "copywriter", contract,
        {"ТЗ": [item for item in state["plan"] if item["post_id"] in expected],
         "черновики": [drafts[post_id] for post_id in sorted(expected) if post_id in drafts], "правки": edits},
        Drafts, schema, lambda output: require_ids(output.posts, expected),
    )
    unchanged = bool(drafts) and all(
        post.text.split() == drafts[post.post_id]["text"].split() for post in result.posts
    )
    drafts.update({post.post_id: {**post.model_dump(), "title": titles[post.post_id]} for post in result.posts})
    return {
        "drafts": [drafts[post_id] for post_id in titles], "version": state.get("version", 0) + 1,
        "revision_unchanged": unchanged, "calls": calls,
    }


def editor(state: RunState):
    if state.get("revision_unchanged"):
        return {"review": state["review"], "revision_unchanged": True, "calls": []}

    edits: list[Edit] = []
    for post in state["drafts"]:
        count = len(body_words(post["text"]))
        if not MIN_POST_WORDS <= count <= MAX_POST_WORDS:
            edits.append({
                "post_id": post["post_id"],
                "instruction": (
                    f"Сейчас слов в тексте: {count}. Перепиши основной текст в пределах "
                    f"{MIN_POST_WORDS}–{MAX_POST_WORDS} слов, ориентируйся на 40–55 слов. "
                    "Разбей текст на 4–5 содержательных предложений. Сохрани факты из брифа и призыв к действию."
                ),
            })
    if edits:
        return {"review": {"approved": False, "edits": edits}, "calls": []}

    expected = {post["post_id"] for post in state["drafts"]}

    def validate(result):
        if any(edit.post_id not in expected for edit in result.edits):
            raise ValueError(f"правки должны относиться только к post_id {sorted(expected)}")

    result, calls = ask(
        state, "editor",
        "Принято: approved=true, edits=[]. Не принято: approved=false, edits содержит только обязательные правки с post_id и instruction.",
        {"ТЗ": [item for item in state["plan"] if item["selected"]], "черновики": state["drafts"],
         "предыдущие правки": state.get("review", {}).get("edits", [])},
        EditorialDecision, EditorialDecision.model_json_schema(), validate,
    )
    return {"review": result.model_dump(), "calls": calls}


def publisher(state: RunState):
    expected = {post["post_id"] for post in state["drafts"]}
    schema = Publication.model_json_schema()
    exact_count(schema, "posts", len(expected))

    def validate(result):
        require_ids(result.posts, expected)
        if any(not re.fullmatch(r"#\w+", tag) for post in result.posts for tag in post.hashtags):
            raise ValueError("каждый хештег начинается с # и не содержит пробелов или знаков препинания")

    result, calls = ask(
        state, "publisher",
        f"Верни только post_id и hashtags для каждого из {sorted(expected)}. Каждый тег начинается с # и не содержит пробелов.",
        {"одобренные посты": state["drafts"]}, Publication, schema, validate,
    )
    tags = {post.post_id: " ".join(dict.fromkeys(post.hashtags)) for post in result.posts}
    ready = [{**post, "text": f"{post['text']}\n\n{tags[post['post_id']]}"} for post in state["drafts"]]
    return {"ready": ready, "calls": calls}


def after_editor(state: RunState) -> Literal["publisher", "copywriter", "__end__"]:
    if state.get("revision_unchanged"):
        return END
    if state["review"]["approved"]:
        return "publisher"
    if state["version"] - 1 < state["task"].max_revisions:
        return "copywriter"
    return END


def timed(node):
    def step(state: RunState, runtime: Runtime[RunContext]):
        start = perf_counter()
        parent = runtime.context.get("trace_parent") if runtime.context else None
        version = state.get("version", 0) + (node is copywriter)
        with agent_span(node.__name__, parent, version) as observation:
            output = node(state)
            duration = round((perf_counter() - start) * 1000, 3)
            if observation is not None:
                observation.update(output={key: value for key, value in output.items() if key != "calls"})
        return {**output, "duration_ms": duration}

    return step


builder = StateGraph(RunState, context_schema=RunContext)
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
