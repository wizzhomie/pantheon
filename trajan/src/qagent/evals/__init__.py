"""Offline evaluation: dataset, scoring, metrics, report and comparison."""

from .compare import compare, load_report
from .compare import render_markdown as render_diff_markdown
from .compare import write_markdown as write_diff_markdown
from .dataset import Dataset
from .metrics import CaseRecord, build_metrics, percentile
from .report import (
    build_report,
    git_revision,
    make_run_id,
    render_markdown,
    write_json,
    write_markdown,
)
from .runner import evaluate
from .scoring import CaseScore, score_case

__all__ = [
    "CaseRecord",
    "CaseScore",
    "Dataset",
    "build_metrics",
    "build_report",
    "compare",
    "evaluate",
    "git_revision",
    "load_report",
    "make_run_id",
    "percentile",
    "render_diff_markdown",
    "render_markdown",
    "score_case",
    "write_diff_markdown",
    "write_json",
    "write_markdown",
]
