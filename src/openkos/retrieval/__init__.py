"""Derived retrieval layer: answers questions from the compiled bundle.

`answer.py` composes the canonical `state.fts` lexical index with an
injected `llm.LLMBackend` to produce a cited answer. This package has no CLI
surface and no `openkos.config` dependency -- the caller supplies the bundle
directory and the backend.
"""
