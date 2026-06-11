from __future__ import annotations
from dotenv import load_dotenv
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END, MessagesState
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langgraph.store.memory import InMemoryStore
from langgraph.store.base import BaseStore

from typing import List, Annotated, TypedDict, Literal, Optional
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
import operator
from langgraph.types import Send
# from langchain_community.tools.tavily_search import TavilySearchResults
from datetime import date, timedelta
import os
from pathlib import Path
import re
from langchain_ollama import ChatOllama
from langchain_tavily import TavilySearch
from langchain_openai import ChatOpenAI
import streamlit as st

load_dotenv()

os.environ["OPEN_ROUTER_API_KEY"] = st.secrets["OPEN_ROUTER_API_KEY"]
os.environ["TAVILY_API_KEY"] = st.secrets["TAVILY_API_KEY"]

# model = ChatOllama(model="qwen3:8b")

model = ChatOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPEN_ROUTER_API_KEY"),
    model="nex-agi/nex-n2-pro:free"
)


class Task(BaseModel):
    id: int
    title: str
    goal: str = Field(..., description= "One sentence describing what the reader should be able to do/understand after this section.")
    bullets: List[str] = Field(..., min_length=3, max_length=5, description="3-5 concrete, non-overlapping subpoints to cover in this section.")
    target_words: int = Field(..., description="Target word count for this section (120-450).")
    tags: List[str] = Field(default_factory=list)
    requires_research: bool = False
    requires_citation: bool = False
    requires_code: bool = False


class Plan(BaseModel):
    blog_title: str = Field(..., description="Title for the Blog...")
    audience: str = Field(..., description="Who this blog is for...")
    tone: str = Field(..., description= "Writing tone (e.g. practical, crisp, ...)")
    blog_kind: Literal["explainer", "tutorial", "news_roundup", "comparison", "system_design"] = "explainer"
    constraints: List[str] = Field(default_factory=list)
    tasks: List[Task] = Field(..., description="List of different sections in the Blog...")


class EvidenceItem(BaseModel):
    title: str
    url: str
    published_at: Optional[str] = None
    snippet: Optional[str] = None
    source: Optional[str] = None

class RouterDecision(BaseModel):
    needs_research: bool
    mode: Literal["closed_book", "hybrid", "open_book"]
    queries: List[str] = Field(default_factory=list)

class EvidencePack(BaseModel):
    evidence: List[EvidenceItem] = Field(default_factory=list)

# class ImageSpec(BaseModel):
#     placeholder: str = Field(..., description="e.g. [[IMAGE_1]]")
#     filename: str = Field(..., description="Save under images/. e.g. qkv_flow.png")
#     alt: str
#     caption: str
#     prompt: str = Field(..., description="Prompt to send to the image model.")
#     size: Literal["1024x1024", "1024x1536", "1536x1024"] = "1024x1024"
#     quality: Literal["low", "medium", "high"] = "medium"

# class GlobalImagePlan(BaseModel):
#     md_with_placeholders: str
#     images: List[ImageSpec] = Field(default_factory=list)



class State(TypedDict):
    topic: str

    # routing / research
    mode: str
    needs_research: bool
    queries: List[str]
    evidence: List[EvidenceItem]
    plan: Optional[Plan]

    # workers
    sections: Annotated[List[tuple[int, str]], operator.add]
    
    # reducer / image
    merged_md: str
    # md_with_placeholders: str
    # image_specs: List[dict]
    final: str



