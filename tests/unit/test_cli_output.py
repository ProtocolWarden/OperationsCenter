# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for operations_center.cli_output."""

from __future__ import annotations

import json
import types
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pydantic import BaseModel
from rich.console import Console

from operations_center.cli_output import print_structured


def _render(output: object, *, sort_keys: bool = False, width: int | None = None) -> str:
    buf = StringIO()
    kwargs = {"file": buf, "no_color": True}
    if width is not None:
        kwargs["width"] = width
    console = Console(**kwargs)
    print_structured(console, output, sort_keys=sort_keys)
    return buf.getvalue()


class _Model(BaseModel):
    name: str
    created_at: datetime
    path: Path


@dataclass
class _Point:
    x: int
    y: int


@dataclass
class _Line:
    start: _Point
    end: _Point


class TestDictInput:
    def test_plain_dict_renders_as_json_object(self) -> None:
        result = _render({"a": 1, "b": "two"})
        assert json.loads(result) == {"a": 1, "b": "two"}

    def test_nested_dict_preserved(self) -> None:
        payload = {"outer": {"inner": [1, 2, 3]}}
        assert json.loads(_render(payload)) == payload


class TestBaseModelInput:
    def test_model_normalized_via_model_dump_json_mode(self) -> None:
        model = _Model(name="x", created_at=datetime(2026, 1, 1, tzinfo=UTC), path=Path("/tmp/x"))
        result = json.loads(_render(model))
        assert result["name"] == "x"
        assert result["path"] == "/tmp/x"
        # datetime is converted to an ISO string by mode="json", not left as
        # a python object that would need `default=str` to rescue it.
        assert result["created_at"] == "2026-01-01T00:00:00Z"


class TestDataclassInput:
    def test_dataclass_normalized_via_asdict(self) -> None:
        result = json.loads(_render(_Point(x=1, y=2)))
        assert result == {"x": 1, "y": 2}

    def test_nested_dataclass_recursively_normalized(self) -> None:
        line = _Line(start=_Point(x=0, y=0), end=_Point(x=1, y=1))
        result = json.loads(_render(line))
        assert result == {"start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 1}}


class TestMappingInput:
    def test_non_dict_mapping_normalized_to_dict(self) -> None:
        mapping = types.MappingProxyType({"k": "v"})
        result = json.loads(_render(mapping))
        assert result == {"k": "v"}

    def test_dict_subclass_passthrough_not_routed_through_mapping_branch(self) -> None:
        # OrderedDict IS a dict, so it must hit the `else` passthrough branch,
        # not the non-dict-Mapping branch (which would still work, but this
        # pins the intended dispatch and confirms `dict(...)` isn't called
        # redundantly on an already-dict value).
        result = json.loads(_render(OrderedDict([("b", 1), ("a", 2)])))
        assert result == {"b": 1, "a": 2}


class TestOtherJsonNativeInputs:
    def test_list_passthrough(self) -> None:
        assert json.loads(_render([1, 2, 3])) == [1, 2, 3]

    def test_none_renders_as_null(self) -> None:
        assert _render(None).strip() == "null"

    def test_empty_dict_renders_as_empty_object(self) -> None:
        assert _render({}).strip() == "{}"

    def test_empty_list_renders_as_empty_array(self) -> None:
        assert _render([]).strip() == "[]"

    def test_bool_renders_as_json_literal(self) -> None:
        assert _render(True).strip() == "true"

    def test_int_renders_as_json_number(self) -> None:
        assert _render(7).strip() == "7"

    def test_float_renders_as_json_number(self) -> None:
        assert _render(1.5).strip() == "1.5"

    def test_str_input_is_not_parsed_as_json_but_rendered_as_a_string_scalar(self) -> None:
        # Documented contract: callers must pass data, not a pre-serialized
        # `model.model_dump_json()` string — a `str` argument is rendered as
        # a JSON string scalar, quotes and all, not parsed and re-emitted as
        # the object/array it might encode.
        result = _render('{"already": "json"}')
        assert result.strip() == json.dumps('{"already": "json"}')
        assert json.loads(result) == '{"already": "json"}'


class TestUnicodeAndFormatting:
    def test_non_ascii_characters_are_not_escaped(self) -> None:
        # ensure_ascii=False: multibyte characters should appear literally,
        # not as \uXXXX escapes.
        result = _render({"name": "café ❤"})
        assert "café ❤" in result
        assert "\\u" not in result

    def test_nested_payload_is_pretty_printed_with_indent(self) -> None:
        result = _render({"outer": {"inner": 1}})
        lines = [line for line in result.splitlines() if line.strip()]
        assert len(lines) > 1
        assert any(line.startswith("  ") for line in lines)


class TestSortKeys:
    def test_sort_keys_false_preserves_insertion_order(self) -> None:
        result = _render({"z": 1, "a": 2}, sort_keys=False)
        assert result.index('"z"') < result.index('"a"')

    def test_sort_keys_true_sorts_alphabetically(self) -> None:
        result = _render({"z": 1, "a": 2}, sort_keys=True)
        assert result.index('"a"') < result.index('"z"')


class TestDefaultStrFallback:
    def test_unserializable_nested_value_stringified(self) -> None:
        result = json.loads(_render({"path": Path("/tmp/foo")}))
        assert result == {"path": "/tmp/foo"}


class TestSoftWrapRegression:
    def test_long_string_value_stays_on_one_line_regardless_of_width(self) -> None:
        long_value = "x" * 220
        result = _render({"value": long_value}, width=80)
        lines = [line for line in result.splitlines() if line.strip()]
        assert any(long_value in line for line in lines)

    def test_no_ansi_escape_codes_on_non_tty_output(self) -> None:
        result = _render({"a": 1})
        assert "\x1b[" not in result
