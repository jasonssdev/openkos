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

use supervisor::{EngineState, Supervisor, SupervisorConfig};

/// The engine's current state: how the web view learns the API's base URL and
/// token. The token stays in memory; it never reaches a URL, argv or a file.
#[tauri::command]
fn engine_state(supervisor: tauri::State<'_, Supervisor>) -> EngineState {
    supervisor.state()
}

/// Start the engine, then the shell; stop the engine when the shell exits.
pub fn run() {
    // Started before the window, so the engine is warming up while it opens.
    let supervisor = Supervisor::start(
        Box::new(|| locator::locate_from_process().map_err(|error| error.to_string())),
        SupervisorConfig::default(),
    );
    let app = tauri::Builder::default()
        .manage(supervisor)
        .invoke_handler(tauri::generate_handler![engine_state])
        .build(tauri::generate_context!())
        .expect("the desktop shell could not be built");
    app.run(|handle, event| {
        // A graceful exit stops the engine itself; a force-quit is covered by
        // the engine's stdin lifeline, which closes with this process.
        if let tauri::RunEvent::Exit = event {
            tauri::Manager::state::<Supervisor>(handle).shutdown();
        }
    });
}