# -----------------------------
# 3) Router (decide upfront)
# -----------------------------
ROUTER_SYSTEM = f"""You are a routing module for a technical blog planner. 
Your task is to generate an output that STRICTLY matches the required Pydantic schema.

IMPORTANT:
- Every required field MUST be present
- Never omit fields
- Use the exact field names from the schema
- Preserve correct datatypes
- Nested objects must be complete
- Lists must contain properly formed objects
- Do not invent fields not present in schema
- Do not return partial objects
- Do not stop generation midway
- Ensure all objects are fully completed

FIELD COMPLETENESS RULE:
If a field exists in the schema, it MUST appear in the output.

OUTPUT RULES:
- Return ONLY the structured output
- No explanations
- No markdown
- No commentary
- No prose outside the schema

The output will be parsed directly into a Pydantic model.
Invalid or incomplete fields will cause failure.


You are generating a RouterDecision object.


Requirements:
- needs_research: boolean
- mode: one of
  ["closed_book","hybrid","open_book"]
- queries: list of strings

Rules:
- If needs_research=false, queries may be empty.
- If needs_research=true, provide 3-10 high quality search queries.
- Never omit fields.

Decide whether web research is needed BEFORE planning.

Modes:
- closed_book (needs_research=false):
  Evergreen topics where correctness does not depend on recent facts (concepts, fundamentals).
- hybrid (needs_research=true):
  Mostly evergreen but needs up-to-date examples/tools/models to be useful.
- open_book (needs_research=true):
  Mostly volatile: weekly roundups, "this week", "latest", rankings, pricing, policy/regulation.

If needs_research=true:
- Output 3–10 high-signal queries.
- Queries should be scoped and specific (avoid generic queries like just "AI" or "LLM").
- If user asked for "last week/this week/latest", reflect that constraint IN THE QUERIES.
"""

def router_node(state: State) -> dict:
    
    topic = state["topic"]

    router_structured_model = model.with_structured_output(RouterDecision)

    decision = router_structured_model.invoke(
        [
            SystemMessage(content=ROUTER_SYSTEM),
            HumanMessage(content=f"Topic: {topic}"),
        ]
    )

   
    return {
        "needs_research": decision.needs_research,
        "mode": decision.mode,
        "queries": decision.queries,
    }

def route_next(state: State) -> str:
    return "research" if state["needs_research"] else "orchestrator"



# -----------------------------
# 4) Research (Tavily) 
# -----------------------------
def _tavily_search(query: str, max_results: int = 5) -> List[dict]:

    tool = TavilySearch(max_results=max_results)

    response = tool.invoke({"query": query})

    results = response.get("results", [])

    normalized = []

    for r in results:
        normalized.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "published_at": r.get("published_date"),
                "source": r.get("source"),
            }
        )

    return normalized


RESEARCH_SYSTEM = f"""You are a research synthesizer for technical writing. 
Your task is to generate an output that STRICTLY matches the required Pydantic schema.

IMPORTANT:
- Every required field MUST be present
- Never omit fields
- Use the exact field names from the schema
- Preserve correct datatypes
- Nested objects must be complete
- Lists must contain properly formed objects
- Do not invent fields not present in schema
- Do not return partial objects
- Do not stop generation midway
- Ensure all objects are fully completed

FIELD COMPLETENESS RULE:
If a field exists in the schema, it MUST appear in the output.

OUTPUT RULES:
- Return ONLY the structured output
- No explanations
- No markdown
- No commentary
- No prose outside the schema

The output will be parsed directly into a Pydantic model.
Invalid or incomplete fields will cause failure.


You are generating an EvidencePack object.


Requirements:
- evidence: list of EvidenceItem objects

Rules:
- Never include empty objects.
- Every evidence item must contain title and url.
- Remove duplicates.
- Exclude malformed entries.

Never output empty dictionaries.
Every evidence item MUST contain:
- title
- url

If either is missing, omit the item entirely.

Given raw web search results, produce a deduplicated list of EvidenceItem objects.

Rules:
- Only include items with a non-empty url.
- Prefer relevant + authoritative sources (company blogs, docs, reputable outlets).
- If a published date is explicitly present in the result payload, keep it as YYYY-MM-DD.
  If missing or unclear, set published_at=null. Do NOT guess.
- Keep snippets short.
- Deduplicate by URL.
"""

