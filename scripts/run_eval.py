"""Run deterministic, source-grounded evaluation against the production backend."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, Self

import matplotlib.pyplot as plt
import yaml
from matplotlib.figure import Figure
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from backend.config import Settings, load_settings
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
DEFAULT_REPORT_NAME: Final = "results.json"
DEFAULT_FIGURE_NAME: Final = "summary.png"

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

CATEGORY_NAMES: Final[tuple[str, ...]] = (
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
METRIC_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("answer_correctness", "Antwort"),
    ("tool_selection", "Tools"),
    ("citation_correctness", "Quellen"),
    ("unsupported_claims", "Belegtreue"),
)
REGISTERED_TOOL_NAMES: Final = frozenset(tool.name for tool in TOOLS)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactExpectation(_FrozenModel):
    """One benchmark fact and accepted terms in answer and cited evidence."""

    label: str = Field(min_length=1)
    answer_any: tuple[str, ...] = Field(min_length=1)
    evidence_any: tuple[str, ...] = ()


class EvaluationCase(_FrozenModel):
    """Validated deterministic rubric for one German oBDS question."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    category: EvaluationCategory
    question: str = Field(min_length=1)
    obds_version: str | None = None
    expected_supported: bool = True
    facts: tuple[FactExpectation, ...] = Field(min_length=1)
    required_tool_groups: tuple[tuple[str, ...], ...] = Field(min_length=1)
    allowed_tools: frozenset[str] = Field(min_length=1)
    expected_versions: frozenset[str] = frozenset()
    expected_source_types: frozenset[SourceType] = frozenset()
    allowed_source_types: frozenset[SourceType] = frozenset()
    forbidden_answer_terms: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_expectations(self) -> Self:
        """Reject rubrics that cannot produce meaningful deterministic scores."""
        unknown_tools = set(self.allowed_tools.difference(REGISTERED_TOOL_NAMES))
        grouped_tools = {
            tool_name
            for tool_group in self.required_tool_groups
            for tool_name in tool_group
        }
        unknown_tools.update(grouped_tools.difference(REGISTERED_TOOL_NAMES))
        if unknown_tools:
            names = ", ".join(sorted(unknown_tools))
            raise ValueError(f"Unknown evaluation tools: {names}")
        if any(not tool_group for tool_group in self.required_tool_groups):
            raise ValueError("Required tool groups must not be empty")
        if not grouped_tools.issubset(self.allowed_tools):
            raise ValueError("Required tools must also be allowed")
        if not self.expected_source_types.issubset(self.allowed_source_types):
            raise ValueError("Expected source types must also be allowed")
        if self.expected_supported and not self.expected_source_types:
            raise ValueError("Supported cases require an expected source type")
        if not self.expected_supported and self.expected_source_types:
            raise ValueError("Unsupported cases cannot expect cited sources")
        if self.expected_supported and any(
            not fact.evidence_any for fact in self.facts
        ):
            raise ValueError("Supported facts require evidence terms")
        return self


class FactScore(_FrozenModel):
    """Observed answer and evidence match for one expected fact."""

    label: str
    answer_matched: bool
    evidence_matched: bool | None


class MetricScores(_FrozenModel):
    """Four requested evaluation metrics, each normalized to zero through one."""

    answer_correctness: float = Field(ge=0, le=1)
    tool_selection: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)
    unsupported_claims: float = Field(ge=0, le=1)


class EvaluationResult(_FrozenModel):
    """Detailed result for one executed evaluation case."""

    case_id: str
    category: EvaluationCategory
    question: str
    answer: str | None
    called_tools: tuple[str, ...]
    used_versions: tuple[str, ...]
    citation_ids: tuple[str, ...]
    cited_source_types: tuple[str, ...]
    fact_scores: tuple[FactScore, ...]
    forbidden_terms_found: tuple[str, ...]
    unexpected_tools: tuple[str, ...]
    metrics: MetricScores
    duration_seconds: float = Field(ge=0)
    error: str | None = None


