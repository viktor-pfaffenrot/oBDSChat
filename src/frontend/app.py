"""Gradio chat and exact-field XSD evidence viewer."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Annotated, Final, Literal
from urllib.parse import quote, urlencode

import gradio as gr
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from frontend.api import (
    BackendApiError,
    BackendClient,
    ConversationTurn,
    SourceReference,
    XsdEvidence,
)

CSS_PATH: Final = Path(__file__).with_name("assets") / "styles.css"
STYLESHEET: Final = CSS_PATH.read_text(encoding="utf-8")
MAX_HISTORY_TURNS: Final = 10
MAX_HISTORY_CHARACTERS: Final = 50_000


class FrontendHealthResponse(BaseModel):
    """Frontend process liveness response."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


class CompletedTurn(BaseModel):
    """One answer and its citations retained in frontend session state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    answer: str
    sources: tuple[SourceReference, ...] = ()


class ConversationState(BaseModel):
    """Completed conversation plus an optional in-flight question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: tuple[CompletedTurn, ...] = ()
    pending_question: str | None = None


def get_backend_client() -> BackendClient:
    """Create the configured backend client for one frontend operation."""
    return BackendClient.from_environment()


def prepare_question(
    question: str,
    state: ConversationState | None,
) -> tuple[str, list[gr.ChatMessage], ConversationState]:
    """Show the user message immediately without revealing a partial answer."""
    normalized_question = question.strip()
    if not normalized_question:
        raise gr.Error("Bitte geben Sie eine Frage ein.")
    if len(normalized_question) > 10_000:
        raise gr.Error("Die Frage darf höchstens 10.000 Zeichen enthalten.")

    current_state = state or ConversationState()
    pending_state = current_state.model_copy(
        update={"pending_question": normalized_question}
    )
    return "", render_conversation(pending_state), pending_state


def complete_question(
    state: ConversationState,
) -> tuple[list[gr.ChatMessage], ConversationState]:
    """Fetch and display one complete answer, never answer fragments."""
    question = state.pending_question
    if question is None:
        return render_conversation(state), state

    try:
        response = get_backend_client().query(
            question,
            history=build_backend_history(state.turns),
        )
    except (BackendApiError, ValueError) as error:
        stable_state = state.model_copy(update={"pending_question": None})
        messages = render_conversation(stable_state)
        messages.append(
            gr.ChatMessage(
                role="assistant",
                content=_error_message(str(error)),
            )
        )
        return messages, stable_state

    completed_state = ConversationState(
        turns=(
            *state.turns,
            CompletedTurn(
                question=question,
                answer=response.answer,
                sources=response.sources,
            ),
        )
    )
    return render_conversation(completed_state), completed_state


def clear_conversation() -> tuple[str, list[gr.ChatMessage], ConversationState]:
    """Reset all frontend-owned conversation state."""
    return "", [], ConversationState()


def build_backend_history(
    turns: tuple[CompletedTurn, ...],
) -> tuple[ConversationTurn, ...]:
    """Keep newest complete turns within backend context limits."""
    selected_turns: list[ConversationTurn] = []
    character_count = 0
    for turn in reversed(turns[-MAX_HISTORY_TURNS:]):
        turn_size = len(turn.question) + len(turn.answer)
        if character_count + turn_size > MAX_HISTORY_CHARACTERS:
            break
        selected_turns.append(
            ConversationTurn(question=turn.question, answer=turn.answer)
        )
        character_count += turn_size
    selected_turns.reverse()
    return tuple(selected_turns)


def render_conversation(state: ConversationState) -> list[gr.ChatMessage]:
    """Render completed turns and one optional pending status message."""
    messages: list[gr.ChatMessage] = []
    for turn in state.turns:
        messages.extend(
            (
                gr.ChatMessage(role="user", content=turn.question),
                gr.ChatMessage(
                    role="assistant",
                    content=_assistant_message(turn.answer, turn.sources),
                ),
            )
        )
    if state.pending_question is not None:
        messages.extend(
            (
                gr.ChatMessage(role="user", content=state.pending_question),
                gr.ChatMessage(
                    role="assistant",
                    content=(
                        '<div class="answer-pending">'
                        '<span class="answer-pending__mark"></span>'
                        "Quellen werden geprüft"
                        "</div>"
                    ),
                ),
            )
        )
    return messages


def _assistant_message(
    answer: str,
    sources: tuple[SourceReference, ...],
) -> str:
    if not sources:
        return answer
    return f"{answer}\n\n{_source_ledger(sources)}"


