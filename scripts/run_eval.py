"""Run deterministic, source-grounded evaluation against the production backend."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, Self
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import yaml
from matplotlib.figure import Figure
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from backend.config import LlmEndpoint, LlmProvider, load_settings
from backend.llm import (
    QuestionAnswer,
    ToolExecution,
    VersionContext,
    answer_question,
    create_client,
)
from backend.tools import CITATION_ID_FIELD, TOOLS
from backend.xsd import SchemaCatalog, get_schema_catalog

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS_PATH: Final = PROJECT_ROOT / "tests" / "questions.yaml"
DEFAULT_OUTPUT_DIRECTORY: Final = PROJECT_ROOT / "evaluation-results"
DEFAULT_REPORT_NAME: Final = (
    f"results_{datetime.now(ZoneInfo('Europe/Berlin')).strftime('%Y%m%d_%H%M%S')}.json"
)
DEFAULT_FIGURE_NAME: Final = (
    f"summary_{datetime.now(ZoneInfo('Europe/Berlin')).strftime('%Y%m%d_%H%M%S')}.png"
)

EvaluationCategory = Literal[
    "field_meaning",
    "allowed_values",
    "datatype",
    "cardinality",
    "xml_hierarchy",
    "implementation_guidance",
    "version",
    "mixed_source",
    "ambiguous_or_unanswerable",
]
SourceType = Literal["xsd", "umsetzungsleitfaden"]

CATEGORY_NAMES: Final[tuple[EvaluationCategory, ...]] = (
    "field_meaning",
    "allowed_values",
    "datatype",
    "cardinality",
    "xml_hierarchy",
    "implementation_guidance",
    "version",
    "mixed_source",
    "ambiguous_or_unanswerable",
)
CATEGORY_LABELS: Final[dict[EvaluationCategory, str]] = {
    "field_meaning": "Feldbedeutung",
    "allowed_values": "Zulässige Werte",
    "datatype": "Datentypen",
    "cardinality": "Kardinalität",
    "xml_hierarchy": "XML-Hierarchie",
    "implementation_guidance": "Implementierung",
    "version": "Versionen",
    "mixed_source": "Gemischte Quellen",
    "ambiguous_or_unanswerable": "Unklar / nicht beantwortbar",
}
_TOKEN_PATTERN: Final = re.compile(r"\w+(?:(?:[.:/])\w+)*")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactExpectation(_FrozenModel):
    """One benchmark fact and accepted answer terms."""

    label: str = Field(min_length=1)
    answer_any: tuple[str, ...] = Field(min_length=1)


class EvaluationCase(_FrozenModel):
    """Validated deterministic rubric for one German oBDS question."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    category: EvaluationCategory
    question: str = Field(min_length=1)
    obds_version: str | None = None
    expected_supported: bool = True
    facts: tuple[FactExpectation, ...] = Field(min_length=1)
    expected_source_types: frozenset[SourceType] = frozenset()

    @model_validator(mode="after")
    def validate_expectations(self) -> Self:
        """Reject rubrics that cannot produce meaningful deterministic scores."""
        if self.expected_supported and not self.expected_source_types:
            raise ValueError("Supported cases require an expected source type")
        if not self.expected_supported and self.expected_source_types:
            raise ValueError("Unsupported cases cannot expect cited sources")
        return self


class FactScore(_FrozenModel):
    """Observed answer match for one expected fact."""

    label: str
    answer_matched: bool


class MetricScores(_FrozenModel):
    """Answer and citation metrics normalized to zero through one."""

    answer_correctness: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)


class EvaluationResult(_FrozenModel):
    """Detailed result for one executed evaluation case."""

    case_id: str
    category: EvaluationCategory
    question: str
    answer: str | None
    citation_ids: tuple[str, ...]
    cited_source_types: tuple[str, ...]
    fact_scores: tuple[FactScore, ...]
    metrics: MetricScores
    duration_seconds: float = Field(ge=0)
    error: str | None = None


class CategorySummary(_FrozenModel):
    """Mean scores for one question category."""

    category: EvaluationCategory
    case_count: int = Field(gt=0)
    answer_correctness: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)


class EvaluationSummary(_FrozenModel):
    """Aggregate metrics for a completed evaluation run."""

    case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    answer_correctness: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)
    categories: tuple[CategorySummary, ...]