class EvaluationSummary(_FrozenModel):
    """Aggregate metrics for a completed evaluation run."""

    case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    answer_correctness: float = Field(ge=0, le=1)
    tool_selection: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)
    unsupported_claims: float = Field(ge=0, le=1)


class EvaluationReport(_FrozenModel):
    """Serializable record of one production evaluation run."""

    generated_at: datetime
    model: str
    questions_path: str
    summary: EvaluationSummary
    results: tuple[EvaluationResult, ...]


@dataclass(frozen=True, slots=True)
class ProductionAnswerer:
    """Call the same model and local tools used by the production query path."""

    settings: Settings
    client: OpenAI
    catalog: SchemaCatalog

    def __call__(self, case: EvaluationCase) -> QuestionAnswer:
        constraint = None
        if case.obds_version is not None:
            constraint = self.catalog.resolve_version(case.obds_version)
        version_context = VersionContext(
            default_version=self.catalog.latest_version,
            available_versions=self.catalog.versions,
            constraint=constraint,
        )
        return answer_question(
            case.question,
            version_context=version_context,
            tools=TOOLS,
            client=self.client,
            settings=self.settings,
        )


Answerer = Callable[[EvaluationCase], QuestionAnswer]


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


def run_evaluation(
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
            answer = answerer(case)
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
    evidence_text = _json_text(cited_evidence)
    fact_scores = tuple(
        FactScore(
            label=fact.label,
            answer_matched=_contains_any(answer.answer, fact.answer_any),
            evidence_matched=(
                _contains_any(evidence_text, fact.evidence_any)
                if case.expected_supported
                else None
            ),
        )
        for fact in case.facts
    )
    forbidden_terms_found = tuple(
        term
        for term in case.forbidden_answer_terms
        if _contains_term(answer.answer, term)
    )
    called_tools = tuple(execution.name for execution in answer.tool_executions)
    called_tool_set = set(called_tools)
    unexpected_tools = tuple(sorted(called_tool_set.difference(case.allowed_tools)))
    used_versions = _used_versions(answer.tool_executions)
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
        tool_selection=_tool_selection_score(
            case,
            called_tool_set,
            set(used_versions),
            unexpected_tools,
        ),
        citation_correctness=_citation_score(
            case,
            answer.citation_ids,
            evidence_by_id,
            set(cited_source_types),
        ),
        unsupported_claims=_unsupported_claims_score(
            case,
            answer.citation_ids,
            fact_scores,
            forbidden_terms_found,
        ),
    )
    return EvaluationResult(
        case_id=case.id,
        category=case.category,
        question=case.question,
        answer=answer.answer,
        called_tools=called_tools,
        used_versions=used_versions,
        citation_ids=answer.citation_ids,
        cited_source_types=cited_source_types,
        fact_scores=fact_scores,
        forbidden_terms_found=forbidden_terms_found,
        unexpected_tools=unexpected_tools,
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
        tool_selection=_mean(result.metrics.tool_selection for result in results),
        citation_correctness=_mean(
            result.metrics.citation_correctness for result in results
        ),
        unsupported_claims=_mean(
            result.metrics.unsupported_claims for result in results
        ),
    )


def plot_summary(
    summary: EvaluationSummary,
    output_path: Path = DEFAULT_OUTPUT_DIRECTORY / DEFAULT_FIGURE_NAME,
) -> Figure:
    """Return an inline-friendly bar plot and save it as a PNG."""
    metric_values = [
        getattr(summary, metric_name) * 100 for metric_name, _ in METRIC_LABELS
    ]
    metric_labels = [label for _, label in METRIC_LABELS]
    figure, axis = plt.subplots(figsize=(8, 4.5))
    bars = axis.bar(metric_labels, metric_values, color="#28666e", width=0.62)
    axis.bar_label(bars, labels=[f"{value:.1f}%" for value in metric_values], padding=3)
    axis.set_title(f"oBDSChat Evaluation ({summary.case_count} Fragen)")
    axis.set_ylabel("Erfüllung (%)")
    axis.set_ylim(0, 105)
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


