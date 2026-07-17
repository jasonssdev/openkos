"""Typer application object exposed as the `openkos` console script."""

import typer

app = typer.Typer()


@app.callback()
def callback() -> None:
    """openkos: local-first engine that compiles text into a portable knowledge base."""
