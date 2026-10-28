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
//! directory. A workspace is created by the engine's own `init` (#1487). The
//! shell also checks for and installs the app's own signed updates, only once
//! the person confirms (ADR-0079).

pub mod apikey;
pub mod browser;
pub mod dropped;
pub mod engine;
pub mod locator;
pub mod notify;
pub mod supervisor;
pub mod update;
pub mod watch;
pub mod workspace;

use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use apikey::{ApiKeys, KeyCommandError};
use supervisor::{EngineState, Supervisor, SupervisorConfig, Unlocated};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_updater::UpdaterExt;
use update::{Found, Pending, UpdateCheck, UpdateChoice, UpdateCommandError, UpdateSettings};
use watch::{Watch, WatchChoice, WatchConfig, WatchStatus, WatchSupervisor};
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

/// The workspace the shell serves now, when it is one.
fn current_workspace(workspaces: &Workspaces) -> Option<std::path::PathBuf> {
    let status = workspaces.status(locator::workspace_from_process().as_deref());
    status
        .path
        .filter(|_| status.valid)
        .map(std::path::PathBuf::from)
}

/// The inbox watch: whether the person turned it on for the current workspace
/// and what its daemon is doing (ADR-0078).
#[tauri::command]
fn watch_state(
    watch: tauri::State<'_, Watch>,
    workspaces: tauri::State<'_, Arc<Workspaces>>,
) -> WatchStatus {
    watch.status(current_workspace(&workspaces).as_deref())
}

/// Turn the inbox watch on or off for the current workspace. On runs the
/// whole daemon; the choice is remembered for this workspace only.
#[tauri::command]
async fn set_watch(
    on: bool,
    watch: tauri::State<'_, Watch>,
    workspaces: tauri::State<'_, Arc<Workspaces>>,
) -> Result<WatchStatus, WorkspaceCommandError> {
    let workspace = current_workspace(&workspaces).ok_or_else(|| {
        WorkspaceCommandError::new("no_workspace", "no workspace is open to watch for")
    })?;
    watch.turn(&workspace, on).map_err(|error| {
        WorkspaceCommandError::new(
            "watch_choice_unkept",
            format!("the choice could not be kept: {error}"),
        )
    })?;
    Ok(watch.status(Some(&workspace)))
}

/// Tell the person the watch paused on the AI model's limit. The daemon idles
/// on a spent budget and records it for the engine to report, so only the page
/// learns of it; the text is fixed here and the page cannot choose it.
#[tauri::command]
fn notify_watch_budget(app: tauri::AppHandle) {
    notify::show_native(&app, &notify::watch_budget());
}

/// Open the existing workspace at `path`, remember it and restart the engine
/// on it. The watch follows: its daemon stops and starts again only where the
/// person turned it on.
#[tauri::command]
async fn open_workspace(
    path: String,
    workspaces: tauri::State<'_, Arc<Workspaces>>,
    supervisor: tauri::State<'_, Supervisor>,
    watch: tauri::State<'_, Watch>,
) -> Result<ChoiceStatus, WorkspaceCommandError> {
    let status = workspaces.open(locator::workspace_from_process().as_deref(), &path, &|| {
        supervisor.restart()
    })?;
    watch.follow_workspace(current_workspace(&workspaces).as_deref());
    Ok(status)
}

