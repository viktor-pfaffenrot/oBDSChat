"""Tests for deterministic production evaluation and reporting."""

import asyncio
from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from matplotlib.figure import Figure

from backend.llm import QuestionAnswer, ToolExecution
from scripts.run_eval import (
    CategorySummary,
    EvaluationCase,
    EvaluationSummary,
    FactExpectation,
    plot_summary,
    run_evaluation,
    score_evaluation_case,
    select_evaluation_cases,
    summarize_results,
)


def _supported_case(case_id: str = "supported-case") -> EvaluationCase:
    return EvaluationCase(
        id=case_id,
        category="datatype",
        question="Welchen Datentyp hat Testfeld?",
        obds_version="3.0.5",
        facts=(
            FactExpectation(
                label="Datentyp",
                answer_any=("xs:string",),
            ),
        ),
        expected_source_types=frozenset({"xsd"}),
    )


def _supported_answer(answer: str = "Der Datentyp ist xs:string.") -> QuestionAnswer:
    evidence = {
        "citation_id": "xsd:3.0.5:/oBDS/Testfeld",
        "source_type": "xsd",
        "path": "/oBDS/Testfeld",
        "datatype": "xs:string",
    }
    return QuestionAnswer(
        answer=answer,
        tool_executions=(
            ToolExecution(
                name="get_schema_element",
                arguments={"name": "Testfeld", "version": "3.0.5"},
                result=[evidence],
                output="[]",
            ),
        ),
        citation_ids=("xsd:3.0.5:/oBDS/Testfeld",),
    )


def test_supported_answer_scores_both_metrics() -> None:
    result = score_evaluation_case(
        _supported_case(),
        _supported_answer(),
        duration_seconds=1.25,
    )

    assert result.metrics.answer_correctness == 1
    assert result.metrics.citation_correctness == 1
    assert result.cited_source_types == ("xsd",)
    assert result.fact_scores[0].answer_matched is True
    assert result.duration_seconds == 1.25


def test_fact_matching_ignores_punctuation_extra_words_and_word_order() -> None:
    case = _supported_case().model_copy(
        update={
            "facts": (
                FactExpectation(
                    label="Kardinalität",
                    answer_any=("minOccurs 1",),
                ),
                FactExpectation(
                    label="Bedeutung",
                    answer_any=("höchste zum Diagnosedatum erreichte Sicherheit",),
                ),
            )
        }
    )
    answer = _supported_answer(
        'minOccurs="1". Zum Diagnosedatum ist die erreichte höchste diagnostische '
        "Sicherheit gemeint."
    )

    result = score_evaluation_case(case, answer)

    assert result.metrics.answer_correctness == 1
    assert all(fact.answer_matched for fact in result.fact_scores)


def test_extra_official_source_is_allowed() -> None:
    answer = _supported_answer()
    guide_evidence = {
        "citation_id": "umsetzungsleitfaden:42",
        "source_type": "umsetzungsleitfaden",
        "source_id": 42,
    }
    answer_with_extra_source = QuestionAnswer(
        answer=answer.answer,
        tool_executions=(
            *answer.tool_executions,
            ToolExecution(
                name="search_umsetzungsleitfaden",
                arguments={"query": "Testfeld"},
                result=[guide_evidence],
                output="[]",
            ),
        ),
        citation_ids=(*answer.citation_ids, "umsetzungsleitfaden:42"),
    )

    result = score_evaluation_case(_supported_case(), answer_with_extra_source)

    assert result.metrics.citation_correctness == 1
    assert result.cited_source_types == ("umsetzungsleitfaden", "xsd")