def _source_ledger(sources: tuple[SourceReference, ...]) -> str:
    source_label = "Quelle" if len(sources) == 1 else "Quellen"
    cards = "".join(_source_card(source) for source in sources)
    return (
        '<details class="source-ledger">'
        '<summary><span class="source-ledger__eyebrow">Beleglage</span>'
        f'<span class="source-ledger__count">{len(sources)} {source_label}</span>'
        "</summary>"
        f'<div class="source-ledger__list">{cards}</div>'
        "</details>"
    )


def _source_card(source: SourceReference) -> str:
    title = escape(source.title)
    section = escape(source.section or source.path or "Originalquelle")
    version = escape(source.obds_version or "versionsübergreifend")
    source_type = escape(source.source_type.upper())
    official_url = escape(str(source.url), quote=True)

    if source.source_type == "xsd" and source.obds_version and source.path:
        viewer_url = _xsd_viewer_url(source.obds_version, source.path)
        primary_link = (
            f'<a href="{escape(viewer_url, quote=True)}" target="_blank" '
            'rel="noopener noreferrer">Feld anzeigen</a>'
        )
        secondary_link = (
            f'<a href="{official_url}" target="_blank" '
            'rel="noopener noreferrer">Offizielle XSD</a>'
        )
    else:
        primary_link = (
            f'<a href="{official_url}" target="_blank" '
            'rel="noopener noreferrer">Originalseite öffnen</a>'
        )
        secondary_link = ""

    return (
        '<article class="source-card">'
        '<div class="source-card__stamp">'
        f"<span>{source_type}</span><span>{version}</span>"
        "</div>"
        f'<strong class="source-card__title">{title}</strong>'
        f'<code class="source-card__section">{section}</code>'
        '<div class="source-card__links">'
        f"{primary_link}{secondary_link}"
        "</div>"
        "</article>"
    )


def _xsd_viewer_url(version: str, path: str) -> str:
    query = urlencode({"path": path})
    return f"/xsd-viewer/{quote(version, safe='')}?{query}"


def _error_message(message: str) -> str:
    return (
        '<div class="answer-error"><strong>Anfrage nicht abgeschlossen</strong>'
        f"<span>{escape(message)}</span></div>"
    )


def build_interface() -> gr.Blocks:
    """Build the production Gradio conversation interface."""
    with gr.Blocks(title="oBDS Schema Desk") as interface:
        state = gr.State(ConversationState())
        gr.HTML(
            """
            <header class="masthead">
              <a class="wordmark" href="/" aria-label="oBDS Schema Desk Startseite">
                <span class="wordmark__index">§65c</span>
                <span class="wordmark__name">oBDS<br>Schema Desk</span>
              </a>
              <div class="masthead__status">
                <span class="status-dot"></span>
                Quellengebundene Auskunft
              </div>
            </header>
            """
        )
        with gr.Row(elem_classes="desk-layout"):
            with gr.Column(scale=3, min_width=250, elem_classes="desk-rail"):
                gr.HTML(
                    """
                    <section class="rail-copy">
                      <p class="rail-copy__kicker">Technische Auskunft</p>
                      <h1>Fragen an den<br><em>onkologischen</em><br>Basisdatensatz.</h1>
                      <p class="rail-copy__intro">
                        Antworten werden gegen XSD und Umsetzungsleitfaden geprüft.
                        Jede verwendete Quelle bleibt direkt am Ergebnis sichtbar.
                      </p>
                    </section>
                    <div class="rail-notes" aria-label="Arbeitsweise">
                      <div><span>01</span><p>Frage präzise formulieren</p></div>
                      <div><span>02</span><p>Antwort und Version prüfen</p></div>
                      <div><span>03</span><p>Originalquelle öffnen</p></div>
                    </div>
                    """
                )
            with gr.Column(scale=8, min_width=320, elem_classes="conversation-stage"):
                chatbot = gr.Chatbot(
                    label="Gespräch",
                    show_label=False,
                    height="65vh",
                    min_height=460,
                    layout="panel",
                    buttons=["copy"],
                    feedback_options=None,
                    sanitize_html=True,
                    elem_id="conversation",
                    placeholder=(
                        '<div class="empty-desk">'
                        '<span class="empty-desk__rule"></span>'
                        "<strong>Aktenblatt ist leer.</strong>"
                        "<p>Stellen Sie eine Frage zu Feldern, Kardinalitäten, "
                        "Werten oder Umsetzungshinweisen.</p>"
                        "</div>"
                    ),
                )
                with gr.Row(elem_classes="composer"):
                    question = gr.Textbox(
                        label="Frage",
                        show_label=False,
                        placeholder="Zum Beispiel: Welche Werte darf Diagnosesicherung haben?",
                        lines=2,
                        max_lines=5,
                        max_length=10_000,
                        autofocus=False,
                        container=False,
                        scale=8,
                        elem_id="question-input",
                    )
                    submit = gr.Button(
                        "Prüfen",
                        variant="primary",
                        scale=1,
                        min_width=110,
                        elem_id="submit-question",
                    )
                with gr.Row(elem_classes="conversation-tools"):
                    gr.HTML(
                        "<p><span>HTTP</span> Frontend und Fachlogik strikt getrennt</p>"
                    )
                    clear = gr.Button(
                        "Gespräch leeren",
                        variant="secondary",
                        size="sm",
                        elem_id="clear-conversation",
                    )

        submit_event = submit.click(
            prepare_question,
            inputs=(question, state),
            outputs=(question, chatbot, state),
            queue=False,
        )
        submit_event.then(
            complete_question,
            inputs=state,
            outputs=(chatbot, state),
        )
        question_event = question.submit(
            prepare_question,
            inputs=(question, state),
            outputs=(question, chatbot, state),
            queue=False,
        )
        question_event.then(
            complete_question,
            inputs=state,
            outputs=(chatbot, state),
        )
        clear.click(
            clear_conversation,
            outputs=(question, chatbot, state),
            queue=False,
        )
    return interface


