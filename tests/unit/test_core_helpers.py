"""C4.1 — Deterministic-core unit tests for core helper functions."""

import pytest
from config.config import _safe_filename, dict_merge


@pytest.mark.parametrize(
    "name,expected",
    [
        ("hello world", "hello_world"),
        ("File/Name:Test!", "File_Name_Test_"),
        ("   leading dots", "leading_dots"),
        ("a" * 100, "a" * 80),  # truncation
    ],
)
def test_safe_filename(name, expected):
    result = _safe_filename(name)
    assert result == expected
    assert len(result) <= 80


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ({}, {}, {}),
        ({"x": 1}, {"x": 2}, {"x": 2}),
        ({"x": {"y": 1}}, {"x": {"z": 2}}, {"x": {"y": 1, "z": 2}}),
        ({"x": 1}, {"y": 2}, {"x": 1, "y": 2}),
        ({"x": {"y": 1, "z": 2}}, {"x": {"y": 10}}, {"x": {"y": 10, "z": 2}}),
    ],
)
def test_dict_merge(a, b, expected):
    assert dict_merge(a, b) == expected


def test_dict_merge_immutable_input():
    """dict_merge must not mutate the input dicts."""
    a = {"x": {"y": 1}}
    b = {"x": {"z": 2}}
    result = dict_merge(a, b)
    assert a == {"x": {"y": 1}}  # unchanged
    assert b == {"x": {"z": 2}}  # unchanged
    assert result == {"x": {"y": 1, "z": 2}}
