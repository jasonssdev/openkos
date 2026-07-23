"""`openkos.vcs`: adapters over version-control tools.

Currently one adapter, `openkos.vcs.git`, which owns the ONLY `subprocess`
call site in `openkos` (see its module docstring). Shared here rather than
under `purge/` because `doctor` also needs the availability probes.
"""
