import re
from dataclasses import dataclass
from .model import Element

@dataclass
class Simple:
    tag: str = "*"
    ids: tuple = ()
    classes: tuple = ()
    attrs: tuple = ()
    pseudos: tuple = ()

def _balanced(text, start, opening="(", closing=")"):
    depth = 0; quote = None
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote: quote = None
        elif ch in "'\"": quote = ch
        elif ch == opening: depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0: return text[start + 1:i], i + 1
    raise SyntaxError("Invalid selector")

def _groups(text):
    result, start, depth, quote = [], 0, 0, None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote: quote = None
        elif ch in "'\"": quote = ch
        elif ch in "[(": depth += 1
        elif ch in "])": depth -= 1
        elif ch == "," and depth == 0:
            if not text[start:i].strip(): raise SyntaxError("Empty selector group")
            result.append(text[start:i].strip()); start = i + 1
        if depth < 0: raise SyntaxError("Unbalanced selector")
    if quote or depth or not text[start:].strip(): raise SyntaxError("Invalid selector")
    result.append(text[start:].strip()); return result

def _simple(text):
    if not text: raise SyntaxError("Expected compound selector")
    i = 0; tag = "*"; m = re.match(r"(?:[A-Za-z_][\w-]*|\*)", text)
    if m: tag, i = m.group().lower(), m.end()
    ids, classes, attrs, pseudos = [], [], [], []
    while i < len(text):
        kind = text[i]
        if kind in "#.":
            m = re.match(r"[.#]([\w-]+)", text[i:])
            if not m: raise SyntaxError("Invalid class or id selector")
            (ids if kind == "#" else classes).append(m.group(1)); i += m.end(); continue
        if kind == "[":
            value, i = _balanced(text, i, "[", "]")
            m = re.match(r"^\s*([\w:-]+)\s*(?:(\^=|\$=|\*=|~=|\|=|=)\s*(.*?)\s*)?$", value)
            if not m: raise SyntaxError("Invalid attribute selector")
            val = m.group(3)
            if val is not None and len(val) >= 2 and val[0] in "'\"" and val[-1] == val[0]: val = val[1:-1]
            attrs.append((m.group(1).lower(), m.group(2), val or "")); continue
        if kind == ":":
            m = re.match(r":([\w-]+)", text[i:])
            if not m: raise SyntaxError("Invalid pseudo-class")
            name, i = m.group(1).lower(), i + m.end(); arg = None
            if i < len(text) and text[i] == "(": arg, i = _balanced(text, i)
            if name not in {"first-child", "last-child", "nth-child", "first-of-type", "last-of-type", "nth-of-type", "not", "is", "where", "empty", "root"}: raise SyntaxError("Unsupported pseudo-class: " + name)
            pseudos.append((name, arg)); continue
        raise SyntaxError("Invalid selector token")
    return Simple(tag, tuple(ids), tuple(classes), tuple(attrs), tuple(pseudos))

def _parse_group(text):
    compounds, relations, buf, depth, quote, pending_space = [], [], "", 0, None, False
    for ch in text:
        if quote:
            buf += ch
            if ch == quote: quote = None
        elif ch in "'\"": quote, buf = ch, buf + ch
        elif ch in "[(": depth, buf = depth + 1, buf + ch
        elif ch in "])": depth, buf = depth - 1, buf + ch
        elif depth == 0 and ch.isspace():
            if buf.strip(): compounds.append(_simple(buf.strip())); buf = ""; pending_space = True
        elif depth == 0 and ch in ">+~":
            if buf.strip(): compounds.append(_simple(buf.strip())); buf = ""
            if not compounds: raise SyntaxError("Combinator without selector")
            relations.append(ch); pending_space = False
        else:
            if pending_space and compounds and len(relations) < len(compounds): relations.append(" "); pending_space = False
            buf += ch
    if quote or depth: raise SyntaxError("Invalid selector")
    if buf.strip(): compounds.append(_simple(buf.strip()))
    if not compounds or len(relations) != len(compounds) - 1: raise SyntaxError("Invalid combinator")
    return compounds, relations