class EvaluationReport(_FrozenModel):
    """Serializable record of one production evaluation run."""

    generated_at: datetime
    provider: LlmProvider
    route: str
    questions_path: str
    summary: EvaluationSummary
    results: tuple[EvaluationResult, ...]


@dataclass(frozen=True, slots=True)
class ProductionAnswerer:
    """Call the same model and local tools used by the production query path."""

    endpoint: LlmEndpoint
    client: AsyncOpenAI
    catalog: SchemaCatalog

    async def __call__(self, case: EvaluationCase) -> QuestionAnswer:
        constraint = None
        if case.obds_version is not None:
            constraint = self.catalog.resolve_version(case.obds_version)
        version_context = VersionContext(
            default_version=self.catalog.latest_version,
            available_versions=self.catalog.versions,
            constraint=constraint,
        )
        return await answer_question(
            case.question,
            version_context=version_context,
            tools=TOOLS,
            client=self.client,
            endpoint=self.endpoint,
        )


Answerer = Callable[[EvaluationCase], Awaitable[QuestionAnswer]]


def load_evaluation_cases(
    path: Path = DEFAULT_QUESTIONS_PATH,
) -> tuple[EvaluationCase, ...]:
    """Load and validate evaluation cases from YAML."""
    with path.open(encoding="utf-8") as questions_file:
        raw_cases = yaml.safe_load(questions_file)
    cases = TypeAdapter(tuple[EvaluationCase, ...]).validate_python(raw_cases)
    case_ids = [case.id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Evaluation case IDs must be unique")
    return cases


def select_evaluation_cases(
    cases: Sequence[EvaluationCase],
    *,
    categories: frozenset[str] = frozenset(),
    case_ids: frozenset[str] = frozenset(),
    limit: int | None = None,
) -> tuple[EvaluationCase, ...]:
    """Select an ordered evaluation subset for focused or low-cost runs."""
    if limit is not None and limit < 1:
        raise ValueError("Evaluation limit must be at least 1")
    selected_cases = tuple(
        case
        for case in cases
        if (not categories or case.category in categories)
        and (not case_ids or case.id in case_ids)
    )
    if limit is not None:
        selected_cases = selected_cases[:limit]
    if not selected_cases:
        raise ValueError("No evaluation cases matched the selected filters")
    return selected_cases


async def run_evaluation(
    cases: Sequence[EvaluationCase],
    answerer: Answerer,
    *,
    progress: Callable[[str], None] | None = print,
) -> tuple[EvaluationResult, ...]:
    """Run cases sequentially and preserve failures as zero-scored results."""
    results: list[EvaluationResult] = []
    case_count = len(cases)
    for index, case in enumerate(cases, start=1):
        if progress is not None:
            progress(f"[{index}/{case_count}] {case.id}: {case.question}")
        started_at = time.monotonic()
        try:
            answer = await answerer(case)
            duration = time.monotonic() - started_at
            result = score_evaluation_case(
                case,
                answer,
                duration_seconds=duration,
            )
        except Exception as error:  # noqa: BLE001 - isolate each live evaluation case
            duration = time.monotonic() - started_at
            results.append(_failed_result(case, error, duration))
            continue
        results.append(result)
    return tuple(results)


def score_evaluation_case(
    case: EvaluationCase,
    answer: QuestionAnswer,
    *,
    duration_seconds: float = 0.0,
) -> EvaluationResult:
    """Apply one deterministic rubric to a production answer and its evidence."""
    evidence_by_id = _index_evidence(answer.tool_executions)
    cited_evidence = tuple(
        item
        for citation_id in answer.citation_ids
        for item in evidence_by_id.get(citation_id, ())
    )
    fact_scores = tuple(
        FactScore(
            label=fact.label,
            answer_matched=_contains_any(answer.answer, fact.answer_any),
        )
        for fact in case.facts
    )
    cited_source_types = tuple(
        sorted(
            {
                source_type
                for item in cited_evidence
                if isinstance((source_type := item.get("source_type")), str)
            }
        )
    )

    metrics = MetricScores(
        answer_correctness=_answer_correctness(fact_scores),
        citation_correctness=_citation_score(
            case,
            answer.citation_ids,
            evidence_by_id,
            set(cited_source_types),
        ),
    )
    return EvaluationResult(
        case_id=case.id,
        category=case.category,
        question=case.question,
        answer=answer.answer,
        citation_ids=answer.citation_ids,
        cited_source_types=cited_source_types,
        fact_scores=fact_scores,
        metrics=metrics,
        duration_seconds=duration_seconds,
    )


def summarize_results(results: Sequence[EvaluationResult]) -> EvaluationSummary:
    """Average each metric across all selected cases, including failed cases."""
    case_count = len(results)
    if case_count == 0:
        raise ValueError("Cannot summarize an empty evaluation run")
    return EvaluationSummary(
        case_count=case_count,
        failed_case_count=sum(result.error is not None for result in results),
        answer_correctness=_mean(
            result.metrics.answer_correctness for result in results
        ),
        citation_correctness=_mean(
            result.metrics.citation_correctness for result in results
        ),
        categories=_summarize_categories(results),
    )


def plot_summary(
    summary: EvaluationSummary,
    output_path: Path = DEFAULT_OUTPUT_DIRECTORY / DEFAULT_FIGURE_NAME,
) -> Figure:
    """Return a category-grouped bar plot and save it as a PNG."""
    positions = list(range(len(summary.categories)))
    bar_width = 0.38
    answer_values = [
        category.answer_correctness * 100 for category in summary.categories
    ]
    citation_values = [
        category.citation_correctness * 100 for category in summary.categories
    ]
    answer_positions = [position - bar_width / 2 for position in positions]
    citation_positions = [position + bar_width / 2 for position in positions]

    figure, axis = plt.subplots(figsize=(14, 6))
    answer_bars = axis.bar(
        answer_positions,
        answer_values,
        width=bar_width,
        color="#28666e",
        label="Antwort",
    )
    citation_bars = axis.bar(
        citation_positions,
        citation_values,
        width=bar_width,
        color="#d99126",
        label="Quellen",
    )
    axis.bar_label(
        answer_bars,
        labels=[f"{value:.0f}%" for value in answer_values],
        padding=3,
        fontsize=8,
    )
    axis.bar_label(
        citation_bars,
        labels=[f"{value:.0f}%" for value in citation_values],
        padding=3,
        fontsize=8,
    )
    axis.set_title(f"oBDSChat Evaluation ({summary.case_count} Fragen)")
    axis.set_ylabel("Erfüllung (%)")
    axis.set_ylim(0, 105)
    axis.set_xticks(
        positions,
        [CATEGORY_LABELS[category.category] for category in summary.categories],
        rotation=25,
        ha="right",
    )
    axis.legend()
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", alpha=0.2)
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180, bbox_inches="tight")
    return figure


