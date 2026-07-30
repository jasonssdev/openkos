"""OpenKOS's derived-layer entity-resolution package (read-only, slice 1).

`resolution` surfaces CANDIDATE pairs/groups of same-type OKF objects that
MIGHT be the same real-world entity, via a deterministic, stdlib-only,
whole-bundle pass (`find_candidates`) -- never an LLM, never a merge or
mutation. Output is ephemeral: frozen dataclasses only, never a persisted
OKF type or `bundle`/`state` file, and this package never writes a byte of
the bundle.

Public surface of this package: `CandidateGroup`, `Tier`,
`find_candidates` (both tiers), and `find_exact_title_groups` (issue #216 --
the exact-title/`Tier.HIGH`-only pass, which skips `find_candidates`'s
O(n^2) pairwise near-match work; see `candidates.py` for the equivalence
contract and for why it is a separate name rather than a `tier=` filter).

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.resolution`. This package may
import `openkos.model.okf` read-only, never the reverse. As of MVP-2 slice
2b, `resolution.edge_typing` DOES import `openkos.graph` internally (a
derived-layer read, over `graph.sqlite_graph.build_graph`) -- derived ->
derived is allowed; only canonical -> `graph` and `cli` -> `graph` stay
forbidden (see `edge_typing.py`'s own docstring, and
`tests/unit/graph/test_analysis.py::test_cli_main_never_imports_graph_and_registers_no_graph_command`).
"""

from .candidates import (
    CandidateGroup,
    Tier,
    find_candidates,
    find_exact_title_groups,
)

__all__ = [
    "CandidateGroup",
    "Tier",
    "find_candidates",
    "find_exact_title_groups",
]