def run_production_evaluation(
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
    answerer = ProductionAnswerer(
        settings=settings,
        client=create_client(settings),
        catalog=get_schema_catalog(),
    )
    all_cases = load_evaluation_cases(questions_path)
    cases = select_evaluation_cases(
        all_cases,
        categories=categories,
        case_ids=case_ids,
        limit=limit,
    )
    results = run_evaluation(cases, answerer)
    summary = summarize_results(results)
    report = EvaluationReport(
        generated_at=datetime.now(UTC),
        model=settings.openai_model,
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


def _used_versions(executions: Sequence[ToolExecution]) -> tuple[str, ...]:
    versions = {
        version
        for execution in executions
        if execution.error is None
        and isinstance((version := execution.arguments.get("version")), str)
    }
    return tuple(sorted(versions, key=_version_key))


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in version.split(".")
    )


def _answer_correctness(fact_scores: Sequence[FactScore]) -> float:
    return _mean(float(fact.answer_matched) for fact in fact_scores)


def _tool_selection_score(
    case: EvaluationCase,
    called_tools: set[str],
    used_versions: set[str],
    unexpected_tools: Sequence[str],
) -> float:
    groups_satisfied = all(
        called_tools.intersection(tool_group)
        for tool_group in case.required_tool_groups
    )
    versions_satisfied = case.expected_versions.issubset(used_versions)
    return float(groups_satisfied and versions_satisfied and not unexpected_tools)


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
    sources_allowed = cited_source_types.issubset(case.allowed_source_types)
    return float(
        actual_supported == case.expected_supported
        and citations_resolve
        and expected_sources_present
        and sources_allowed
    )


def _unsupported_claims_score(
    case: EvaluationCase,
    citation_ids: Sequence[str],
    fact_scores: Sequence[FactScore],
    forbidden_terms_found: Sequence[str],
) -> float:
    actual_supported = bool(citation_ids)
    grounded_asserted_facts = all(
        not fact.answer_matched or fact.evidence_matched is not False
        for fact in fact_scores
    )
    return float(
        actual_supported == case.expected_supported
        and grounded_asserted_facts
        and not forbidden_terms_found
    )


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
        called_tools=(),
        used_versions=(),
        citation_ids=(),
        cited_source_types=(),
        fact_scores=(),
        forbidden_terms_found=(),
        unexpected_tools=(),
        metrics=MetricScores(
            answer_correctness=0,
            tool_selection=0,
            citation_correctness=0,
            unsupported_claims=0,
        ),
        duration_seconds=duration_seconds,
        error=f"{type(error).__name__}: {error}",
    )


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(_matches_term(text, term) for term in terms)


def _matches_term(text: str, term: str) -> bool:
    if _contains_term(text, term):
        return True
    term_tokens = set(re.findall(r"\w+", _normalize_text(term)))
    if len(term_tokens) < 3:
        return False
    text_tokens = set(re.findall(r"\w+", _normalize_text(text)))
    return term_tokens.issubset(text_tokens)


def _contains_term(text: str, term: str) -> bool:
    normalized_term = _normalize_text(term)
    if not normalized_term:
        raise ValueError("Evaluation terms must not be empty")
    prefix = r"(?<!\w)" if normalized_term[0].isalnum() else ""
    suffix = r"(?!\w)" if normalized_term[-1].isalnum() else ""
    pattern = f"{prefix}{re.escape(normalized_term)}{suffix}"
    return re.search(pattern, _normalize_text(text)) is not None


def _normalize_text(value: str) -> str:
    normalized_value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized_value).strip()


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _mean(values: Iterator[float]) -> float:
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
        report, _ = run_production_evaluation(
            questions_path=arguments.questions,
            output_directory=arguments.output_directory,
            categories=frozenset(arguments.category),
            case_ids=frozenset(arguments.case_id),
            limit=arguments.limit,
            show_plot=not arguments.no_show,
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