def research_node(state: State) -> dict:

    # take the first 10 queries from state
    queries = (state.get("queries", []) or [])
    max_results = 6

    raw_results: List[dict] = []

    for q in queries:
        raw_results.extend(_tavily_search(q, max_results=max_results))

    if not raw_results:
        return {"evidence": []}
    
    evidence_structured_model = model.with_structured_output(EvidencePack)

    pack = evidence_structured_model.invoke(
        [
            SystemMessage(content=RESEARCH_SYSTEM),
            HumanMessage(content=f"Raw results:\n{raw_results}"),
        ]
    )

   
    # Deduplicate by URL
    dedup = {}
    for e in pack.evidence:
        if e.url:
            dedup[e.url] = e

    return {"evidence": list(dedup.values())}



ORCH_SYSTEM = f"""You are a senior technical writer and developer advocate.
Your job is to produce a highly actionable outline for a technical blog post.
Your task is to generate an output that STRICTLY matches the required Pydantic schema.

IMPORTANT:
- Every required field MUST be present
- Never omit fields
- Use the exact field names from the schema
- Preserve correct datatypes
- Nested objects must be complete
- Lists must contain properly formed objects
- Do not invent fields not present in schema
- Do not return partial objects
- Do not stop generation midway
- Ensure all objects are fully completed

FIELD COMPLETENESS RULE:
If a field exists in the schema, it MUST appear in the output.

OUTPUT RULES:
- Return ONLY the structured output
- No explanations
- No markdown
- No commentary
- No prose outside the schema

The output will be parsed directly into a Pydantic model.
Invalid or incomplete fields will cause failure.


You are generating a Plan object.


Requirements:
- blog_title: string
- audience: string
- tone: string
- blog_kind: one of
  ["explainer","tutorial","news_roundup","comparison","system_design"]
- constraints: list of strings
- tasks: list of Task objects

Rules:
- Create 5-9 tasks.
- Every task must contain:
  id
  title
  goal
  bullets
  target_words
  tags
  requires_research
  requires_citation
  requires_code

- Never return partial task objects.
- Never omit fields.
Hard requirements:
- Create 5–9 sections (tasks) suitable for the topic and audience.
- Each task must include:
  1) goal (1 sentence)
  2) 3–6 bullets that are concrete, specific, and non-overlapping
  3) target word count (120–550)

Quality bar:
- Assume the reader is a developer; use correct terminology.
- Bullets must be actionable: build/compare/measure/verify/debug.
- Ensure the overall plan includes at least 2 of these somewhere:
  * minimal code sketch / MWE (set requires_code=True for that section)
  * edge cases / failure modes
  * performance/cost considerations
  * security/privacy considerations (if relevant)
  * debugging/observability tips

Grounding rules:
- Mode closed_book: keep it evergreen; do not depend on evidence.
- Mode hybrid:
  - Use evidence for up-to-date examples (models/tools/releases) in bullets.
  - Mark sections using fresh info as requires_research=True and requires_citation=True.
- Mode open_book:
  - Set blog_kind = "news_roundup".
  - Every section is about summarizing events + implications.
  - DO NOT include tutorial/how-to sections unless user explicitly asked for that.
  - If evidence is empty or insufficient, create a plan that transparently says "insufficient sources"
    and includes only what can be supported.

Output must strictly match the Plan schema.
"""

def orchestrator_node(state: State) -> dict:

    evidence = state.get("evidence", [])
    mode = state.get("mode", "closed_book")

    plan_structured_model = model.with_structured_output(Plan)

    plan = plan_structured_model.invoke(
        [
            SystemMessage(content=ORCH_SYSTEM),
            HumanMessage(
                content=(
                    f"Topic: {state['topic']}\n"
                    f"Mode: {mode}\n\n"
                    f"Evidence (ONLY use for fresh claims; may be empty):\n"
                    f"{[e.model_dump() for e in evidence][:16]}"
                )
            ),
        ]
    )


    return {"plan": plan}

