import itertools
import re

import pytest
import rstr

from ocs_deploy.waf_utils import (
    NoUserAgent_HEADER,
    SizeRestrictions_BODY,
    compact_waf_regexes,
    compact_waf_regexes_simply,
    create_waf_regex_groupings,
)

PATTERNS_TO_TEST = SizeRestrictions_BODY + NoUserAgent_HEADER


def _generate_matching_example(pattern):
    """
    Taking a regex pattern and return a string that will match it
    """
    naive_example = (
        pattern.replace(r"([-a-zA-Z0-9_]+)", "team-one_two3")
        .replace(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            "08628b8f-bbee-4237-badd-a991e988b7fe",
        )
        .replace(r"([0-9]+)", "42")
        .replace(r"([^/]+)", "XYZ")
        .replace(r"\.", ".")
        .replace(r"\-", "-")
        .replace(r"^", "")
        .replace(r"$", "")
    )
    example = rstr.xeger(pattern)
    return [naive_example, example]


EXAMPLES = list(
    itertools.chain.from_iterable(
        _generate_matching_example(pattern) for pattern in PATTERNS_TO_TEST
    )
)


def test_compact_waf_regexes_simply__single_pattern():
    regex = r"^/a/([-a-zA-Z0-9_]+)/assistants/new/$"
    assert compact_waf_regexes_simply([regex]) == [regex]


def test_compact_waf_regexes_simply__two_patterns():
    patter1 = r"^/a/([-a-zA-Z0-9_]+)/assistants/new/$"
    pattern2 = r"^/slack/events$"
    assert compact_waf_regexes_simply([patter1, pattern2]) == [rf"{patter1}|{pattern2}"]


@pytest.mark.parametrize("example", EXAMPLES)
def test_compact_waf_regexes_simply__match_examples(example):
    _test_compact_function_against_examples(compact_waf_regexes_simply, example)


def test_compact_waf_regexes__single_pattern():
    prefix = r"^/a/([-a-zA-Z0-9_]+)/"
    suffix = r"experiments/source_material/new/"
    assert compact_waf_regexes([rf"{prefix}{suffix}$"]) == [rf"{prefix}({suffix})$"]


def test_compact_waf_regexes__two_patterns():
    prefix = r"^/a/([-a-zA-Z0-9_]+)/"
    suffix1 = r"experiments/source_material/new/"
    suffix2 = r"pipelines/data/([0-9]+)/"
    assert compact_waf_regexes(
        [
            rf"{prefix}{suffix1}$",
            rf"{prefix}{suffix2}$",
        ]
    ) == [rf"{prefix}({suffix1}|{suffix2})$"]


def test_compact_waf_regexes__single_non_matching_pattern():
    assert compact_waf_regexes([r"abc"]) == [r"abc"]


@pytest.mark.parametrize("example", EXAMPLES)
def test_compact_waf_regexes__match_examples(example):
    _test_compact_function_against_examples(compact_waf_regexes, example)


def test_compact_waf_regexes__pattern_length():
    compacted_patterns = compact_waf_regexes(PATTERNS_TO_TEST)
    for pattern in compacted_patterns:
        assert len(pattern) <= 200, pattern


def test_compact_regex_lists__restricts_group_sizes():
    groups = create_waf_regex_groupings(PATTERNS_TO_TEST)
    assert len(groups) <= 10, f"Max regex groups is 10: {len(groups)}"
    for idx, group in enumerate(groups):
        assert len(group) <= 10, f"{idx} has too many regexes: {len(group)}"


def _test_compact_function_against_examples(compact_function, example):
    compacted_patterns = compact_function(PATTERNS_TO_TEST)

    # make sure that all of the example strings match
    assert any(re.match(pattern, example) for pattern in compacted_patterns), (
        example,
        compacted_patterns,
    )

    # make sure not just any string matches
    for example in ["fish", "/a/b/c/d/"]:
        assert not any(re.match(pattern, example) for pattern in compacted_patterns), (
            example,
            compacted_patterns,
        )
