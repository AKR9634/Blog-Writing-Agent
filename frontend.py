"""
AI Blog Studio — Streamlit frontend for the AI Blog Writing Agent.

Designed with a thin API layer so a future WebSocket/SSE backend can replace
the mock generator without changing the UI code.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable, Generator, Iterator

import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AI Blog Studio",
    page_icon="✍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BLOG_TYPES = [
    "Explainer",
    "Tutorial",
    "Technical Deep Dive",
    "Beginner Guide",
    "Case Study",
    "Comparison",
]

AUDIENCES = ["Beginner", "Intermediate", "Advanced", "Developers", "Researchers"]

TONES = ["Professional", "Conversational", "Academic"]

AGENT_STEPS = ["Router", "Research", "Planning", "Writing", "Reducer", "Images", "Complete"]

STATUS_WAITING = "waiting"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"

STATUS_COLORS = {
    STATUS_WAITING: "#9CA3AF",
    STATUS_RUNNING: "#3B82F6",
    STATUS_COMPLETED: "#10B981",
    STATUS_SKIPPED: "#D1D5DB",
}

STATUS_LABELS = {
    STATUS_WAITING: "Waiting",
    STATUS_RUNNING: "Running",
    STATUS_COMPLETED: "Completed",
    STATUS_SKIPPED: "Skipped",
}

# Minimal CSS for status cards and log panel
st.markdown(
    """
    <style>
    .agent-card {
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 8px;
        background: #FAFAFA;
    }
    .agent-card-title { font-weight: 600; font-size: 0.9rem; color: #111827; }
    .agent-card-status { font-size: 0.8rem; margin-top: 2px; }
    .log-panel {
        background: #1F2937;
        color: #E5E7EB;
        font-family: monospace;
        font-size: 0.78rem;
        padding: 12px;
        border-radius: 8px;
        max-height: 320px;
        overflow-y: auto;
        line-height: 1.5;
    }
    .section-stream {
        border-left: 3px solid #3B82F6;
        padding-left: 12px;
        margin: 8px 0;
        font-size: 0.85rem;
        color: #4B5563;
    }
    div[data-testid="stSidebar"] { background-color: #F9FAFB; }
  </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
def init_session_state() -> None:
    defaults = {
        "generating": False,
        "agent_status": {step: STATUS_WAITING for step in AGENT_STEPS},
        "logs": [],
        "progress": 0.0,
        "markdown": "",
        "outline": [],
        "sources": [],
        "metadata": {},
        "images": [],
        "streamed_sections": [],
        "generation_complete": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------
# Fixed generation settings (not exposed in the UI)
INCLUDE_CODE = True
INCLUDE_IMAGES = False
GENERATE_SEO = False


def get_blog_request() -> dict[str, Any]:
    """Collect sidebar inputs into a request payload for the backend."""
    return {
        "topic": st.session_state.get("input_topic", ""),
        "blog_type": st.session_state.get("input_blog_type", BLOG_TYPES[0]),
        "audience": st.session_state.get("input_audience", AUDIENCES[0]),
        "tone": st.session_state.get("input_tone", TONES[0]),
        "target_length": st.session_state.get("input_length", 2000),
        "include_code": INCLUDE_CODE,
        "include_images": INCLUDE_IMAGES,
        "generate_seo": GENERATE_SEO,
    }


# ---------------------------------------------------------------------------
# Status & logging helpers
# ---------------------------------------------------------------------------
def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def update_agent_status(step: str, status: str) -> None:
    """Update a workflow step status in session state."""
    st.session_state.agent_status[step] = status


def append_log(message: str) -> None:
    """Append a timestamped log line."""
    st.session_state.logs.append(f"[{_timestamp()}] {message}")


def reset_generation_state() -> None:
    """Clear prior run artifacts before a new generation."""
    st.session_state.agent_status = {step: STATUS_WAITING for step in AGENT_STEPS}
    st.session_state.logs = []
    st.session_state.progress = 0.0
    st.session_state.markdown = ""
    st.session_state.outline = []
    st.session_state.sources = []
    st.session_state.metadata = {}
    st.session_state.images = []
    st.session_state.streamed_sections = []
    st.session_state.generation_complete = False


# ---------------------------------------------------------------------------
# Mock data (used when backend is unavailable)
# ---------------------------------------------------------------------------
def _mock_plan(request: dict[str, Any]) -> dict[str, Any]:
    topic = request["topic"] or "Self Attention in Transformer Architecture"
    return {
        "blog_title": f"Understanding {topic}",
        "audience": request["audience"],
        "tone": request["tone"],
        "blog_kind": request["blog_type"].lower().replace(" ", "_"),
        "tasks": [
            {"id": 1, "title": "Introduction", "target_words": 250},
            {"id": 2, "title": "Core Concepts", "target_words": 400},
            {"id": 3, "title": "How It Works", "target_words": 450},
            {"id": 4, "title": "Practical Examples", "target_words": 380},
            {"id": 5, "title": "Common Pitfalls", "target_words": 300},
            {"id": 6, "title": "Conclusion", "target_words": 200},
        ],
    }


def _mock_sources() -> list[dict[str, str]]:
    return [
        {
            "title": "Attention Is All You Need",
            "url": "https://arxiv.org/abs/1706.03762",
            "snippet": "The original Transformer paper introducing self-attention.",
            "published_at": "2017-06-12",
        },
        {
            "title": "The Illustrated Transformer",
            "url": "https://jalammar.github.io/illustrated-transformer/",
            "snippet": "Visual walkthrough of encoder-decoder attention mechanisms.",
            "published_at": None,
        },
    ]


def _mock_section(task: dict[str, Any], request: dict[str, Any]) -> str:
    title = task["title"]
    code_block = ""
    if request.get("include_code") and task["id"] == 4:
        code_block = """
```python
import torch
import torch.nn.functional as F

scores = torch.matmul(Q, K.transpose(-2, -1)) / (Q.size(-1) ** 0.5)
weights = F.softmax(scores, dim=-1)
output = torch.matmul(weights, V)
```
"""
    return f"""## {title}

This section explores **{title.lower()}** in the context of {request['topic']}.

Self-attention allows each token to weigh its relationship with every other token in the sequence. For a {request['audience'].lower()} audience, the key takeaway is understanding how query, key, and value projections interact.

- Define the problem space clearly
- Connect theory to practical implementation
- Highlight trade-offs relevant to production systems
{code_block}
> Written in a {request['tone'].lower()} tone for approximately {task['target_words']} words.
"""


def _mock_final_markdown(plan: dict[str, Any], sections: list[str], request: dict[str, Any]) -> str:
    body = "\n\n".join(sections)
    seo_block = ""
    if request.get("generate_seo"):
        seo_block = (
            "\n\n---\n\n"
            "**SEO Meta Description:** A comprehensive guide to "
            f"{request['topic']} for {request['audience'].lower()} readers.\n"
        )
    return f"# {plan['blog_title']}\n\n{body}{seo_block}"


# ---------------------------------------------------------------------------
# Event types for streaming (future SSE/WebSocket compatible)
# ---------------------------------------------------------------------------
# Each event: {"type": str, "data": dict}
EVENT_STATUS = "status"
EVENT_LOG = "log"
EVENT_PROGRESS = "progress"
EVENT_OUTLINE = "outline"
EVENT_SECTION = "section"
EVENT_SOURCES = "sources"
EVENT_MARKDOWN = "markdown"
EVENT_IMAGES = "images"
EVENT_METADATA = "metadata"
EVENT_COMPLETE = "complete"
EVENT_ERROR = "error"


def mock_stream_generation(request: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Simulates backend progress events.
    Replace `stream_generation` with a real SSE/WebSocket client later.
    """
    topic = request.get("topic") or "Self Attention in Transformer Architecture"
    include_images = request.get("include_images", True)
    needs_research = request.get("blog_type") in ("Case Study", "Comparison")

    yield {"type": EVENT_PROGRESS, "data": {"value": 0.05}}
    yield {"type": EVENT_STATUS, "data": {"step": "Router", "status": STATUS_RUNNING}}
    yield {"type": EVENT_LOG, "data": {"message": f"Analyzing topic: {topic}"}}
    time.sleep(0.4)

    mode = "hybrid" if needs_research else "closed_book"
    yield {
        "type": EVENT_LOG,
        "data": {"message": f"Router selected {mode.upper()}"},
    }
    yield {"type": EVENT_STATUS, "data": {"step": "Router", "status": STATUS_COMPLETED}}
    yield {"type": EVENT_PROGRESS, "data": {"value": 0.12}}

    if needs_research:
        yield {"type": EVENT_STATUS, "data": {"step": "Research", "status": STATUS_RUNNING}}
        yield {"type": EVENT_LOG, "data": {"message": "Running Tavily search (3 queries)..."}}
        time.sleep(0.5)
        sources = _mock_sources()
        yield {"type": EVENT_SOURCES, "data": {"sources": sources}}
        yield {"type": EVENT_LOG, "data": {"message": f"Collected {len(sources)} evidence items"}}
        yield {"type": EVENT_STATUS, "data": {"step": "Research", "status": STATUS_COMPLETED}}
    else:
        yield {"type": EVENT_STATUS, "data": {"step": "Research", "status": STATUS_SKIPPED}}
        yield {"type": EVENT_LOG, "data": {"message": "Research skipped (closed-book topic)"}}

    yield {"type": EVENT_PROGRESS, "data": {"value": 0.22}}

    yield {"type": EVENT_STATUS, "data": {"step": "Planning", "status": STATUS_RUNNING}}
    yield {"type": EVENT_LOG, "data": {"message": "Orchestrator building blog plan..."}}
    time.sleep(0.4)

    plan = _mock_plan(request)
    outline = [t["title"] for t in plan["tasks"]]
    yield {"type": EVENT_OUTLINE, "data": {"outline": outline, "plan": plan}}
    yield {"type": EVENT_LOG, "data": {"message": f"Generated {len(plan['tasks'])} tasks"}}
    yield {"type": EVENT_STATUS, "data": {"step": "Planning", "status": STATUS_COMPLETED}}
    yield {"type": EVENT_PROGRESS, "data": {"value": 0.35}}

    yield {"type": EVENT_STATUS, "data": {"step": "Writing", "status": STATUS_RUNNING}}
    sections: list[str] = []
    total = len(plan["tasks"])
    for i, task in enumerate(plan["tasks"], start=1):
        time.sleep(0.35)
        section_md = _mock_section(task, request)
        sections.append(section_md)
        yield {
            "type": EVENT_SECTION,
            "data": {"task_id": task["id"], "title": task["title"], "markdown": section_md},
        }
        yield {"type": EVENT_LOG, "data": {"message": f"Worker {i} completed — {task['title']}"}}
        progress = 0.35 + (0.4 * i / total)
        yield {"type": EVENT_PROGRESS, "data": {"value": progress}}

    yield {"type": EVENT_STATUS, "data": {"step": "Writing", "status": STATUS_COMPLETED}}

    yield {"type": EVENT_STATUS, "data": {"step": "Reducer", "status": STATUS_RUNNING}}
    yield {"type": EVENT_LOG, "data": {"message": "Reducer started — merging sections"}}
    time.sleep(0.3)
    yield {"type": EVENT_LOG, "data": {"message": "Sections merged and formatted"}}
    yield {"type": EVENT_STATUS, "data": {"step": "Reducer", "status": STATUS_COMPLETED}}
    yield {"type": EVENT_PROGRESS, "data": {"value": 0.85}}

    images: list[dict[str, str]] = []
    if include_images:
        yield {"type": EVENT_STATUS, "data": {"step": "Images", "status": STATUS_RUNNING}}
        yield {"type": EVENT_LOG, "data": {"message": "Deciding image placements..."}}
        time.sleep(0.3)
        yield {"type": EVENT_LOG, "data": {"message": "Generated 2 diagram placeholders"}}
        images = [
            {
                "alt": "Self-attention flow diagram",
                "caption": "Q/K/V projections and attention weights",
                "url": "https://placehold.co/600x400/3B82F6/FFFFFF?text=Attention+Flow",
            },
            {
                "alt": "Multi-head attention",
                "caption": "Parallel attention heads concatenated",
                "url": "https://placehold.co/600x400/10B981/FFFFFF?text=Multi-Head",
            },
        ]
        yield {"type": EVENT_IMAGES, "data": {"images": images}}
        yield {"type": EVENT_STATUS, "data": {"step": "Images", "status": STATUS_COMPLETED}}
    else:
        yield {"type": EVENT_STATUS, "data": {"step": "Images", "status": STATUS_SKIPPED}}
        yield {"type": EVENT_LOG, "data": {"message": "Image generation disabled"}}

    final_md = _mock_final_markdown(plan, sections, request)
    metadata = {
        "topic": topic,
        "mode": mode,
        "blog_title": plan["blog_title"],
        "blog_type": request["blog_type"],
        "audience": request["audience"],
        "tone": request["tone"],
        "target_length": request["target_length"],
        "section_count": len(plan["tasks"]),
        "word_count": len(final_md.split()),
        "generated_at": datetime.now().isoformat(),
    }

    yield {"type": EVENT_MARKDOWN, "data": {"markdown": final_md}}
    yield {"type": EVENT_METADATA, "data": {"metadata": metadata}}
    yield {"type": EVENT_PROGRESS, "data": {"value": 1.0}}
    yield {"type": EVENT_STATUS, "data": {"step": "Complete", "status": STATUS_COMPLETED}}
    yield {"type": EVENT_LOG, "data": {"message": "Blog generation complete"}}
    yield {"type": EVENT_COMPLETE, "data": {}}


def backend_stream_generation(request: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """
    Real backend integration hook.
    Import backend.run() and emit progress events.
    Falls back to mock if import or execution fails.
    """
    try:
        from backend import app as graph_app  # noqa: WPS433

        topic = request.get("topic", "")
        yield {"type": EVENT_PROGRESS, "data": {"value": 0.05}}
        yield {"type": EVENT_STATUS, "data": {"step": "Router", "status": STATUS_RUNNING}}
        yield {"type": EVENT_LOG, "data": {"message": f"Running LangGraph pipeline for: {topic}"}}

        # LangGraph invoke is blocking; a future API would stream node events
        result = graph_app.invoke(
            {
                "topic": topic,
                "mode": "",
                "needs_research": False,
                "queries": [],
                "evidence": [],
                "plan": None,
                "sections": [],
                "merged_md": "",
                "final": "",
            }
        )

        mode = result.get("mode", "closed_book")
        yield {"type": EVENT_LOG, "data": {"message": f"Router selected {mode.upper()}"}}
        yield {"type": EVENT_STATUS, "data": {"step": "Router", "status": STATUS_COMPLETED}}

        if result.get("needs_research"):
            yield {"type": EVENT_STATUS, "data": {"step": "Research", "status": STATUS_COMPLETED}}
            sources = [
                {
                    "title": e.title if hasattr(e, "title") else e.get("title", ""),
                    "url": e.url if hasattr(e, "url") else e.get("url", ""),
                    "snippet": e.snippet if hasattr(e, "snippet") else e.get("snippet", ""),
                    "published_at": (
                        e.published_at if hasattr(e, "published_at") else e.get("published_at")
                    ),
                }
                for e in result.get("evidence", [])
            ]
            yield {"type": EVENT_SOURCES, "data": {"sources": sources}}
        else:
            yield {"type": EVENT_STATUS, "data": {"step": "Research", "status": STATUS_SKIPPED}}

        plan = result.get("plan")
        if plan:
            tasks = plan.tasks if hasattr(plan, "tasks") else plan.get("tasks", [])
            outline = [t.title if hasattr(t, "title") else t.get("title", "") for t in tasks]
            plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan
            yield {"type": EVENT_OUTLINE, "data": {"outline": outline, "plan": plan_dict}}
            yield {"type": EVENT_LOG, "data": {"message": f"Generated {len(tasks)} tasks"}}

        yield {"type": EVENT_STATUS, "data": {"step": "Planning", "status": STATUS_COMPLETED}}
        yield {"type": EVENT_STATUS, "data": {"step": "Writing", "status": STATUS_COMPLETED}}

        for i, (task_id, section_md) in enumerate(result.get("sections", []), start=1):
            yield {
                "type": EVENT_SECTION,
                "data": {"task_id": task_id, "title": f"Section {task_id}", "markdown": section_md},
            }
            yield {"type": EVENT_LOG, "data": {"message": f"Worker {i} completed"}}

        yield {"type": EVENT_STATUS, "data": {"step": "Reducer", "status": STATUS_COMPLETED}}
        yield {"type": EVENT_STATUS, "data": {"step": "Images", "status": STATUS_SKIPPED}}

        final_md = result.get("final", "")
        blog_title = plan.blog_title if plan and hasattr(plan, "blog_title") else "blog"
        metadata = {
            "topic": topic,
            "mode": mode,
            "blog_title": blog_title,
            "word_count": len(final_md.split()),
            "generated_at": datetime.now().isoformat(),
        }

        yield {"type": EVENT_MARKDOWN, "data": {"markdown": final_md}}
        yield {"type": EVENT_METADATA, "data": {"metadata": metadata}}
        yield {"type": EVENT_PROGRESS, "data": {"value": 1.0}}
        yield {"type": EVENT_STATUS, "data": {"step": "Complete", "status": STATUS_COMPLETED}}
        yield {"type": EVENT_LOG, "data": {"message": "Blog generation complete"}}
        yield {"type": EVENT_COMPLETE, "data": {}}

    except Exception as exc:
        yield {"type": EVENT_ERROR, "data": {"message": str(exc)}}
        yield from mock_stream_generation(request)


def get_stream_generator(request: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Always use the real LangGraph backend (falls back to mock on error)."""
    return backend_stream_generation(request)


def apply_event(event: dict[str, Any]) -> None:
    """Apply a single streaming event to session state."""
    etype = event["type"]
    data = event.get("data", {})

    if etype == EVENT_STATUS:
        update_agent_status(data["step"], data["status"])
    elif etype == EVENT_LOG:
        append_log(data["message"])
    elif etype == EVENT_PROGRESS:
        st.session_state.progress = data["value"]
    elif etype == EVENT_OUTLINE:
        st.session_state.outline = data.get("outline", [])
        st.session_state.metadata["plan"] = data.get("plan", {})
    elif etype == EVENT_SECTION:
        st.session_state.streamed_sections.append(data)
    elif etype == EVENT_SOURCES:
        st.session_state.sources = data.get("sources", [])
    elif etype == EVENT_MARKDOWN:
        st.session_state.markdown = data.get("markdown", "")
    elif etype == EVENT_IMAGES:
        st.session_state.images = data.get("images", [])
    elif etype == EVENT_METADATA:
        st.session_state.metadata.update(data.get("metadata", {}))
    elif etype == EVENT_COMPLETE:
        st.session_state.generation_complete = True
        st.session_state.generating = False
    elif etype == EVENT_ERROR:
        append_log(f"Error: {data.get('message', 'Unknown error')}")
        st.session_state.generating = False


def start_generation(
    request: dict[str, Any],
    progress_bar,
    status_containers: dict[str, Any],
    log_container,
    section_container,
) -> None:
    """
    Drive the generation loop, updating UI placeholders as events arrive.
    """
    reset_generation_state()
    st.session_state.generating = True

    for event in get_stream_generator(request):
        apply_event(event)

        progress_bar.progress(min(st.session_state.progress, 1.0))
        render_agent_status_cards(status_containers)
        render_logs(log_container)
        render_streamed_sections(section_container)

        if event["type"] == EVENT_ERROR:
            break

    st.session_state.generating = False


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------
def render_agent_status_cards(containers: dict[str, Any]) -> None:
    """Update status card placeholders."""
    for step in AGENT_STEPS:
        status = st.session_state.agent_status.get(step, STATUS_WAITING)
        color = STATUS_COLORS.get(status, STATUS_COLORS[STATUS_WAITING])
        label = STATUS_LABELS.get(status, status.capitalize())
        containers[step].markdown(
            f"""
            <div class="agent-card">
              <div class="agent-card-title">{step}</div>
              <div class="agent-card-status" style="color:{color};">● {label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_logs(container) -> None:
    """Render timestamped generation logs."""
    lines = st.session_state.logs[-50:]
    log_html = "<br>".join(lines) if lines else "<span style='color:#6B7280;'>Waiting to start...</span>"
    container.markdown(f'<div class="log-panel">{log_html}</div>', unsafe_allow_html=True)


def render_streamed_sections(container) -> None:
    """Show sections as they stream in during writing."""
    sections = st.session_state.streamed_sections
    if not sections:
        container.empty()
        return
    html_parts = [
        f'<div class="section-stream">✓ <strong>{s["title"]}</strong> — section ready</div>'
        for s in sections
    ]
    container.markdown("".join(html_parts), unsafe_allow_html=True)


def render_preview() -> None:
    """Render markdown preview."""
    md = st.session_state.markdown
    if not md:
        if st.session_state.generating:
            st.info("Generating your blog post… sections will appear here when complete.")
        else:
            st.markdown(
                """
                <div style="text-align:center; padding: 60px 20px; color:#9CA3AF;">
                  <p style="font-size: 2rem; margin-bottom: 8px;">✍️</p>
                  <p style="font-size: 1.1rem; font-weight: 500; color:#6B7280;">
                    Your blog preview will appear here
                  </p>
                  <p style="font-size: 0.9rem;">Enter a topic and click <strong>Generate Blog</strong></p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        return
    st.markdown(md, unsafe_allow_html=False)


def render_markdown() -> None:
    """Show raw markdown in an editable text area."""
    st.text_area(
        "Raw Markdown",
        value=st.session_state.markdown,
        height=500,
        label_visibility="collapsed",
        disabled=st.session_state.generating,
    )


def render_outline() -> None:
    """Display generated section titles."""
    outline = st.session_state.outline
    plan = st.session_state.metadata.get("plan", {})

    if not outline:
        st.caption("Section outline will appear after planning completes.")
        return

    if plan:
        col1, col2, col3 = st.columns(3)
        col1.metric("Sections", len(outline))
        col2.metric("Audience", plan.get("audience", "—"))
        col3.metric("Tone", plan.get("tone", "—"))
        st.markdown(f"**{plan.get('blog_title', 'Untitled')}**")
        st.divider()

    for i, title in enumerate(outline, start=1):
        st.markdown(f"{i}. **{title}**")


def render_sources() -> None:
    """Display research sources when available."""
    sources = st.session_state.sources
    if not sources:
        st.caption("No research sources — topic was handled in closed-book mode.")
        return

    for src in sources:
        title = src.get("title", "Untitled")
        url = src.get("url", "")
        snippet = src.get("snippet", "")
        published = src.get("published_at")
        date_str = f" · {published}" if published else ""
        st.markdown(f"**[{title}]({url})**{date_str}")
        if snippet:
            st.caption(snippet)
        st.divider()


def render_downloads() -> None:
    """Download buttons and image gallery."""
    if not st.session_state.generation_complete:
        return

    st.divider()
    st.subheader("Downloads")

    md = st.session_state.markdown
    meta = st.session_state.metadata
    title = meta.get("blog_title", "blog").replace(" ", "_")[:60]

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="⬇️ Download Markdown",
            data=md,
            file_name=f"{title}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            label="⬇️ Download Metadata JSON",
            data=json.dumps(meta, indent=2, default=str),
            file_name=f"{title}_metadata.json",
            mime="application/json",
            use_container_width=True,
        )

    images = st.session_state.images
    if images:
        st.subheader("Generated Images")
        cols = st.columns(min(len(images), 3))
        for i, img in enumerate(images):
            with cols[i % len(cols)]:
                st.image(img.get("url", ""), caption=img.get("caption", img.get("alt", "")))


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar() -> bool:
    """Render inputs; return True if Generate was clicked."""
    with st.sidebar:
        st.title("AI Blog Studio")
        st.caption("Configure your blog and let the agents write.")

        st.subheader("Inputs")
        st.text_input("Blog Topic", placeholder="e.g. Self Attention in Transformer Architecture", key="input_topic")
        st.selectbox("Blog Type", BLOG_TYPES, key="input_blog_type")
        st.selectbox("Audience", AUDIENCES, key="input_audience")
        st.selectbox("Tone", TONES, key="input_tone")
        st.slider("Length (words)", min_value=1000, max_value=5000, value=2000, step=250, key="input_length")

        st.divider()
        generate = st.button(
            "Generate Blog",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.get("generating", False),
        )

        if generate:
            st.session_state["_trigger_generate"] = True

    return st.session_state.pop("_trigger_generate", False)


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
def main() -> None:
    init_session_state()

    # Header
    st.markdown("# AI Blog Studio")
    st.caption("Multi-agent blog generation with live workflow tracking")

    trigger = render_sidebar()

    # Three-column layout: main (wide) + right activity panel
    main_col, activity_col = st.columns([3, 1])

    with activity_col:
        st.markdown("### Agent Activity")
        status_containers = {step: st.empty() for step in AGENT_STEPS}
        render_agent_status_cards(status_containers)

        st.markdown("##### Generation Logs")
        log_container = st.empty()
        render_logs(log_container)

        st.markdown("##### Sections")
        section_container = st.empty()
        render_streamed_sections(section_container)

    with main_col:
        progress_placeholder = st.empty()
        progress_bar = progress_placeholder.progress(
            st.session_state.progress if st.session_state.generating else 0.0
        )

        tab_preview, tab_md, tab_outline, tab_sources = st.tabs(
            ["Preview", "Markdown", "Outline", "Sources"]
        )

        with tab_preview:
            render_preview()
        with tab_md:
            render_markdown()
        with tab_outline:
            render_outline()
        with tab_sources:
            render_sources()

        render_downloads()

    # Handle generation trigger
    if trigger:
        request = get_blog_request()
        if not request["topic"].strip():
            st.warning("Please enter a blog topic before generating.")
        else:
            with st.spinner("Agents are working…"):
                start_generation(
                    request,
                    progress_bar,
                    status_containers,
                    log_container,
                    section_container,
                )
            progress_bar.progress(1.0)
            render_agent_status_cards(status_containers)
            render_logs(log_container)
            st.rerun()


if __name__ == "__main__":
    main()
