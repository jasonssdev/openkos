//! The OpenKOS desktop shell.
//!
//! The shell is a thin supervisor (ADR-0055): it starts the engine as a sidecar
//! running `openkos serve`, hands it a per-launch token over stdin, reads the
//! port from the engine's single ready line (ADR-0058), restarts it within a
//! bounded budget when it crashes, and stops it when the shell quits. Every
//! behaviour of the product stays in the engine, behind its local API. The one
//! thing the shell keeps itself is the remote backend's API key, in the
//! operating system's keychain, handed to the engine as its environment
//! (ADR-0070), and the workspace the user chose, in its own configuration
//! directory. A workspace is created by the engine's own `init` (#1487).

pub mod apikey;
pub mod browser;
pub mod dropped;
pub mod engine;
pub mod locator;
pub mod supervisor;
pub mod workspace;

use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use apikey::{ApiKeys, KeyCommandError};
use supervisor::{EngineState, Supervisor, SupervisorConfig, Unlocated};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use workspace::{Choice, ChoiceStatus, WorkspaceCommandError, Workspaces};

/// How long the engine's `init` may take: a development engine under `uv run`
/// may sync its environment first, and `init` probes Ollama once.
pub const INIT_TIMEOUT: Duration = Duration::from_secs(180);

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

/// Which workspace is served and whether it is one: the variable's, the
/// remembered one, or none yet.
#[tauri::command]
fn workspace_choice_status(workspaces: tauri::State<'_, Arc<Workspaces>>) -> ChoiceStatus {
    workspaces.status(locator::workspace_from_process().as_deref())
}

/// Open the existing workspace at `path`, remember it and restart the engine
/// on it.
#[tauri::command]
async fn open_workspace(
    path: String,
    workspaces: tauri::State<'_, Arc<Workspaces>>,
    supervisor: tauri::State<'_, Supervisor>,
) -> Result<ChoiceStatus, WorkspaceCommandError> {
    workspaces.open(locator::workspace_from_process().as_deref(), &path, &|| {
        supervisor.restart()
    })
}

/// Create a workspace in the empty or missing folder `path` with the engine's
/// `init`, remember it and start the engine on it.
#[tauri::command]
async fn create_workspace(
    path: String,
    workspaces: tauri::State<'_, Arc<Workspaces>>,
    supervisor: tauri::State<'_, Supervisor>,
) -> Result<ChoiceStatus, WorkspaceCommandError> {
    workspaces.create(
        locator::workspace_from_process().as_deref(),
        &path,
        &run_engine_init,
        &|| supervisor.restart(),
    )
}

/// Ask the user for a folder with the system's own picker. `None` when they
/// cancel. Only the shell opens the dialog: the page has no dialog permission.
#[tauri::command]
async fn pick_workspace_folder(app: tauri::AppHandle) -> Option<String> {
    app.dialog()
        .file()
        .set_title("Choose a folder for your OpenKOS workspace")
        .set_can_create_directories(true)
        .blocking_pick_folder()
        .and_then(|picked| picked.into_path().ok())
        .map(|path| path.to_string_lossy().into_owned())
}

/// Ask the user for files with the system's own picker, several at once. `None`
/// when they cancel. The paths are absolute; only the shell opens the dialog.
#[tauri::command]
async fn pick_files(app: tauri::AppHandle) -> Option<Vec<String>> {
    app.dialog()
        .file()
        .set_title("Choose files to add to your library")
        .blocking_pick_files()
        .map(|picked| {
            picked
                .into_iter()
                .filter_map(|file| file.into_path().ok())
                .map(|path| path.to_string_lossy().into_owned())
                .collect()
        })
}

/// Open Ollama's official download page in the system browser. The page
/// cannot name another address: the shell opens only this fixed one.
#[tauri::command]
async fn open_runtime_download() -> Result<(), String> {
    browser::open_runtime_download()
}

/// Run the engine's `init` in `folder`, with no terminal, so it writes the
/// recommended defaults without prompting.
fn run_engine_init(folder: &Path) -> Result<(), WorkspaceCommandError> {
    let command = locator::init_from_process(folder)
        .map_err(|error| WorkspaceCommandError::new("engine_not_located", error.to_string()))?;
    engine::run_once(&command, INIT_TIMEOUT).map_err(|error| workspace::init_refusal(&error))
}

/// Start the engine, then the shell; stop the engine when the shell exits.
pub fn run() {
    let keys = Arc::new(ApiKeys::new(apikey::platform_store()));
    let environ = Arc::clone(&keys);
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(keys)
        .on_window_event(|window, event| {
            // Files dropped on the window go to the page as a plain DOM event;
            // it only quotes them, and the person agrees before anything is added.
            if let tauri::WindowEvent::DragDrop(drag) = event {
                let script = match drag {
                    tauri::DragDropEvent::Enter { .. } => dropped::over_script(true),
                    tauri::DragDropEvent::Leave => dropped::over_script(false),
                    tauri::DragDropEvent::Drop { paths, .. } => dropped::drop_script(paths),
                    _ => return,
                };
                if let Some(webview) = window.app_handle().get_webview_window(window.label()) {
                    let _ = webview.eval(script);
                }
            }
        })
        .setup(move |app| {
            // The shell's own directory, never the workspace.
            let config_dir = app.path().app_config_dir()?;
            let workspaces = Arc::new(Workspaces::new(Choice::new(&config_dir)));
            let chosen = Arc::clone(&workspaces);
            // Started before the window opens, so the engine warms up meanwhile.
            let supervisor = Supervisor::start_with_env(
                Box::new(move || {
                    locator::locate_from_process(chosen.remembered().as_deref())
                        .map_err(Unlocated::from)
                }),
                Box::new(move || environ.engine_env()),
                SupervisorConfig::default(),
            );
            app.manage(supervisor);
            app.manage(workspaces);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine_state,
            set_api_key,
            clear_api_key,
            api_key_status,
            workspace_choice_status,
            open_workspace,
            create_workspace,
            pick_workspace_folder,
            pick_files,
            open_runtime_download
        ])
        .build(tauri::generate_context!())
        .expect("the desktop shell could not be built");
    app.run(|handle, event| {
        // A graceful exit stops the engine itself; a force-quit is covered by
        // the engine's stdin lifeline, which closes with this process.
        if let tauri::RunEvent::Exit = event
            && let Some(supervisor) = handle.try_state::<Supervisor>()
        {
            supervisor.shutdown();
        }
    });
}
