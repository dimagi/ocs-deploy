from collections import defaultdict
from itertools import islice
from typing import List, Tuple

# URI patterns for endpoints that can send large POST bodies
# These bypass only SizeRestrictions_BODY, all other protections remain active
SizeRestrictions_BODY = [
    r"^/a/([-a-zA-Z0-9_]+)/assistants/new/$",
    r"^/a/([-a-zA-Z0-9_]+)/documents/collections/([0-9]+)/add_files$",
    r"^/a/([-a-zA-Z0-9_]+)/evaluations/dataset/([0-9]+)/add\-sessions/$",
    r"^/a/([-a-zA-Z0-9_]+)/evaluations/dataset/new/$",
    r"^/a/([-a-zA-Z0-9_]+)/evaluations/evaluator/([0-9]+)/$",
    r"^/a/([-a-zA-Z0-9_]+)/evaluations/evaluator/new/$",
    r"^/a/([-a-zA-Z0-9_]+)/evaluations/parse_csv_columns/$",
    r"^/a/([-a-zA-Z0-9_]+)/experiments/e/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/v/([0-9]+)/session/([^/]+)/embed/message/$",
    r"^/a/([-a-zA-Z0-9_]+)/experiments/e/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/v/([0-9]+)/session/([^/]+)/message/$",
    r"^/a/([-a-zA-Z0-9_]+)/experiments/source_material/([0-9]+)/$",
    r"^/a/([-a-zA-Z0-9_]+)/experiments/source_material/new/$",
    r"^/a/([-a-zA-Z0-9_]+)/pipelines/data/([0-9]+)/$",
    r"^/anymail/amazon_ses/inbound/$",
    r"^/anymail/mailgun/inbound(_mime)?/$",
    r"^/api/(?:v1/)?chat/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/message/$",
    r"^/api/(?:v1/)?chat/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/upload/$",
    r"^/channels/commcare_connect/incoming_message$",
    r"^/slack/events$",
    r"^/users/profile/upload\-image/$",
]


# URI patterns for endpoints that may not send User-Agent header
# These bypass only NoUserAgent_HEADER, all other protections remain active
NoUserAgent_HEADER = [
    r"^/$",
    r"^/a/([-a-zA-Z0-9_]+)/chatbots/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/s/([^/]+)/chat/$",
    r"^/a/([-a-zA-Z0-9_]+)/chatbots/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/start/$",
    r"^/about/$",
    r"^/api/docs/$",
    r"^/applications/$",
    r"^/channels/sureadhere/([^/]+)/incoming_message$",
    r"^/channels/telegram/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    r"^/contact/$",
    r"^/open\-opportunities/$",
    r"^/platform/$",
    r"^/robots\.txt$",
    r"^/sitemap\.xml$",
]
COMPACTIBLE_AFFIXES = (
    (r"^/a/([-a-zA-Z0-9_]+)/", "$"),
    (r"^/channels/", "$"),
    (r"^/", "$"),
)


def compact_waf_regexes_simply(patterns: List[str], max_length: int = 200) -> List[str]:
    """
    Compact multiple patterns into combined regexes using OR (|) operator,
    respecting max_length constraint.
    """
    compacted_regexes = []
    # Tracked as a list rather than a concatenated string: an empty pattern is a legitimate
    # alternative (it is what "^/$" reduces to once its affixes are stripped), and testing the
    # buffer for truthiness silently discarded it.
    buffer: List[str] = []
    buffer_length = 0
    for pattern in patterns:
        # +1 for the "|" that will join this pattern to a non-empty buffer
        if buffer and buffer_length + len(pattern) + 1 > max_length:
            compacted_regexes.append("|".join(buffer))
            buffer, buffer_length = [], 0
        buffer_length += len(pattern) + (1 if buffer else 0)
        buffer.append(pattern)
    if buffer:
        compacted_regexes.append("|".join(buffer))
    return compacted_regexes


def compact_waf_regexes(
    patterns: List[str],
    compactible_affixes: Tuple[Tuple[str, str], ...] = COMPACTIBLE_AFFIXES,
    max_length: int = 200,
) -> List[str]:
    """
    Compact regexes into as few as possible regexes each of which is no longer
    than `max_length` characters. Groups patterns by common prefixes/suffixes.
    """
    patterns_grouped_by_affix = defaultdict(list)
    non_matching_patterns = []

    # Group patterns by matching prefix/suffix pairs
    for pattern in patterns:
        for prefix, suffix in compactible_affixes:
            # Strictly longer than the affixes: a pattern that is *only* its affixes (e.g. "^/$")
            # would reduce to an empty alternative, so leave it standalone instead.
            if (
                pattern.startswith(prefix)
                and pattern.endswith(suffix)
                and len(pattern) > len(prefix + suffix)
            ):
                patterns_grouped_by_affix[(prefix, suffix)].append(
                    pattern[len(prefix) : -len(suffix)]
                )
                break
        else:
            non_matching_patterns.append(pattern)

    # Create intermediate compacted regexes
    intermediate_compacted_regexes = [
        f"{prefix}({regex}){suffix}"
        for (prefix, suffix), grouped_patterns in patterns_grouped_by_affix.items()
        for regex in compact_waf_regexes_simply(
            grouped_patterns, max_length=max_length - len(prefix + suffix) - 2
        )
    ] + compact_waf_regexes_simply(non_matching_patterns, max_length=max_length)

    # Sort and further compact
    intermediate_compacted_regexes.sort(key=lambda r: len(r))
    final_compacted_regexes = []

    while intermediate_compacted_regexes:
        shortest = intermediate_compacted_regexes[0]
        longest = intermediate_compacted_regexes[-1]
        if (
            len(shortest) + len(longest) + 1 > max_length
            or len(intermediate_compacted_regexes) == 1
        ):
            final_compacted_regexes.append(intermediate_compacted_regexes.pop())
        else:
            intermediate_compacted_regexes.pop(0)
            intermediate_compacted_regexes[-1] = f"{shortest}|{longest}"

    return final_compacted_regexes


def create_waf_regex_groupings(
    patterns: List[str],
    compactible_affixes: Tuple[Tuple[str, str], ...] = COMPACTIBLE_AFFIXES,
    max_length: int = 200,
    max_group_size: int = 10,
) -> List[Tuple[str, ...]]:
    """
    Create WAF regex pattern groups that respect both max_length and max_group_size constraints.
    Returns list of tuples, each containing up to max_group_size patterns.
    """
    regexes = compact_waf_regexes(patterns, compactible_affixes, max_length)
    groups = []
    iterator = iter(regexes)
    while batch := tuple(islice(iterator, max_group_size)):
        groups.append(batch)
    return groups