# -----------------------------
# 6) Fanout
# -----------------------------
def fanout(state: State):
    return [
        Send(
            "worker",
            {
                "task": task.model_dump(),
                "topic": state["topic"],
                "mode": state["mode"],
                "plan": state["plan"].model_dump(),
                "evidence": [e.model_dump() for e in state.get("evidence", [])],
            },
        )
        for task in state["plan"].tasks
    ]



# -----------------------------
# 7) Worker (write one section)
# -----------------------------
WORKER_SYSTEM = """You are a senior technical writer and developer advocate.
Write ONE section of a technical blog post in Markdown.

Hard constraints:
- Follow the provided Goal and cover ALL Bullets in order (do not skip or merge bullets).
- Stay close to Target words (±15%).
- Output ONLY the section content in Markdown (no blog title H1, no extra commentary).
- Start with a '## <Section Title>' heading.

Scope guard:
- If blog_kind == "news_roundup": do NOT turn this into a tutorial/how-to guide.
  Do NOT teach web scraping, RSS, automation, or "how to fetch news" unless bullets explicitly ask for it.
  Focus on summarizing events and implications.

Grounding policy:
- If mode == open_book:
  - Do NOT introduce any specific event/company/model/funding/policy claim unless it is supported by provided Evidence URLs.
  - For each event claim, attach a source as a Markdown link: ([Source](URL)).
  - Only use URLs provided in Evidence. If not supported, write: "Not found in provided sources."
- If requires_citation == true:
  - For outside-world claims, cite Evidence URLs the same way.
- Evergreen reasoning is OK without citations unless requires_citation is true.

Code:
- If requires_code == true, include at least one minimal, correct code snippet relevant to the bullets.

Style:
- Short paragraphs, bullets where helpful, code fences for code.
- Avoid fluff/marketing. Be precise and implementation-oriented.

Your task is to generate an output that STRICTLY matches the required Pydantic schema.

IMPORTANT:
- Every required field MUST be present
- Never omit fields
- Use the exact field names from the schema
- Preserve correct datatypes
- Nested objects must be complete
- Lists must contain properly formed objects
- Do not invent fields not present in schema
- Do not return partial objects
- Do not stop generation midway
- Ensure all objects are fully completed

FIELD COMPLETENESS RULE:
If a field exists in the schema, it MUST appear in the output.

OUTPUT RULES:
- Return ONLY the structured output
- No explanations
- No markdown
- No commentary
- No prose outside the schema

The output will be parsed directly into a Pydantic model.
Invalid or incomplete fields will cause failure.
"""

def worker_node(payload: dict) -> dict:
    
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]
    topic = payload["topic"]
    mode = payload.get("mode", "closed_book")

    bullets_text = "\n- " + "\n- ".join(task.bullets)

    evidence_text = ""
    if evidence:
        evidence_text = "\n".join(
            f"- {e.title} | {e.url} | {e.published_at or 'date:unknown'}".strip()
            for e in evidence[:20]
        )

    section_md = model.invoke(
        [
            SystemMessage(content=WORKER_SYSTEM),
            HumanMessage(
                content=(
                    f"Blog title: {plan.blog_title}\n"
                    f"Audience: {plan.audience}\n"
                    f"Tone: {plan.tone}\n"
                    f"Blog kind: {plan.blog_kind}\n"
                    f"Constraints: {plan.constraints}\n"
                    f"Topic: {topic}\n"
                    f"Mode: {mode}\n\n"
                    f"Section title: {task.title}\n"
                    f"Goal: {task.goal}\n"
                    f"Target words: {task.target_words}\n"
                    f"Tags: {task.tags}\n"
                    f"requires_research: {task.requires_research}\n"
                    f"requires_citation: {task.requires_citation}\n"
                    f"requires_code: {task.requires_code}\n"
                    f"Bullets:{bullets_text}\n\n"
                    f"Evidence (ONLY use these URLs when citing):\n{evidence_text}\n"
                )
            ),
        ]
    ).content.strip()

    return {"sections": [(task.id, section_md)]}

