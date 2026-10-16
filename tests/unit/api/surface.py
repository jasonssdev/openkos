"""The public-surface snapshot of `openkos.api`: generator, leak check and
compatibility classifier.

`python -m tests.unit.api.surface --write` regenerates
`tests/unit/api/api_surface.json` and prints the classified diff against the
file it replaces. The same module backs `test_api_surface.py`.

Annotations are evaluated (`annotationlib.get_annotations`, `Format.VALUE`) and
rendered canonically: a class exported in `__all__` renders as its exported
name, any other class as `module.qualname`. Source spelling therefore never
moves the snapshot, and an internal type shows up with its module path, which
is what the leak check looks for.
"""

import annotationlib
import ast
import dataclasses
import enum
import inspect
import json
import re
import shutil
import subprocess
import sys
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openkos.api

SNAPSHOT_PATH = Path(__file__).with_name("api_surface.json")
SNAPSHOT_RELPATH = "tests/unit/api/api_surface.json"

_LEAK = re.compile(r"\b(?:openkos|sqlite3)\.[\w.]+")
_SHORT_MODULES = {"builtins", "collections.abc", "typing"}


@dataclass(frozen=True)
class Change:
    """One difference between two snapshots."""

    severity: str  # "additive" | "breaking"
    name: str
    detail: str

    def __str__(self) -> str:
        return f"{self.severity:9} {self.name}: {self.detail}"


# -- rendering ---------------------------------------------------------------- #


def _qualified(cls: type) -> str:
    if cls.__module__ in _SHORT_MODULES:
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def render(annotation: object, exported: dict[int, str]) -> str:
    """The canonical text of an evaluated annotation."""
    if annotation is None or annotation is type(None):
        return "None"
    if annotation is Ellipsis:
        return "..."
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, list):
        return "[" + ", ".join(render(a, exported) for a in annotation) + "]"
    if isinstance(annotation, type) and id(annotation) in exported:
        return exported[id(annotation)]
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal:
        return "Literal[" + ", ".join(repr(a) for a in args) + "]"
    if origin is typing.Union or origin is types.UnionType:
        return " | ".join(render(a, exported) for a in args)
    if origin is not None:
        base = render(origin, exported) if isinstance(origin, type) else str(origin)
        if not args:
            return base
        return f"{base}[" + ", ".join(render(a, exported) for a in args) + "]"
    if isinstance(annotation, type):
        return _qualified(annotation)
    return repr(annotation)


def _hints(obj: object) -> dict[str, object]:
    """Evaluated annotations of `obj`; a stringized one is resolved against its
    module (modules with `from __future__ import annotations`)."""
    return annotationlib.get_annotations(
        obj, format=annotationlib.Format.VALUE, eval_str=True
    )


def _default_text(default: object) -> str | None:
    if default is inspect.Parameter.empty:
        return None
    if default is None or isinstance(default, bool | int | float | str):
        return repr(default)
    return f"<{type(default).__name__}>"


def _signature(
    fn: Any, exported: dict[int, str], *, skip_first: bool = False
) -> dict[str, object]:
    sig = inspect.signature(fn)
    hints = _hints(fn)
    params = list(sig.parameters.values())
    if skip_first:
        params = params[1:]
    return {
        "parameters": [
            {
                "name": p.name,
                "kind": p.kind.name,
                "annotation": render(hints[p.name], exported)
                if p.name in hints
                else None,
                "default": _default_text(p.default),
            }
            for p in params
        ],
        "returns": render(hints["return"], exported) if "return" in hints else None,
    }


