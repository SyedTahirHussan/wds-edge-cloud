"""The subset of SensorThings/OData query options that the fake server implements.

``$filter`` supports comparisons (eq, ne, gt, ge, lt, le) between a path and a
literal, combined with and/or/not and parentheses. Paths are ``/``-separated and may
cross single-valued navigation properties (``Datastream/@iot.id``,
``properties/role``). Literals: single-quoted strings, numbers, true/false/null and
unquoted ISO 8601 timestamps. That is everything the client in this package sends.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<lp>\()|(?P<rp>\))"
    r"|(?P<dt>\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})?)"
    r"|(?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"|(?P<word>[A-Za-z@_][\w@./:-]*))"
)
OPS = {"eq", "ne", "gt", "ge", "lt", "le"}

Resolver = Callable[[dict[str, Any], list[str]], Any]


def parse_time(v: Any) -> Any:
    if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[10] == "T":
        try:
            return datetime.fromisoformat(v.split("/")[0].replace("Z", "+00:00"))
        except ValueError:
            return v
    return v


def tokenize(s: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(s):
        if s[pos:].strip() == "":
            break
        m = TOKEN.match(s, pos)
        if m is None:
            raise ValueError(f"cannot parse $filter near {s[pos:]!r}")
        kind = m.lastgroup
        assert kind is not None
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


@dataclass
class Cmp:
    path: list[str]
    op: str
    value: Any


@dataclass
class BoolOp:
    op: str
    args: list[Any]


class Parser:
    def __init__(self, s: str) -> None:
        self.toks = tokenize(s)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def parse(self) -> Any:
        node = self.expr()
        if self.peek() is not None:
            raise ValueError("trailing tokens in $filter")
        return node

    def expr(self) -> Any:
        node = self.conj()
        while (t := self.peek()) and t == ("word", "or"):
            self.take()
            node = BoolOp("or", [node, self.conj()])
        return node

    def conj(self) -> Any:
        node = self.unary()
        while (t := self.peek()) and t == ("word", "and"):
            self.take()
            node = BoolOp("and", [node, self.unary()])
        return node

    def unary(self) -> Any:
        t = self.peek()
        if t == ("word", "not"):
            self.take()
            return BoolOp("not", [self.unary()])
        if t is not None and t[0] == "lp":
            self.take()
            node = self.expr()
            if self.take()[0] != "rp":
                raise ValueError("missing ')' in $filter")
            return node
        return self.comparison()

    def comparison(self) -> Cmp:
        kind, path = self.take()
        if kind != "word":
            raise ValueError("$filter comparison must start with a property path")
        op = self.take()[1]
        if op not in OPS:
            raise ValueError(f"unsupported operator {op!r}")
        return Cmp(path.split("/"), op, self.literal())

    def literal(self) -> Any:
        kind, text = self.take()
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text) if any(c in text for c in ".eE") else int(text)
        if kind == "dt":
            return parse_time(text)
        if kind == "word" and text in ("true", "false"):
            return text == "true"
        if kind == "word" and text == "null":
            return None
        raise ValueError(f"unsupported literal {text!r}")


def compare(a: Any, op: str, b: Any) -> bool:
    if isinstance(b, datetime):
        a = parse_time(a)
    if op == "eq":
        return bool(a == b)
    if op == "ne":
        return bool(a != b)
    if a is None or b is None:
        return False
    try:
        return {"gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]
    except TypeError:
        return False


def evaluate(node: Any, entity: dict[str, Any], resolve: Resolver) -> bool:
    if isinstance(node, Cmp):
        return compare(resolve(entity, node.path), node.op, node.value)
    if node.op == "not":
        return not evaluate(node.args[0], entity, resolve)
    if node.op == "and":
        return all(evaluate(a, entity, resolve) for a in node.args)
    return any(evaluate(a, entity, resolve) for a in node.args)


def split_top_level(s: str, sep: str) -> list[str]:
    """Split on ``sep`` outside parentheses and quotes."""
    parts, depth, cur, quoted = [], 0, "", False
    for ch in s:
        if ch == "'":
            quoted = not quoted
        if not quoted:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append(cur)
                cur = ""
                continue
        cur += ch
    if cur:
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def parse_expand(s: str) -> dict[str, dict[str, str]]:
    """``Datastreams($select=name;$top=2),Locations`` -> {nav: {option: value}}."""
    out: dict[str, dict[str, str]] = {}
    for part in split_top_level(s, ","):
        if "(" in part:
            name, rest = part.split("(", 1)
            opts = {}
            for o in split_top_level(rest[:-1], ";"):
                k, v = o.split("=", 1)
                opts[k.strip()] = v.strip()
            out[name.strip()] = opts
        else:
            out[part] = {}
    return out


def order_key(orderby: str) -> list[tuple[list[str], bool]]:
    keys = []
    for part in split_top_level(orderby, ","):
        bits = part.split()
        keys.append((bits[0].split("/"), len(bits) > 1 and bits[1].lower() == "desc"))
    return keys