# ============================================================
# 8) ReducerWithImages (subgraph)
#    merge_content -> decide_images -> generate_and_place_images
# ============================================================
def merge_content(state: State) -> dict:

    plan = state["plan"]

    ordered_sections = [md for _, md in sorted(state["sections"], key=lambda x: x[0])]
    body = "\n\n".join(ordered_sections).strip()
    merged_md = f"# {plan.blog_title}\n\n{body}\n"
    
    return {"merged_md": merged_md}

def final_content_generation(state: State) -> dict:

    FINAL_PROMPT = f"""

        You are an expert technical editor specializing in Markdown formatting and document structure.

        Your task is to beautify and improve the readability of the following Markdown content while preserving all information exactly.

        Requirements:

        1. Preserve all content, meaning, and technical accuracy.
        2. DO NOT modify, simplify, reword, calculate, or interpret:

        * Mathematical formulas
        * Equations
        * LaTeX expressions
        * Code blocks
        * Tables
        * URLs
        * Citations
        * JSON
        * YAML
        * Configuration snippets
        * File paths
        3. Maintain every formula and code snippet character-for-character.
        4. Detect headings and convert them into proper Markdown heading levels (`#`, `##`, `###`, etc.) when appropriate.
        5. Improve spacing, line breaks, and section organization.
        6. Convert obvious lists into proper Markdown bullet or numbered lists.
        7. Ensure consistent Markdown formatting throughout the document.
        8. Remove unnecessary duplicate blank lines while preserving intentional paragraph separation.
        9. Keep the original section order unchanged.
        10. Do not add new information, explanations, summaries, conclusions, or commentary.
        11. If a line appears to be a title followed by content, format it as a Markdown heading.
        12. Preserve all existing Markdown that is already correctly formatted.
        13. Output only the beautified Markdown document and nothing else.

    """

    final_md = model.invoke(
        [
            SystemMessage(content=FINAL_PROMPT),
            HumanMessage(content=(f"Markdown File: {state["merged_md"]}\n"))
        ]
    ).content.strip()

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    title = state["plan"].blog_title.lower()

    slug = re.sub(r'[^a-z0-9\s-]', '', title)
    slug = re.sub(r'\s+', '-', slug).strip('-')

    filename = output_dir / f"{slug}.md"

    filename.write_text(final_md, encoding="utf-8")
    
    return {"final": final_md}



# build reducer subgraph
reducer_graph = StateGraph(State)
reducer_graph.add_node("merge_content", merge_content)
reducer_graph.add_node("final_content_gen", final_content_generation)
reducer_graph.add_edge(START, "merge_content")
reducer_graph.add_edge("merge_content", "final_content_gen")
reducer_graph.add_edge("final_content_gen", END)
reducer_subgraph = reducer_graph.compile()


# -----------------------------
# 9) Build main graph
# -----------------------------
g = StateGraph(State)
g.add_node("router", router_node)
g.add_node("research", research_node)
g.add_node("orchestrator", orchestrator_node)
g.add_node("worker", worker_node)
g.add_node("reducer", reducer_subgraph)

g.add_edge(START, "router")
g.add_conditional_edges("router", route_next, {"research": "research", "orchestrator": "orchestrator"})
g.add_edge("research", "orchestrator")

g.add_conditional_edges("orchestrator", fanout, ["worker"])
g.add_edge("worker", "reducer")
g.add_edge("reducer", END)

app = g.compile()


# -----------------------------
# 10) Runner
# -----------------------------
def run(topic: str, as_of: Optional[str] = None):
    if as_of is None:
        as_of = date.today().isoformat()

    out = app.invoke(
        {
            "topic": topic,
            "mode": "",
            "needs_research": False,
            "queries": [],
            "evidence": [],
            "plan": None,
            "as_of": as_of,
            "recency_days": 7,
            "sections": [],
            "merged_md": "",
            "md_with_placeholders": "",
            "image_specs": [],
            "final": "",
        }
    )

    return out