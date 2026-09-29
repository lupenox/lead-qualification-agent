"""robots.txt evaluation following RFC 9309.

- The group whose ``User-agent`` matches our product token is used; otherwise ``*``.
- The longest matching ``Allow``/``Disallow`` rule wins; ``Allow`` wins ties.
- An empty ``Disallow:`` rule matches nothing (i.e. everything is allowed).
- ``*`` wildcards and a trailing ``$`` end anchor are supported.
"""
from __future__ import annotations

import re
from urllib.parse import unquote


def _product_token(user_agent: str) -> str:
    token = user_agent.strip().split("/", 1)[0].split(" ", 1)[0]
    return token.lower()


def _parse_groups(robots_txt: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    last_was_agent = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_agent = True
        elif field in {"allow", "disallow"}:
            last_was_agent = False
            if agents:
                rules.append((field, value))
        else:
            last_was_agent = False
    if agents:
        groups.append((agents, rules))
    return groups


def _rule_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(unquote(piece)) for piece in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


def robots_allows(robots_txt: str, user_agent: str, path: str) -> bool:
    """Return True when ``user_agent`` may fetch ``path`` (path + optional query)."""
    if not robots_txt.strip():
        return True
    token = _product_token(user_agent)
    groups = _parse_groups(robots_txt)

    specific = [rules for agents, rules in groups if any(a != "*" and a == token for a in agents)]
    selected = specific or [rules for agents, rules in groups if "*" in agents]
    if not selected:
        return True
    rules = [rule for group in selected for rule in group]

    target = unquote(path or "/")
    best_len = -1
    allowed = True
    for kind, pattern in rules:
        if not pattern:
            continue  # empty Disallow/Allow matches nothing
        if not _rule_regex(pattern).match(target):
            continue
        length = len(pattern)
        if length > best_len or (length == best_len and kind == "allow"):
            best_len = length
            allowed = kind == "allow"
    return allowed
