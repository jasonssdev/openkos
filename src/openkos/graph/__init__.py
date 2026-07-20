"""OpenKOS's derived-layer graph projection package.

`graph` projects the canonical OKF bundle into a queryable node-edge
representation over the bundle's existing markdown links -- `base.py`'s
`GraphStore` Protocol and `Edge`, built and converted to NetworkX by later
modules in this package. It is derived, read-only, and fully reconstructible
from canonical markdown -- never a source of truth and never a mutator of
bundle bytes.

Layering boundary: the canonical layer (`openkos.model`, `openkos.bundle`,
`openkos.state`) MUST NOT import `openkos.graph`. This package may import
from the canonical layer (read-only), never the reverse.
"""