/// Create a workspace in the empty or missing folder `path` with the engine's
/// `init`, remember it and start the engine on it.
#[tauri::command]
async fn create_workspace(
    path: String,
    workspaces: tauri::State<'_, Arc<Workspaces>>,
    supervisor: tauri::State<'_, Supervisor>,
    watch: tauri::State<'_, Watch>,
) -> Result<ChoiceStatus, WorkspaceCommandError> {
    let status = workspaces.create(
        locator::workspace_from_process().as_deref(),
        &path,
        &run_engine_init,
        &|| supervisor.restart(),
    )?;
    watch.follow_workspace(current_workspace(&workspaces).as_deref());
    Ok(status)
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

/// Ask the user for the folder to watch with the system's own picker. `None`
/// when they cancel. Only the shell opens the dialog.
#[tauri::command]
async fn pick_inbox_folder(app: tauri::AppHandle) -> Option<String> {
    app.dialog()
        .file()
        .set_title("Choose a folder to watch for new files")
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

/// The update found by the last check, held until the person confirms it.
type HeldUpdate = Pending<tauri_plugin_updater::Update>;

/// Whether the person lets the app check for its own updates (ADR-0079).
#[tauri::command]
fn update_settings(choice: tauri::State<'_, UpdateChoice>) -> UpdateSettings {
    UpdateSettings {
        check: choice.is_on(),
    }
}

/// Turn update checks on or off. Off also forgets an update on offer.
#[tauri::command]
fn set_update_check(
    on: bool,
    choice: tauri::State<'_, UpdateChoice>,
    pending: tauri::State<'_, HeldUpdate>,
) -> Result<UpdateSettings, UpdateCommandError> {
    choice.set(on).map_err(|error| {
        UpdateCommandError::new(
            "choice_unkept",
            format!("the choice could not be kept: {error}"),
        )
    })?;
    if !on {
        pending.hold(None, None);
    }
    Ok(UpdateSettings {
        check: choice.is_on(),
    })
}

/// Ask the feed whether a newer build exists. Off asks nothing; a failure is
/// quiet. What it finds is only shown: nothing is downloaded here.
#[tauri::command]
async fn check_for_update(
    app: tauri::AppHandle,
    choice: tauri::State<'_, UpdateChoice>,
    pending: tauri::State<'_, HeldUpdate>,
) -> Result<UpdateCheck, UpdateCommandError> {
    let (check, update) = update::check_when_on(choice.is_on(), || async {
        let found = app.updater()?.check().await.inspect_err(|error| {
            eprintln!("openkos-desktop: the update check failed: {error}");
        })?;
        Ok::<_, tauri_plugin_updater::Error>(found.map(|update| {
            let found = Found {
                version: update.version.clone(),
                current_version: update.current_version.clone(),
                notes: update.body.clone(),
            };
            (found, update)
        }))
    })
    .await;
    let version = match &check {
        UpdateCheck::Available { version, .. } => Some(version.clone()),
        _ => None,
    };
    pending.hold(version, update);
    Ok(check)
}

/// Download, verify and install the update the person confirmed, by the
/// version they were shown, then restart into it. The plugin checks the
/// archive's signature against the compiled-in public key before it replaces
/// anything.
#[tauri::command]
async fn install_update(
    version: String,
    app: tauri::AppHandle,
    pending: tauri::State<'_, HeldUpdate>,
) -> Result<(), UpdateCommandError> {
    let update = pending.take(&version)?;
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| {
            eprintln!("openkos-desktop: the update could not be installed: {error}");
            UpdateCommandError::new(
                "install_failed",
                "the update could not be downloaded, verified or installed",
            )
        })?;
    // The new build starts its own engine and watch; stop these first.
    if let Some(watch) = app.try_state::<Watch>() {
        watch.shutdown();
    }
    if let Some(supervisor) = app.try_state::<Supervisor>() {
        supervisor.shutdown();
    }
    app.restart();
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
        .plugin(tauri_plugin_notification::init())
        // Used only from the commands above, never by the page directly.
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(keys)
        .manage(HeldUpdate::default())
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
            app.manage(UpdateChoice::new(&config_dir));
            let chosen = Arc::clone(&workspaces);
            let watch_environ = Arc::clone(&environ);
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
            // The watch's daemon is a second sidecar, idle until the person
            // turns it on; it resumes only where they already did.
            let located = Arc::clone(&workspaces);
            let handle = app.handle().clone();
            let watch = Watch::new(
                WatchChoice::new(&config_dir),
                WatchSupervisor::new(
                    Box::new(move || {
                        locator::locate_daemon_from_process(located.remembered().as_deref())
                            .map_err(Unlocated::from)
                    }),
                    Box::new(move || watch_environ.engine_env()),
                    // The macOS notification is raised from here (ADR-0078).
                    notify::on_failed(Arc::new(move |notice| {
                        notify::show_native(&handle, notice);
                    })),
                    WatchConfig::default(),
                ),
            );
            watch.follow_workspace(current_workspace(&workspaces).as_deref());
            app.manage(watch);
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
            watch_state,
            set_watch,
            notify_watch_budget,
            pick_inbox_folder,
            pick_workspace_folder,
            pick_files,
            open_runtime_download,
            update_settings,
            set_update_check,
            check_for_update,
            install_update
        ])
        .build(tauri::generate_context!())
        .expect("the desktop shell could not be built");
    app.run(|handle, event| {
        // A graceful exit stops the engine itself; a force-quit is covered by
        // the engine's stdin lifeline, which closes with this process.
        if let tauri::RunEvent::Exit = event {
            if let Some(watch) = handle.try_state::<Watch>() {
                watch.shutdown();
            }
            if let Some(supervisor) = handle.try_state::<Supervisor>() {
                supervisor.shutdown();
            }
        }
    });
}
