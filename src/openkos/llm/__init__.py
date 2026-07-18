"""LLM backend seam (architecture.md:46,:109; tech_stack.md:96).

This package is the canonical `LLMBackend` boundary: a pure library, no CLI
command and no workspace effect. `base.py` defines the `LLMBackend` Protocol
and `Message` TypedDict; `ollama.py` implements a concrete client over a
locally running Ollama server. Leaf-module discipline (mirrors `fsio`):
nothing under `openkos.llm` imports `openkos.config` -- the caller resolves
the model tag and host and passes them in as arguments.
"""
