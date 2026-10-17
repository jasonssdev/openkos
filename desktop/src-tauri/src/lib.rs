//! The OpenKOS desktop shell.
//!
//! The shell is a thin supervisor (ADR-0055): it starts the engine as a sidecar
//! running `openkos serve`, hands it a per-launch token over stdin, reads the
//! port from the engine's single ready line (ADR-0058), restarts it within a
//! bounded budget when it crashes, and stops it when the shell quits. Every
//! behaviour of the product stays in the engine, behind its local API. The one
//! thing the shell keeps itself is the remote backend's API key, in the
//! operating system's keychain, handed to the engine as its environment
//! (ADR-0069).

pub mod apikey;
pub mod engine;
pub mod locator;
pub mod supervisor;

use std::sync::Arc;

use apikey::{ApiKeys, KeyCommandError};
use supervisor::{EngineState, Supervisor, SupervisorConfig};

/// The engine's current state: how the web view learns the API's base URL and
/// token. The token stays in memory; it never reaches a URL, argv or a file.
#[tauri::command]
fn engine_state(supervisor: tauri::State<'_, Supervisor>) -> EngineState {
    supervisor.state()
}

/// Keep `key` as the remote backend's API key and restart the engine with it.
/// Async so a keychain prompt never blocks the main thread.
#[tauri::command]
async fn set_api_key(
    key: String,
    keys: tauri::State<'_, Arc<ApiKeys>>,
    supervisor: tauri::State<'_, Supervisor>,
) -> Result<(), KeyCommandError> {
    apikey::set_and_restart(&keys, &key, &|| supervisor.restart())
}

/// Remove the remote backend's API key and restart the engine without it.
#[tauri::command]
async fn clear_api_key(
    keys: tauri::State<'_, Arc<ApiKeys>>,
    supervisor: tauri::State<'_, Supervisor>,
) -> Result<(), KeyCommandError> {
    apikey::clear_and_restart(&keys, &|| supervisor.restart())
}

/// Whether an API key is kept. The key itself never goes back to the web view.
#[tauri::command]
async fn api_key_status(keys: tauri::State<'_, Arc<ApiKeys>>) -> Result<bool, KeyCommandError> {
    keys.is_set().map_err(KeyCommandError::from)
}

/// Start the engine, then the shell; stop the engine when the shell exits.
pub fn run() {
    let keys = Arc::new(ApiKeys::new(apikey::platform_store()));
    let environ = Arc::clone(&keys);
    // Started before the window, so the engine is warming up while it opens.
    let supervisor = Supervisor::start_with_env(
        Box::new(|| locator::locate_from_process().map_err(|error| error.to_string())),
        Box::new(move || environ.engine_env()),
        SupervisorConfig::default(),
    );
    let app = tauri::Builder::default()
        .manage(supervisor)
        .manage(keys)
        .invoke_handler(tauri::generate_handler![
            engine_state,
            set_api_key,
            clear_api_key,
            api_key_status
        ])
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
