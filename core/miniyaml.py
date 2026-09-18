"""A deliberately small YAML subset parser.

Only what `config/*.yml` actually uses: nested mappings, block lists, inline flow
lists, scalars, quoted strings and comments. If PyYAML is installed,
`core.config.load_yaml` prefers it; this exists so the repo runs with a bare
Python 3.11+ and no install step.

Unsupported on purpose: anchors, multi-line scalars, flow mappings, multi-docs.
"""
from __future__ import annotations

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~", "none"}


class MiniYamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(raw: str):
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in _split_flow(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        out = {}
        for part in _split_flow(inner):
            kv = _split_key(part)
            if kv is None:
                raise MiniYamlError(f"expected 'key: value' in flow map at {part!r}")
            out[kv[0].strip("\"'")] = _scalar(kv[1])
        return out
    low = s.lower()
    if low in _NULL:
        return None if low != "" else None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_flow(inner: str) -> list[str]:
    parts, buf, quote, depth = [], [], None, 0
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _split_key(content: str) -> tuple[str, str] | None:  # noqa: F811
    """Split `key: value` outside quotes. Returns None if there is no key."""
    quote = None
    for i, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "[":
            return None
        elif ch == ":" and (i + 1 == len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1 :].strip()
    return None


def _flow_depth(s: str) -> int:
    depth, quote = 0, None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth


def _lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    pending = 0  # unclosed flow brackets carried from the previous line
    for n, raw in enumerate(text.splitlines(), 1):
        if pending == 0 and raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise MiniYamlError(f"line {n}: tabs are not valid indentation")
        if pending > 0:
            ind, prev = out[-1]
            out[-1] = (ind, prev + " " + line.strip())
            pending += _flow_depth(line)
            continue
        out.append((len(line) - len(line.lstrip()), line.strip()))
        pending = _flow_depth(line)
    if pending > 0:
        raise MiniYamlError("unclosed '[' in flow sequence")
    return out


def _parse(lines: list[tuple[int, str]], i: int, indent: int):
    if lines[i][1].startswith("- "):
        return _parse_list(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines: list[tuple[int, str]], i: int, indent: int):
    out: dict = {}
    while i < len(lines) and lines[i][0] >= indent:
        ind, content = lines[i]
        if ind > indent:
            raise MiniYamlError(f"unexpected indent at {content!r}")
        kv = _split_key(content)
        if kv is None:
            raise MiniYamlError(f"expected 'key: value' at {content!r}")
        key, value = kv
        if value:
            out[key] = _scalar(value)
            i += 1
        else:
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                out[key], i = _parse(lines, i + 1, lines[i + 1][0])
            elif i + 1 < len(lines) and lines[i + 1][1].startswith("- ") and lines[i + 1][0] == indent:
                out[key], i = _parse_list(lines, i + 1, indent)
            else:
                out[key] = None
                i += 1
    return out, i


def _parse_list(lines: list[tuple[int, str]], i: int, indent: int):
    out: list = []
    while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
        item = lines[i][1][2:].strip()
        if _split_key(item) is not None:
            sub = [(indent + 2, item)]
            j = i + 1
            while j < len(lines) and lines[j][0] > indent:
                sub.append(lines[j])
                j += 1
            value, _ = _parse_map(sub, 0, indent + 2)
            out.append(value)
            i = j
        else:
            out.append(_scalar(item))
            i += 1
    return out, i


def loads(text: str):
    lines = _lines(text)
    if not lines:
        return {}
    value, _ = _parse(lines, 0, lines[0][0])
    return value