def write_report(report: EvaluationReport, output_path: Path) -> None:
    """Write detailed machine-readable results as UTF-8 JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


async def run_production_evaluation(
    *,
    questions_path: Path = DEFAULT_QUESTIONS_PATH,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
    categories: frozenset[str] = frozenset(),
    case_ids: frozenset[str] = frozenset(),
    limit: int | None = None,
    show_plot: bool = True,
) -> tuple[EvaluationReport, Figure]:
    """Execute selected cases against production dependencies and save artifacts."""
    settings = load_settings()
    endpoint = settings.resolve_llm_endpoint()
    all_cases = load_evaluation_cases(questions_path)
    cases = select_evaluation_cases(
        all_cases,
        categories=categories,
        case_ids=case_ids,
        limit=limit,
    )
    async with create_client(endpoint) as client:
        answerer = ProductionAnswerer(
            endpoint=endpoint,
            client=client,
            catalog=get_schema_catalog(),
        )
        results = await run_evaluation(cases, answerer)
    summary = summarize_results(results)
    report = EvaluationReport(
        generated_at=datetime.now(ZoneInfo("Europe/Berlin")),
        provider=endpoint.provider,
        route=endpoint.route,
        questions_path=str(questions_path.resolve()),
        summary=summary,
        results=results,
    )
    write_report(report, output_directory / DEFAULT_REPORT_NAME)
    figure = plot_summary(summary, output_directory / DEFAULT_FIGURE_NAME)
    if show_plot:
        plt.show()
    return report, figure


def _index_evidence(
    executions: Sequence[ToolExecution],
) -> dict[str, tuple[Mapping[str, object], ...]]:
    evidence_lists: dict[str, list[Mapping[str, object]]] = {}
    for execution in executions:
        for item in _iter_mappings(execution.result):
            citation_id = item.get(CITATION_ID_FIELD)
            if isinstance(citation_id, str) and citation_id.strip():
                evidence_lists.setdefault(citation_id.strip(), []).append(item)
    return {citation_id: tuple(items) for citation_id, items in evidence_lists.items()}


def _iter_mappings(value: object) -> Iterator[Mapping[str, object]]:
    if isinstance(value, Mapping):
        yield value
        for nested_value in value.values():
            yield from _iter_mappings(nested_value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _iter_mappings(item)


def _answer_correctness(fact_scores: Sequence[FactScore]) -> float:
    return _mean(float(fact.answer_matched) for fact in fact_scores)


def _citation_score(
    case: EvaluationCase,
    citation_ids: Sequence[str],
    evidence_by_id: Mapping[str, Sequence[Mapping[str, object]]],
    cited_source_types: set[str],
) -> float:
    actual_supported = bool(citation_ids)
    citations_resolve = all(
        citation_id in evidence_by_id for citation_id in citation_ids
    )
    expected_sources_present = case.expected_source_types.issubset(cited_source_types)
    return float(
        actual_supported == case.expected_supported
        and citations_resolve
        and expected_sources_present
    )


def _summarize_categories(
    results: Sequence[EvaluationResult],
) -> tuple[CategorySummary, ...]:
    summaries: list[CategorySummary] = []
    for category in CATEGORY_NAMES:
        category_results = tuple(
            result for result in results if result.category == category
        )
        if not category_results:
            continue
        summaries.append(
            CategorySummary(
                category=category,
                case_count=len(category_results),
                answer_correctness=_mean(
                    result.metrics.answer_correctness for result in category_results
                ),
                citation_correctness=_mean(
                    result.metrics.citation_correctness for result in category_results
                ),
            )
        )
    return tuple(summaries)


def _failed_result(
    case: EvaluationCase,
    error: Exception,
    duration_seconds: float,
) -> EvaluationResult:
    return EvaluationResult(
        case_id=case.id,
        category=case.category,
        question=case.question,
        answer=None,
        citation_ids=(),
        cited_source_types=(),
        fact_scores=(),
        metrics=MetricScores(
            answer_correctness=0,
            citation_correctness=0,
        ),
        duration_seconds=duration_seconds,
        error=f"{type(error).__name__}: {error}",
    )


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(_matches_term(text, term) for term in terms)


def _matches_term(text: str, term: str) -> bool:
    if _contains_term(text, term):
        return True
    term_tokens = _tokenize(term)
    if len(term_tokens) < 2:
        return False
    text_tokens = set(_tokenize(text))
    return set(term_tokens).issubset(text_tokens)


def _contains_term(text: str, term: str) -> bool:
    normalized_term = _normalize_text(term)
    if not normalized_term:
        raise ValueError("Evaluation terms must not be empty")
    normalized_text = _normalize_text(text)
    return f" {normalized_term} " in f" {normalized_text} "


def _normalize_text(value: str) -> str:
    return " ".join(_tokenize(value))


def _tokenize(value: str) -> tuple[str, ...]:
    normalized_value = unicodedata.normalize("NFKC", value).casefold()
    return tuple(_TOKEN_PATTERN.findall(normalized_value))


def _mean(values: Iterable[float]) -> float:
    collected_values = tuple(values)
    if not collected_values:
        raise ValueError("Cannot calculate the mean of no values")
    return sum(collected_values) / len(collected_values)


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate oBDSChat against production dependencies.",
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    parser.add_argument(
        "--category", action="append", choices=CATEGORY_NAMES, default=[]
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-show", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run production evaluation from the command line."""
    arguments = _parse_arguments(argv)
    try:
        report, _ = asyncio.run(
            run_production_evaluation(
                questions_path=arguments.questions,
                output_directory=arguments.output_directory,
                categories=frozenset(arguments.category),
                case_ids=frozenset(arguments.case_id),
                limit=arguments.limit,
                show_plot=not arguments.no_show,
            )
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Evaluation could not start: {error}", file=sys.stderr)
        return 1

    summary = report.summary
    print(
        "Evaluation complete: "
        f"{summary.case_count} cases, {summary.failed_case_count} execution failures"
    )
    print(f"Report: {arguments.output_directory / DEFAULT_REPORT_NAME}")
    print(f"Figure: {arguments.output_directory / DEFAULT_FIGURE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
