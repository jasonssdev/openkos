//! The OpenKOS desktop shell.
//!
//! The shell is a thin supervisor (ADR-0055): it starts the engine as a sidecar
//! running `openkos serve`, hands it a per-launch token over stdin, reads the
//! port from the engine's single ready line (ADR-0058), restarts it within a
//! bounded budget when it crashes, and stops it when the shell quits. Every
//! behaviour of the product stays in the engine, behind its local API.

pub mod engine;
pub mod locator;
pub mod supervisor;
