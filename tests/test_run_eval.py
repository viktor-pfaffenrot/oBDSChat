"""Tests for deterministic production evaluation and reporting."""

from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from matplotlib.figure import Figure

from backend.llm import QuestionAnswer, ToolExecution
from scripts.run_eval import (
    CATEGORY_NAMES,
    EvaluationCase,
    EvaluationSummary,
    FactExpectation,
    load_evaluation_cases,
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
                evidence_any=("xs:string",),
            ),
        ),
        required_tool_groups=(("get_schema_element", "search_schema"),),
        allowed_tools=frozenset({"get_schema_element", "search_schema"}),
        expected_versions=frozenset({"3.0.5"}),
        expected_source_types=frozenset({"xsd"}),
        allowed_source_types=frozenset({"xsd"}),
        forbidden_answer_terms=("xs:integer",),
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


def test_question_corpus_has_requested_size_and_category_coverage() -> None:
    cases = load_evaluation_cases()

    assert len(cases) == 76
    assert Counter(case.category for case in cases) == {
        "field_meaning": 9,
        "allowed_values": 9,
        "datatype": 9,
        "cardinality": 9,
        "xml_hierarchy": 10,
        "implementation_guidance": 9,
        "version": 8,
        "mixed_source": 7,
        "ambiguous_or_unanswerable": 6,
    }
    assert {case.category for case in cases} == set(CATEGORY_NAMES)


def test_supported_answer_scores_all_metrics() -> None:
    result = score_evaluation_case(
        _supported_case(),
        _supported_answer(),
        duration_seconds=1.25,
    )

    assert result.metrics.answer_correctness == 1
    assert result.metrics.tool_selection == 1
    assert result.metrics.citation_correctness == 1
    assert result.metrics.unsupported_claims == 1
    assert result.used_versions == ("3.0.5",)
    assert result.cited_source_types == ("xsd",)
    assert result.fact_scores[0].answer_matched is True
    assert result.fact_scores[0].evidence_matched is True
    assert result.duration_seconds == 1.25


def test_fact_matching_allows_extra_words_and_changed_word_order() -> None:
    case = _supported_case()
    case = case.model_copy(
        update={
            "facts": (
                FactExpectation(
                    label="Bedeutung",
                    answer_any=("höchste zum Diagnosedatum erreichte Sicherheit",),
                    evidence_any=("höchste erreichte Sicherheit",),
                ),
            )
        }
    )
    answer = _supported_answer(
        "Gemeint ist die höchste zum Diagnosedatum erreichte diagnostische Sicherheit."
    )
    answer = QuestionAnswer(
        answer=answer.answer,
        tool_executions=(
            ToolExecution(
                name="get_schema_element",
                arguments={"name": "Testfeld", "version": "3.0.5"},
                result=[
                    {
                        "citation_id": "xsd:3.0.5:/oBDS/Testfeld",
                        "source_type": "xsd",
                        "documentation": "Höchste erreichte diagnostische Sicherheit.",
                    }
                ],
                output="[]",
            ),
        ),
        citation_ids=answer.citation_ids,
    )

    result = score_evaluation_case(case, answer)

    assert result.metrics.answer_correctness == 1
    assert result.fact_scores[0].evidence_matched is True


def test_unexpected_tool_forbidden_claim_and_missing_evidence_are_reported() -> None:
    answer = _supported_answer("Der Datentyp ist xs:string, nicht xs:integer.")
    ungrounded_evidence = {
        "citation_id": "xsd:3.0.5:/oBDS/Testfeld",
        "source_type": "xsd",
        "path": "/oBDS/Testfeld",
        "datatype": "xs:date",
    }
    extra_execution = ToolExecution(
        name="search_umsetzungsleitfaden",
        arguments={"query": "Test", "version": "3.0.5"},
        result=[],
        output="[]",
    )
    altered_answer = QuestionAnswer(
        answer=answer.answer,
        tool_executions=(
            ToolExecution(
                name="get_schema_element",
                arguments={"name": "Testfeld", "version": "3.0.5"},
                result=[ungrounded_evidence],
                output="[]",
            ),
            extra_execution,
        ),
        citation_ids=answer.citation_ids,
    )

    result = score_evaluation_case(_supported_case(), altered_answer)

    assert result.metrics.answer_correctness == 1
    assert result.metrics.tool_selection == 0
    assert result.metrics.citation_correctness == 1
    assert result.metrics.unsupported_claims == 0
    assert result.unexpected_tools == ("search_umsetzungsleitfaden",)
    assert result.forbidden_terms_found == ("xs:integer",)
    assert result.fact_scores[0].evidence_matched is False


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
        required_tool_groups=(("search_schema",),),
        allowed_tools=frozenset({"search_schema"}),
    )
    answer = QuestionAnswer(
        answer="Das Feld wurde nicht gefunden.",
        tool_executions=(
            ToolExecution(
                name="search_schema",
                arguments={"query": "Zauberstatus", "version": "3.0.5"},
                result=[],
                output="[]",
            ),
        ),
    )

    result = score_evaluation_case(case, answer)

    assert result.metrics.answer_correctness == 1
    assert result.metrics.tool_selection == 1
    assert result.metrics.citation_correctness == 1
    assert result.metrics.unsupported_claims == 1
    assert result.fact_scores[0].evidence_matched is None


def test_run_continues_after_case_failure_and_summary_includes_zero_scores() -> None:
    cases = (_supported_case("works"), _supported_case("fails"))

    def answerer(case: EvaluationCase) -> QuestionAnswer:
        if case.id == "fails":
            raise RuntimeError("dependency unavailable")
        return _supported_answer()

    results = run_evaluation(cases, answerer, progress=None)
    summary = summarize_results(results)

    assert len(results) == 2
    assert results[1].error == "RuntimeError: dependency unavailable"
    assert results[1].metrics.answer_correctness == 0
    assert summary.failed_case_count == 1
    assert summary.answer_correctness == 0.5
    assert summary.tool_selection == 0.5
    assert summary.citation_correctness == 0.5
    assert summary.unsupported_claims == 0.5


def test_case_selection_filters_categories_ids_and_limit() -> None:
    cases = load_evaluation_cases()
    target = next(case for case in cases if case.category == "datatype")

    selected = select_evaluation_cases(
        cases,
        categories=frozenset({"datatype"}),
        case_ids=frozenset({target.id}),
        limit=1,
    )

    assert selected == (target,)
    with pytest.raises(ValueError, match="No evaluation cases"):
        select_evaluation_cases(cases, case_ids=frozenset({"missing"}))


def test_summary_plot_returns_figure_and_writes_png(tmp_path: Path) -> None:
    summary = EvaluationSummary(
        case_count=75,
        failed_case_count=2,
        answer_correctness=0.8,
        tool_selection=0.7,
        citation_correctness=0.9,
        unsupported_claims=0.85,
    )
    output_path = tmp_path / "summary.png"

    figure = plot_summary(summary, output_path)

    assert isinstance(figure, Figure)
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert [tick.get_text() for tick in figure.axes[0].get_xticklabels()] == [
        "Antwort",
        "Tools",
        "Quellen",
        "Belegtreue",
    ]
    plt.close(figure)
