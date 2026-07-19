"""Derived extraction layer: classifies at most one derived object from a
source's text.

`concept.py` prompts an injected `llm.LLMBackend` to classify a source as a
`Concept` or `Entity`, then parses and validates its reply fail-closed. This
package has no CLI surface and no `openkos.config` dependency -- the caller
supplies the source text and the backend.
"""