def parse(selector):
    if not isinstance(selector, str) or not selector.strip(): raise SyntaxError("Invalid selector")
    return [_parse_group(group) for group in _groups(selector)]

def _siblings(node, of_type=False):
    values = [x for x in getattr(node.parentNode, "_children", ()) if isinstance(x, Element)] if node.parentNode else []
    return [x for x in values if not of_type or x.localName == node.localName]

def _nth(pos, expr):
    expr = (expr or "").strip().lower().replace(" ", "")
    if expr == "odd": return pos % 2 == 1
    if expr == "even": return pos % 2 == 0
    try: return pos == int(expr)
    except ValueError: pass
    m = re.match(r"([+-]?\d*)n([+-]\d+)?$", expr)
    if not m: raise SyntaxError("Invalid nth expression")
    a = -1 if m.group(1) == "-" else 1 if m.group(1) in ("", "+") else int(m.group(1)); b = int(m.group(2) or 0)
    return pos >= b if a == 0 else (pos - b) * a >= 0 and (pos - b) % a == 0

def _simple_match(node, simple):
    if not isinstance(node, Element) or (simple.tag != "*" and node.localName != simple.tag): return False
    if any(node.get_id() != value for value in simple.ids) or any(x not in set(node.get_className().split()) for x in simple.classes): return False
    for name, op, wanted in simple.attrs:
        actual = node.attributes.get(name)
        if actual is None: return False
        if op and not {"=": actual == wanted, "^=": actual.startswith(wanted), "$=": actual.endswith(wanted), "*=": wanted in actual, "~=": wanted in actual.split(), "|=": actual == wanted or actual.startswith(wanted + "-")}[op]: return False
    for name, arg in simple.pseudos:
        siblings = _siblings(node, name.endswith("of-type")); pos = siblings.index(node) + 1 if node in siblings else 0
        if name == "first-child" and pos != 1 or name == "last-child" and pos != len(siblings): return False
        if name in ("nth-child", "nth-of-type") and not _nth(pos, arg): return False
        if name in ("first-of-type", "last-of-type") and (pos != 1 if name.startswith("first") else pos != len(siblings)): return False
        if name == "empty" and getattr(node, "_children", ()): return False
        if name == "root" and getattr(node.parentNode, "nodeType", None) not in (None, 9): return False
        if name in ("not", "is", "where"):
            result = any(_matches_ast(node, group) for group in parse(arg or ""))
            if (name == "not" and result) or (name != "not" and not result): return False
    return True

def _matches_ast(node, ast):
    compounds, relations = ast; current = node
    if not _simple_match(current, compounds[-1]): return False
    for index in range(len(compounds) - 2, -1, -1):
        relation = relations[index]
        if relation == ">": current = current.parentNode
        elif relation == "+":
            values = list(getattr(current.parentNode, "_children", ())); current = values[values.index(current) - 1] if current in values and values.index(current) else None
        elif relation == "~":
            values = list(getattr(current.parentNode, "_children", ())); current = next((x for x in reversed(values[:values.index(current)]) if isinstance(x, Element) and _simple_match(x, compounds[index])), None)
            if current is not None: continue
        else:
            current = current.parentNode
            while current is not None and not _simple_match(current, compounds[index]): current = current.parentNode
        if current is None or not _simple_match(current, compounds[index]): return False
    return True

def matches(node, selector): return any(_matches_ast(node, group) for group in parse(selector))

def select(root, selector):
    ast = parse(selector); nodes = []
    if isinstance(root, Element) and any(_matches_ast(root, group) for group in ast): nodes.append(root)
    nodes.extend(n for n in root._page.document._walk(root) if isinstance(n, Element) and any(_matches_ast(n, group) for group in ast))
    return nodes