def _members(cls: type, exported: dict[int, str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, member in vars(cls).items():
        if name.startswith("_"):
            continue
        if isinstance(member, property) and member.fget is not None:
            hints = _hints(member.fget)
            out[name] = {
                "kind": "property",
                "returns": render(hints["return"], exported)
                if "return" in hints
                else None,
            }
        elif isinstance(member, classmethod):
            out[name] = {
                "kind": "classmethod",
                **_signature(member.__func__, exported, skip_first=True),
            }
        elif isinstance(member, staticmethod):
            out[name] = {
                "kind": "staticmethod",
                **_signature(member.__func__, exported),
            }
        elif inspect.isfunction(member):
            out[name] = {
                "kind": "method",
                **_signature(member, exported, skip_first=True),
            }
    return out


def _entry(name: str, obj: object, exported: dict[int, str]) -> dict[str, object]:
    if inspect.isclass(obj):
        bases = [render(b, exported) for b in obj.__bases__]
        if issubclass(obj, enum.Enum):
            return {
                "kind": "enum",
                "bases": bases,
                "members": {m.name: m.value for m in obj},
            }
        if issubclass(obj, BaseException):
            hints = _hints(obj)
            return {
                "kind": "exception",
                "bases": bases,
                "fields": {n: render(h, exported) for n, h in hints.items()},
                "members": _members(obj, exported),
            }
        if dataclasses.is_dataclass(obj):
            hints = _hints(obj)
            params = obj.__dataclass_params__  # type: ignore[attr-defined]
            return {
                "kind": "dataclass",
                "frozen": bool(params.frozen),
                "fields": [
                    {
                        "name": f.name,
                        "annotation": render(hints.get(f.name, f.type), exported),
                        "default": f.default is not dataclasses.MISSING
                        or f.default_factory is not dataclasses.MISSING,
                    }
                    for f in dataclasses.fields(obj)
                    if not f.name.startswith("_")
                ],
                "members": _members(obj, exported),
            }
        return {
            "kind": "protocol" if getattr(obj, "_is_protocol", False) else "class",
            "bases": bases,
            "members": _members(obj, exported),
        }
    if inspect.isfunction(obj):
        return {"kind": "function", **_signature(obj, exported)}
    if typing.get_origin(obj) is not None:
        return {"kind": "alias", "value": render(obj, exported)}
    return {"kind": "constant", "type": type(obj).__name__}


def live_surface() -> dict[str, Any]:
    """The surface of `openkos.api` as a JSON-ready mapping."""
    exported = {
        id(obj): name
        for name in openkos.api.__all__
        if inspect.isclass(obj := getattr(openkos.api, name))
    }
    names = {
        name: _entry(name, getattr(openkos.api, name), exported)
        for name in sorted(openkos.api.__all__)
    }
    return {"api_version": openkos.api.API_VERSION, "names": names}


def dumps(surface: dict[str, Any]) -> str:
    return json.dumps(surface, indent=2, sort_keys=True) + "\n"


# -- leak check ----------------------------------------------------------------- #


def _strings(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [pair for k, v in value.items() for pair in _strings(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [
            pair for i, v in enumerate(value) for pair in _strings(v, f"{path}[{i}]")
        ]
    return []


def leaks(surface: dict[str, Any]) -> list[str]:
    """Every rendered `openkos.*` or `sqlite3.*` name: an unexported engine
    type, a `state` type or the database module reaching a public symbol."""
    found: list[str] = []
    for name, entry in surface["names"].items():
        for path, text in _strings(entry, name):
            if path.endswith(".name") or path.endswith(".kind"):
                continue
            for match in _LEAK.findall(text):
                found.append(f"{path}: {match}")
    return found


# -- classifier ------------------------------------------------------------------ #


def _split_union(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for index, char in enumerate(text):
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        if char == "|" and depth == 0 and text[index - 1 : index + 2] == " | ":
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return parts


def _literal_values(member: str) -> set[object] | None:
    if not (member.startswith("Literal[") and member.endswith("]")):
        return None
    body = ast.literal_eval("(" + member[len("Literal[") : -1] + ",)")
    return set(body)


def is_literal_widening(before: str | None, after: str | None) -> bool:
    """`after` only adds `Literal` values to `before`; everything else equal."""
    if before is None or after is None or before == after:
        return False
    old, new = _split_union(before), _split_union(after)
    old_other = [m for m in old if _literal_values(m) is None]
    new_other = [m for m in new if _literal_values(m) is None]
    if old_other != new_other:
        return False
    old_values = set().union(*(_literal_values(m) or set() for m in old))
    new_values = set().union(*(_literal_values(m) or set() for m in new))
    return bool(old_values) and old_values < new_values


def _annotation_change(
    name: str, what: str, before: str | None, after: str | None, out: list[Change]
) -> None:
    if before == after:
        return
    if is_literal_widening(before, after):
        out.append(Change("additive", name, f"{what} Literal widened"))
    else:
        out.append(Change("breaking", name, f"{what} {before} -> {after}"))


def _classify_params(
    name: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    out: list[Change],
) -> None:
    old = {p["name"]: p for p in before}
    new = {p["name"]: p for p in after}
    for pname in old:
        if pname not in new:
            out.append(Change("breaking", name, f"parameter {pname} removed"))
    common = [p["name"] for p in before if p["name"] in new]
    if common != [p["name"] for p in after if p["name"] in old]:
        out.append(Change("breaking", name, "parameters reordered"))
    for pname in common:
        o, n = old[pname], new[pname]
        if o["kind"] != n["kind"]:
            out.append(
                Change(
                    "breaking",
                    name,
                    f"parameter {pname} kind {o['kind']} -> {n['kind']}",
                )
            )
        if o["annotation"] != n["annotation"]:
            out.append(
                Change(
                    "breaking",
                    name,
                    f"parameter {pname} annotation {o['annotation']} -> {n['annotation']}",
                )
            )
        if o["default"] is not None and n["default"] is None:
            out.append(Change("breaking", name, f"parameter {pname} default removed"))
        elif o["default"] is None and n["default"] is not None:
            out.append(Change("additive", name, f"parameter {pname} became optional"))
        elif o["default"] != n["default"]:
            out.append(
                Change(
                    "breaking",
                    name,
                    f"parameter {pname} default {o['default']} -> {n['default']}",
                )
            )
    old_positional = [p["name"] for p in before if p["kind"] in _POSITIONAL]
    for index, p in enumerate(after):
        if p["name"] in old:
            continue
        if p["kind"] in {"VAR_POSITIONAL", "VAR_KEYWORD"}:
            out.append(Change("additive", name, f"parameter {p['name']} added"))
        elif p["default"] is None:
            out.append(
                Change("breaking", name, f"required parameter {p['name']} added")
            )
        elif p["kind"] in _POSITIONAL and any(
            q["name"] in old_positional for q in after[index + 1 :]
        ):
            out.append(
                Change(
                    "breaking",
                    name,
                    f"optional parameter {p['name']} added before others",
                )
            )
        else:
            out.append(
                Change("additive", name, f"optional parameter {p['name']} added")
            )


_POSITIONAL = {"POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD"}


def _classify_callable(
    name: str, before: dict[str, Any], after: dict[str, Any], out: list[Change]
) -> None:
    _classify_params(name, before["parameters"], after["parameters"], out)
    _annotation_change(name, "return", before["returns"], after["returns"], out)


def _classify_members(
    name: str, before: dict[str, Any], after: dict[str, Any], out: list[Change]
) -> None:
    for member in before:
        if member not in after:
            out.append(Change("breaking", f"{name}.{member}", "member removed"))
    for member, entry in after.items():
        if member not in before:
            out.append(Change("additive", f"{name}.{member}", "member added"))
            continue
        old = before[member]
        if old["kind"] != entry["kind"]:
            out.append(
                Change(
                    "breaking",
                    f"{name}.{member}",
                    f"kind {old['kind']} -> {entry['kind']}",
                )
            )
        elif "parameters" in entry:
            _classify_callable(f"{name}.{member}", old, entry, out)
        else:
            _annotation_change(
                f"{name}.{member}", "return", old["returns"], entry["returns"], out
            )


def _classify_fields_list(
    name: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    out: list[Change],
) -> None:
    old = {f["name"]: f for f in before}
    new = {f["name"]: f for f in after}
    for fname in old:
        if fname not in new:
            out.append(Change("breaking", name, f"field {fname} removed"))
    if [f["name"] for f in before if f["name"] in new] != [
        f["name"] for f in after if f["name"] in old
    ]:
        out.append(Change("breaking", name, "fields reordered"))
    for fname, n in new.items():
        if fname not in old:
            severity = "additive" if n["default"] else "breaking"
            detail = (
                f"field {fname} added"
                if n["default"]
                else f"field {fname} added without a default"
            )
            out.append(Change(severity, name, detail))
            continue
        o = old[fname]
        _annotation_change(
            name, f"field {fname}", o["annotation"], n["annotation"], out
        )
        if o["default"] and not n["default"]:
            out.append(Change("breaking", name, f"field {fname} default removed"))
        elif not o["default"] and n["default"]:
            out.append(Change("additive", name, f"field {fname} gained a default"))


def _classify_entry(
    name: str, before: dict[str, Any], after: dict[str, Any], out: list[Change]
) -> None:
    if before["kind"] != after["kind"]:
        out.append(
            Change("breaking", name, f"kind {before['kind']} -> {after['kind']}")
        )
        return
    kind = before["kind"]
    if kind == "function":
        _classify_callable(name, before, after, out)
    elif kind == "dataclass":
        if before["frozen"] != after["frozen"]:
            out.append(Change("breaking", name, "frozen changed"))
        _classify_fields_list(name, before["fields"], after["fields"], out)
        _classify_members(name, before["members"], after["members"], out)
    elif kind in {"exception", "class", "protocol", "enum"}:
        _classify_bases(name, before["bases"], after["bases"], out)
        if kind == "exception":
            _classify_exception_fields(name, before["fields"], after["fields"], out)
        if kind == "enum":
            _classify_enum(name, before["members"], after["members"], out)
        else:
            _classify_members(name, before["members"], after["members"], out)
    elif kind == "alias":
        _annotation_change(name, "alias", before["value"], after["value"], out)
    elif kind == "constant" and before["type"] != after["type"]:
        out.append(
            Change("breaking", name, f"type {before['type']} -> {after['type']}")
        )


def _classify_bases(
    name: str, before: list[str], after: list[str], out: list[Change]
) -> None:
    if before == after:
        return
    removed = [b for b in before if b not in after]
    if removed:
        out.append(Change("breaking", name, f"base removed: {', '.join(removed)}"))
        return
    kept = [b for b in after if b in before]
    if kept != before:
        out.append(Change("breaking", name, "bases reordered"))
        return
    out.append(
        Change(
            "additive",
            name,
            f"base added: {', '.join(b for b in after if b not in before)}",
        )
    )


def _classify_exception_fields(
    name: str, before: dict[str, str], after: dict[str, str], out: list[Change]
) -> None:
    for fname in before:
        if fname not in after:
            out.append(Change("breaking", name, f"attribute {fname} removed"))
    for fname, annotation in after.items():
        if fname not in before:
            out.append(Change("additive", name, f"attribute {fname} added"))
        else:
            _annotation_change(
                name, f"attribute {fname}", before[fname], annotation, out
            )


def _classify_enum(
    name: str, before: dict[str, Any], after: dict[str, Any], out: list[Change]
) -> None:
    for member, value in before.items():
        if member not in after:
            out.append(Change("breaking", name, f"member {member} removed"))
        elif after[member] != value:
            out.append(Change("breaking", name, f"member {member} value changed"))
    for member in after:
        if member not in before:
            out.append(Change("additive", name, f"member {member} added"))


def classify(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """Every difference between two snapshots, each classified. Anything the
    compatibility policy does not name as additive is breaking."""
    out: list[Change] = []
    old, new = before["names"], after["names"]
    for name in sorted(old):
        if name not in new:
            out.append(Change("breaking", name, "name removed"))
    for name in sorted(new):
        if name not in old:
            out.append(Change("additive", name, "name added"))
        else:
            _classify_entry(name, old[name], new[name], out)
    return out


def breaking(changes: list[Change]) -> list[Change]:
    return [c for c in changes if c.severity == "breaking"]


# -- the base branch ------------------------------------------------------------- #


class BaseUnavailable(Exception):
    """The snapshot on the base branch cannot be read: git is missing, the base
    ref is not reachable, or it shares no history with `HEAD`."""


def base_snapshot(repo: Path, base_ref: str) -> dict[str, Any] | None:
    """The snapshot recorded at the merge base of `HEAD` and `base_ref`.

    `None` when that commit has no snapshot yet. Comparing against the merge
    base rather than the tip means a base that moved on after the branch forked
    is not blamed on the branch.
    """
    git = shutil.which("git")
    if git is None:
        raise BaseUnavailable("git is unavailable")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- a fixed git argv, no shell
            [git, *args], cwd=repo, capture_output=True, text=True, check=False
        )

    base = run("merge-base", "HEAD", base_ref)
    if base.returncode != 0 or not base.stdout.strip():
        raise BaseUnavailable(
            f"no merge base between HEAD and {base_ref}: {base.stderr.strip()}"
        )
    shown = run("show", f"{base.stdout.strip()}:{SNAPSHOT_RELPATH}")
    if shown.returncode != 0:
        return None
    result: dict[str, Any] = json.loads(shown.stdout)
    return result


def unbumped_breaking(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[Change]:
    """The breaking changes from `previous` to `current` that `API_VERSION` does
    not answer; empty when the version rose or nothing broke."""
    if current["api_version"] > previous["api_version"]:
        return []
    return breaking(classify(previous, current))


# -- regeneration ---------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    live = live_surface()
    committed: dict[str, Any] | None = (
        json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if SNAPSHOT_PATH.exists()
        else None
    )
    if committed is not None:
        changes = classify(committed, live)
        for change in changes:
            print(change)
        if not changes:
            print("no change to the public surface")
        if breaking(changes) and live["api_version"] <= committed["api_version"]:
            print(
                "BREAKING without a bump: raise API_VERSION above "
                f"{committed['api_version']} in openkos/api/__init__.py"
            )
    if "--write" in argv:
        SNAPSHOT_PATH.write_text(dumps(live), encoding="utf-8")
        print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