def _render_viewer(evidence: XsdEvidence) -> str:
    line_range = _line_range(evidence)
    source_markup = _source_markup(evidence)
    allowed_values = _allowed_values_markup(evidence)
    documentation = _documentation_markup(evidence)
    path_parts = [part for part in evidence.path.split("/") if part]
    breadcrumb = '<span aria-hidden="true">/</span>'.join(
        f"<span>{escape(part)}</span>" for part in path_parts
    )
    source_url = escape(str(evidence.source_url), quote=True)
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(evidence.name)} · oBDS {escape(evidence.version)}</title>
  <style>{STYLESHEET}</style>
</head>
<body class="viewer-body">
  <header class="viewer-masthead">
    <a class="wordmark" href="/">
      <span class="wordmark__index">§65c</span>
      <span class="wordmark__name">oBDS<br>Schema Desk</span>
    </a>
    <span class="viewer-version">XSD · Version {escape(evidence.version)}</span>
  </header>
  <main class="viewer-shell">
    <nav class="viewer-breadcrumb" aria-label="XML-Pfad">{breadcrumb}</nav>
    <section class="viewer-heading">
      <div>
        <p class="viewer-kicker">Exakter Feldnachweis</p>
        <h1>{escape(evidence.name)}</h1>
      </div>
      <div class="viewer-actions">
        <a class="button-link button-link--quiet" href="/">Zurück zum Chat</a>
        <a class="button-link" href="{source_url}" target="_blank"
           rel="noopener noreferrer">Offizielle XSD öffnen</a>
      </div>
    </section>
    <div class="viewer-grid">
      <section class="source-sheet" aria-labelledby="source-heading">
        <header class="source-sheet__header">
          <div>
            <span>Quelldatei</span>
            <h2 id="source-heading">{escape(evidence.xsd_file)}</h2>
          </div>
          <code>{line_range}</code>
        </header>
        {source_markup}
      </section>
      <aside class="fact-sheet" aria-label="Feldfakten">
        <p class="fact-sheet__index">FIELD / {escape(evidence.version)}</p>
        <dl>
          <div><dt>Datentyp</dt><dd>{escape(evidence.datatype)}</dd></div>
          <div><dt>Basistyp</dt><dd>{escape(evidence.base_datatype or "–")}</dd></div>
          <div><dt>Kardinalität</dt><dd>{evidence.min_occurs}…{evidence.max_occurs}</dd></div>
          <div><dt>XML-Pfad</dt><dd><code>{_path_markup(evidence.path)}</code></dd></div>
        </dl>
        {allowed_values}
        {documentation}
      </aside>
    </div>
  </main>
