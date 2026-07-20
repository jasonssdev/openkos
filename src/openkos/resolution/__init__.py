"""OpenKOS's derived-layer entity-resolution package (read-only, slice 1).

`resolution` surfaces CANDIDATE pairs/groups of same-type OKF objects that
MIGHT be the same real-world entity, via a deterministic, stdlib-only,
whole-bundle pass (`find_candidates`) -- never an LLM, never a merge or
mutation. Output is ephemeral: frozen dataclasses only, never a persisted
OKF type or `bundle`/`state` file, and this package never writes a byte of
the bundle.

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.resolution`. This package may
import `openkos.model.okf` read-only, never the reverse, and does not
import `openkos.graph` this slice.
"""

from .candidates import CandidateGroup, Tier, find_candidates

__all__ = ["CandidateGroup", "Tier", "find_candidates"]
