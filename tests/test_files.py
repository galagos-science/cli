"""Tests for galagos_cli.commands.files._walk_tree."""
from __future__ import annotations

from galagos_cli.commands.files import _walk_tree


def test_flattens_files_at_root():
    tree = {
        "a.txt": {"type": "file", "size": 10},
        "b.txt": {"type": "file", "size": 20},
    }
    assert _walk_tree(tree) == [("a.txt", 10), ("b.txt", 20)]


def test_recurses_into_directories():
    tree = {
        "results": {
            "hits.tsv": {"type": "file", "size": 1024},
        },
        "top.txt": {"type": "file", "size": 5},
    }
    rows = _walk_tree(tree)
    assert rows == [("results/hits.tsv", 1024), ("top.txt", 5)]


def test_empty_directory_is_listed_with_trailing_slash():
    tree = {"empty": {}}
    assert _walk_tree(tree) == [("empty/", None)]


def test_results_are_sorted_alphabetically_within_each_level():
    tree = {
        "z.txt": {"type": "file", "size": 1},
        "a.txt": {"type": "file", "size": 2},
        "mid": {
            "z.txt": {"type": "file", "size": 3},
            "a.txt": {"type": "file", "size": 4},
        },
    }
    paths = [p for p, _ in _walk_tree(tree)]
    assert paths == ["a.txt", "mid/a.txt", "mid/z.txt", "z.txt"]


def test_nested_directories_are_walked_deeply():
    tree = {
        "a": {"b": {"c": {"deep.txt": {"type": "file", "size": 7}}}},
    }
    assert _walk_tree(tree) == [("a/b/c/deep.txt", 7)]


def test_size_can_be_missing_on_a_file_node():
    tree = {"x.txt": {"type": "file"}}
    assert _walk_tree(tree) == [("x.txt", None)]