</body>
</html>"""


def _source_markup(evidence: XsdEvidence) -> str:
    if not evidence.source_lines:
        return (
            '<div class="source-unavailable"><strong>Quellenausschnitt nicht verfügbar.</strong>'
            "<p>Die strukturierten Feldfakten bleiben gültig. Nutzen Sie die "
            "offizielle XSD für den vollständigen Quelltext.</p></div>"
        )
    rendered_lines = "".join(
        '<span class="code-line{}"><span class="code-line__number">{}</span>'
        '<span class="code-line__content">{}</span></span>'.format(
            " code-line--target" if line.highlighted else "",
            line.number,
            escape(line.content),
        )
        for line in evidence.source_lines
    )
    truncation = ""
    if evidence.declaration_truncated:
        truncation = (
            '<p class="source-truncated">Ausschnitt begrenzt. '
            "Vollständige Deklaration steht in der offiziellen XSD.</p>"
        )
    return f'<pre class="source-code"><code>{rendered_lines}</code></pre>{truncation}'


def _allowed_values_markup(evidence: XsdEvidence) -> str:
    if not evidence.allowed_values:
        return ""
    values = "".join(
        f'<li title="{escape(item.documentation or "", quote=True)}">'
        f"{escape(item.value)}</li>"
        for item in evidence.allowed_values
    )
    return (
        '<section class="fact-section"><h2>Zulässige Werte</h2>'
        f'<ul class="value-list">{values}</ul></section>'
    )


def _documentation_markup(evidence: XsdEvidence) -> str:
    documentation = evidence.documentation or evidence.datatype_documentation
    if documentation is None:
        return ""
    return (
        '<section class="fact-section"><h2>Dokumentation</h2>'
        f"<p>{escape(documentation)}</p></section>"
    )


def _line_range(evidence: XsdEvidence) -> str:
    if evidence.declaration_start_line is None:
        return "Zeilen nicht verfügbar"
    if evidence.declaration_end_line == evidence.declaration_start_line:
        return f"Zeile {evidence.declaration_start_line}"
    return f"Zeilen {evidence.declaration_start_line}–{evidence.declaration_end_line}"


def _path_markup(path: str) -> str:
    parts = [escape(part) for part in path.split("/") if part]
    return "<wbr>/" + "<wbr>/".join(parts)


def _render_viewer_error(message: str) -> str:
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nachweis nicht verfügbar · oBDS Schema Desk</title>
  <style>{STYLESHEET}</style>
</head>
<body class="viewer-body">
  <main class="viewer-error">
    <p class="viewer-kicker">Quellennachweis</p>
    <h1>Feldansicht nicht verfügbar.</h1>
    <p>{escape(message)}</p>
    <a class="button-link" href="/">Zurück zum Chat</a>
  </main>
</body>
</html>"""


frontend_app = FastAPI(title="oBDSChat Frontend")


@frontend_app.get("/health", response_model=FrontendHealthResponse)
def health() -> FrontendHealthResponse:
    """Report frontend process liveness without contacting the backend."""
    return FrontendHealthResponse()


@frontend_app.get("/xsd-viewer/{version}", response_class=HTMLResponse)
def xsd_viewer(
    version: str,
    path: Annotated[str, Query(min_length=1, max_length=10_000)],
) -> HTMLResponse:
    """Render exact XSD evidence fetched from the backend over HTTP."""
    try:
        evidence = get_backend_client().get_xsd_evidence(version, path)
    except (BackendApiError, ValueError) as error:
        status_code = error.status_code if isinstance(error, BackendApiError) else 422
        return HTMLResponse(
            _render_viewer_error(str(error)),
            status_code=status_code or 502,
        )
    return HTMLResponse(_render_viewer(evidence))


interface = build_interface()
theme = gr.themes.Base(
    primary_hue="orange",
    neutral_hue="slate",
    font=("Avenir Next", "Avenir", "Century Gothic", "sans-serif"),
    font_mono=("IBM Plex Mono", "Cascadia Mono", "monospace"),
).set(
    body_background_fill="#eee9df",
    body_background_fill_dark="#eee9df",
    body_text_color="#17252d",
    body_text_color_dark="#17252d",
    background_fill_primary="#eee9df",
    background_fill_primary_dark="#eee9df",
    background_fill_secondary="#f8f4eb",
    background_fill_secondary_dark="#f8f4eb",
    block_background_fill="#f8f4eb",
    block_background_fill_dark="#f8f4eb",
    block_border_color="#c8c0b1",
    block_border_color_dark="#c8c0b1",
    input_background_fill="#fffdf7",
    input_background_fill_dark="#fffdf7",
    button_primary_background_fill="#df4a22",
    button_primary_background_fill_dark="#df4a22",
    button_primary_background_fill_hover="#bd3515",
    button_primary_background_fill_hover_dark="#bd3515",
    button_primary_text_color="#fffaf1",
    button_primary_text_color_dark="#fffaf1",
)
app = gr.mount_gradio_app(
    frontend_app,
    interface,
    path="/",
    footer_links=[],
    theme=theme,
    css_paths=CSS_PATH,
)