def test_missing_expected_source_fails_citation_score() -> None:
    guide_evidence = {
        "citation_id": "umsetzungsleitfaden:42",
        "source_type": "umsetzungsleitfaden",
        "source_id": 42,
    }
    answer = QuestionAnswer(
        answer="Der Datentyp ist xs:string.",
        tool_executions=(
            ToolExecution(
                name="search_umsetzungsleitfaden",
                arguments={"query": "Testfeld"},
                result=[guide_evidence],
                output="[]",
            ),
        ),
        citation_ids=("umsetzungsleitfaden:42",),
    )

    result = score_evaluation_case(_supported_case(), answer)

    assert result.metrics.answer_correctness == 1
    assert result.metrics.citation_correctness == 0


def test_unknown_citation_id_fails_citation_score() -> None:
    answer = _supported_answer()
    answer_with_unknown_citation = QuestionAnswer(
        answer=answer.answer,
        tool_executions=answer.tool_executions,
        citation_ids=(*answer.citation_ids, "xsd:3.0.5:/oBDS/Unknown"),
    )

    result = score_evaluation_case(_supported_case(), answer_with_unknown_citation)

    assert result.metrics.citation_correctness == 0


def test_unsupported_refusal_scores_without_citations() -> None:
    case = EvaluationCase(
        id="unsupported-case",
        category="ambiguous_or_unanswerable",
        question="Welche Werte hat Zauberstatus?",
        expected_supported=False,
        facts=(
            FactExpectation(
                label="Ablehnung",
                answer_any=("nicht gefunden",),
            ),
        ),
    )
    answer = QuestionAnswer(
        answer="Das Feld wurde nicht gefunden.",
        tool_executions=(),
    )

    result = score_evaluation_case(case, answer)

    assert result.metrics.answer_correctness == 1
    assert result.metrics.citation_correctness == 1


def test_run_continues_and_summarizes_each_category() -> None:
    working_case = _supported_case("works")
    failing_case = _supported_case("fails").model_copy(
        update={"category": "cardinality"}
    )

    async def answerer(case: EvaluationCase) -> QuestionAnswer:
        if case.id == "fails":
            raise RuntimeError("dependency unavailable")
        return _supported_answer()

    results = asyncio.run(
        run_evaluation((working_case, failing_case), answerer, progress=None)
    )
    summary = summarize_results(results)

    assert len(results) == 2
    assert results[1].error == "RuntimeError: dependency unavailable"
    assert summary.failed_case_count == 1
    assert summary.answer_correctness == 0.5
    assert summary.citation_correctness == 0.5
    assert [category.category for category in summary.categories] == [
        "datatype",
        "cardinality",
    ]
    assert [category.answer_correctness for category in summary.categories] == [
        1,
        0,
    ]


def test_case_selection_filters_categories_ids_and_limit() -> None:
    cases = (_supported_case("first"), _supported_case("second"))

    selected = select_evaluation_cases(
        cases,
        categories=frozenset({"datatype"}),
        case_ids=frozenset({"second"}),
        limit=1,
    )

    assert selected == (cases[1],)
    with pytest.raises(ValueError, match="No evaluation cases"):
        select_evaluation_cases(cases, case_ids=frozenset({"missing"}))


def test_summary_plot_groups_metrics_by_category(tmp_path: Path) -> None:
    summary = EvaluationSummary(
        case_count=5,
        failed_case_count=0,
        answer_correctness=0.72,
        citation_correctness=0.88,
        categories=(
            CategorySummary(
                category="field_meaning",
                case_count=2,
                answer_correctness=0.8,
                citation_correctness=0.9,
            ),
            CategorySummary(
                category="datatype",
                case_count=3,
                answer_correctness=0.6,
                citation_correctness=1,
            ),
        ),
    )
    output_path = tmp_path / "summary.png"

    figure = plot_summary(summary, output_path)

    assert isinstance(figure, Figure)
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert [tick.get_text() for tick in figure.axes[0].get_xticklabels()] == [
        "Feldbedeutung",
        "Datentypen",
    ]
    legend = figure.axes[0].get_legend()
    assert legend is not None
    assert [text.get_text() for text in legend.get_texts()] == [
        "Antwort",
        "Quellen",
    ]
    assert len(figure.axes[0].patches) == 4
    plt.close(figure)
