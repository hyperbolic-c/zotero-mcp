"""Shared helpers for server_tools modules."""

from __future__ import annotations


_FIELD_ALIASES: dict[str, str] = {
    "itemtype": "itemType",
    "dateadded": "dateAdded",
    "datemodified": "dateModified",
    "doi": "DOI",
}


def extract_field_values(data: dict, field: str) -> list[str]:
    """Extract one or more comparable string values for *field* from an item data dict."""
    field_lower = field.lower()

    if field_lower in {"author", "authors", "creator", "creators"}:
        creators = data.get("creators", []) or []
        values: list[str] = []
        for creator in creators:
            if not isinstance(creator, dict):
                continue
            if creator.get("firstName") or creator.get("lastName"):
                full_name = " ".join(
                    [
                        str(creator.get("firstName", "")).strip(),
                        str(creator.get("lastName", "")).strip(),
                    ]
                ).strip()
                if full_name:
                    values.append(full_name)
            if creator.get("name"):
                values.append(str(creator.get("name", "")).strip())
        return values

    if field_lower in {"tag", "tags"}:
        tags = data.get("tags", []) or []
        return [
            str(tag.get("tag", "")).strip()
            for tag in tags
            if isinstance(tag, dict) and tag.get("tag")
        ]

    if field_lower == "year":
        date_value = str(data.get("date", "")).strip()
        return [date_value[:4]] if len(date_value) >= 4 else []

    source_field = _FIELD_ALIASES.get(field_lower, field)
    raw_value = data.get(source_field, "")
    if raw_value is None:
        return []
    return [str(raw_value).strip()]


def _as_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def compare_field_value(candidate: str, expected: str, operation: str) -> bool:
    """Return True if *candidate* satisfies *operation* against *expected*."""
    left = candidate.lower()
    right = expected.lower()

    if operation == "is":
        return left == right
    if operation == "isNot":
        return left != right
    if operation == "contains":
        return right in left
    if operation == "doesNotContain":
        return right not in left
    if operation == "beginsWith":
        return left.startswith(right)
    if operation == "endsWith":
        return left.endswith(right)

    left_num = _as_float(left)
    right_num = _as_float(right)
    if (
        operation in {"isGreaterThan", "isLessThan", "isBefore", "isAfter"}
        and left_num is not None
        and right_num is not None
    ):
        if operation in {"isGreaterThan", "isAfter"}:
            return left_num > right_num
        return left_num < right_num

    if operation in {"isGreaterThan", "isAfter"}:
        return left > right
    return left < right


def matches_condition(data: dict, condition: dict[str, str]) -> bool:
    """Return True if item *data* satisfies a single search *condition*."""
    values = extract_field_values(data, condition["field"])
    if not values:
        return False

    operation = condition["operation"]
    target = condition["value"]
    comparisons = [compare_field_value(v, target, operation) for v in values]

    if operation in {"isNot", "doesNotContain"}:
        return all(comparisons)
    return any(comparisons)
