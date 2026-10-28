//! The engine locator: the one place that knows WHICH engine the shell runs.
//!
//! Three engines, in this order: any `openkos`-compatible executable named by
//! `OPENKOS_ENGINE`; inside a macOS app bundle, the frozen engine the bundle
//! ships under `Contents/Resources/engine` (ADR-0056); otherwise the
//! development engine of the source checkout this shell was built from, run as
//! `uv run --project <checkout> openkos serve --workspace <dir>`. Nothing
//! outside this module knows which one runs. The same engine creates a
//! workspace, run as `init` in the chosen folder.

use std::ffi::OsString;
use std::fmt;
use std::path::{Path, PathBuf};

use crate::engine::EngineCommand;
use crate::supervisor::Unlocated;
use crate::workspace;

/// An executable that accepts `serve --workspace <dir>`, overriding the default.
pub const ENGINE_ENV: &str = "OPENKOS_ENGINE";

/// The workspace to serve. When unset, the workspace the shell remembers.
pub const WORKSPACE_ENV: &str = "OPENKOS_WORKSPACE";

/// The source checkout this shell was built from: its development engine.
pub const DEV_CHECKOUT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");

/// The bundle resource directory the frozen engine ships in
/// (`tauri.bundle.conf.json` maps the freeze here).
pub const BUNDLED_ENGINE_DIR: &str = "engine";

/// The frozen engine's executable (`desktop/engine/openkos-engine.spec`).
pub const BUNDLED_ENGINE_NAME: &str = "openkos-engine";

/// Where the engine comes from when `OPENKOS_ENGINE` does not name one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EngineSource {
    /// A shell inside an app bundle: the bundle's resource directory.
    Bundled { resources: PathBuf },
    /// A shell outside a bundle: the source checkout of its development engine.
    Development { checkout: PathBuf },
}

/// Which engine a shell whose executable is `shell_exe` runs by default.
///
/// A macOS app runs its executable from `<App>.app/Contents/MacOS/`, and keeps
/// its resources in `<App>.app/Contents/Resources/`, which is where Tauri
/// resolves them. Any other location (a `cargo` build under `target/`) is
/// development.
pub fn engine_source(shell_exe: &Path, dev_checkout: &Path) -> EngineSource {
    let macos = shell_exe.parent().filter(|dir| dir.ends_with("MacOS"));
    let contents = macos
        .and_then(Path::parent)
        .filter(|dir| dir.ends_with("Contents"));
    match contents {
        Some(contents) => EngineSource::Bundled {
            resources: contents.join("Resources"),
        },
        None => EngineSource::Development {
            checkout: dev_checkout.to_path_buf(),
        },
    }
}

/// The frozen engine's executable inside a bundle's resource directory.
pub fn bundled_engine(resources: &Path) -> PathBuf {
    resources.join(BUNDLED_ENGINE_DIR).join(BUNDLED_ENGINE_NAME)
}

/// Why no engine command could be formed. Not retried: nothing changes on its own.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LocateError {
    /// No workspace is chosen yet: the variable is unset and nothing usable is
    /// remembered. Not a failure; the user has a choice to make.
    NoWorkspace,
    WorkspaceMissing(PathBuf),
    BundledEngineMissing(PathBuf),
}

impl fmt::Display for LocateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoWorkspace => write!(f, "no workspace is chosen yet"),
            Self::WorkspaceMissing(path) => write!(
                f,
                "the workspace {} is not a directory; set {WORKSPACE_ENV} to an OpenKOS workspace",
                path.display()
            ),
            Self::BundledEngineMissing(path) => write!(
                f,
                "this app's engine {} is missing; the app bundle is incomplete, reinstall it",
                path.display()
            ),
        }
    }
}

impl From<LocateError> for Unlocated {
    fn from(error: LocateError) -> Self {
        match error {
            LocateError::NoWorkspace => Self::NoWorkspace,
            other => Self::Failed(other.to_string()),
        }
    }
}

/// The workspace `OPENKOS_WORKSPACE` names, resolved against `cwd`, if set.
pub fn workspace_from_env(env: &dyn Fn(&str) -> Option<OsString>, cwd: &Path) -> Option<PathBuf> {
    env(WORKSPACE_ENV).map(|path| cwd.join(path))
}

/// The engine's program and the arguments that come before its subcommand.
fn engine_program(
    env: &dyn Fn(&str) -> Option<OsString>,
    source: &EngineSource,
) -> Result<(OsString, Vec<OsString>), LocateError> {
    match (env(ENGINE_ENV), source) {
        (Some(engine), _) => Ok((engine, Vec::new())),
        (None, EngineSource::Bundled { resources }) => {
            // Never a fallback to `uv`: an installed app without its engine is
            // a broken build, and a checkout's engine would hide it.
            let engine = bundled_engine(resources);
            if !engine.is_file() {
                return Err(LocateError::BundledEngineMissing(engine));
            }
            Ok((engine.into_os_string(), Vec::new()))
        }
        (None, EngineSource::Development { checkout }) => Ok((
            OsString::from("uv"),
            vec![
                OsString::from("run"),
                OsString::from("--project"),
                checkout.as_os_str().to_owned(),
                OsString::from("openkos"),
            ],
        )),
    }
}

/// The workspace to run on: the variable's, else a remembered folder that is
/// still a workspace; otherwise `NoWorkspace`.
fn chosen_workspace(
    env: &dyn Fn(&str) -> Option<OsString>,
    cwd: &Path,
    remembered: Option<&Path>,
) -> Result<PathBuf, LocateError> {
    Ok(match workspace_from_env(env, cwd) {
        Some(workspace) => {
            if !workspace.is_dir() {
                return Err(LocateError::WorkspaceMissing(workspace));
            }
            workspace
        }
        None => match remembered {
            Some(path) if workspace::is_workspace(path) => path.to_path_buf(),
            _ => return Err(LocateError::NoWorkspace),
        },
    })
}

/// Form the engine command from the environment.
///
/// `env` reads one variable, `cwd` is the shell's working directory,
/// `remembered` the workspace the shell remembers choosing, and `source` where
/// the default engine comes from. The variable wins; a remembered folder is
/// used only while it is still a workspace; otherwise there is no workspace
/// yet, and nothing is started.
pub fn locate(
    env: &dyn Fn(&str) -> Option<OsString>,
    cwd: &Path,
    remembered: Option<&Path>,
    source: &EngineSource,
) -> Result<EngineCommand, LocateError> {
    let workspace = chosen_workspace(env, cwd, remembered)?;
    let (program, mut args) = engine_program(env, source)?;
    args.extend([
        OsString::from("serve"),
        OsString::from("--workspace"),
        workspace.clone().into_os_string(),
    ]);
    Ok(EngineCommand {
        program,
        args,
        cwd: Some(workspace),
    })
}

/// The command that runs the unattended daemon on the chosen workspace (the
/// same rules as [`locate`]): the same engine, run as `daemon` in the
/// workspace, with the lifeline that ends it when the shell's pipe closes
/// (ADR-0078). It takes no workspace argument: the daemon serves the folder it
/// runs in.
pub fn locate_daemon(
    env: &dyn Fn(&str) -> Option<OsString>,
    cwd: &Path,
    remembered: Option<&Path>,
    source: &EngineSource,
) -> Result<EngineCommand, LocateError> {
    let workspace = chosen_workspace(env, cwd, remembered)?;
    let (program, mut args) = engine_program(env, source)?;
    args.extend([
        OsString::from("daemon"),
        OsString::from("--stop-on-stdin-eof"),
    ]);
    Ok(EngineCommand {
        program,
        args,
        cwd: Some(workspace),
    })
}

/// The command that creates a workspace in `folder`: the same engine, run as
/// `init` with `folder` as its working directory.
pub fn init_command(
    env: &dyn Fn(&str) -> Option<OsString>,
    source: &EngineSource,
    folder: &Path,
) -> Result<EngineCommand, LocateError> {
    let (program, mut args) = engine_program(env, source)?;
    args.push(OsString::from("init"));
    Ok(EngineCommand {
        program,
        args,
        cwd: Some(folder.to_path_buf()),
    })
}

fn process_cwd() -> PathBuf {
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn process_source() -> EngineSource {
    let shell_exe = std::env::current_exe().unwrap_or_default();
    engine_source(&shell_exe, Path::new(DEV_CHECKOUT))
}

/// [`locate`] against the real process environment and executable.
pub fn locate_from_process(remembered: Option<&Path>) -> Result<EngineCommand, LocateError> {
    locate(
        &|name| std::env::var_os(name),
        &process_cwd(),
        remembered,
        &process_source(),
    )
}

/// [`locate_daemon`] against the real process environment and executable.
pub fn locate_daemon_from_process(remembered: Option<&Path>) -> Result<EngineCommand, LocateError> {
    locate_daemon(
        &|name| std::env::var_os(name),
        &process_cwd(),
        remembered,
        &process_source(),
    )
}

/// [`init_command`] against the real process environment and executable.
pub fn init_from_process(folder: &Path) -> Result<EngineCommand, LocateError> {
    init_command(&|name| std::env::var_os(name), &process_source(), folder)
}

/// [`workspace_from_env`] against the real process environment.
pub fn workspace_from_process() -> Option<PathBuf> {
    workspace_from_env(&|name| std::env::var_os(name), &process_cwd())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env_of(pairs: &[(&str, &Path)]) -> impl Fn(&str) -> Option<OsString> {
        let map: HashMap<String, OsString> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), v.as_os_str().to_owned()))
            .collect();
        move |name| map.get(name).cloned()
    }

    fn args(command: &EngineCommand) -> Vec<String> {
        command
            .args
            .iter()
            .map(|a| a.to_string_lossy().into_owned())
            .collect()
    }

    fn development() -> EngineSource {
        EngineSource::Development {
            checkout: PathBuf::from("/src/openkos"),
        }
    }

    /// A scratch app bundle; with `engine`, its frozen engine is a file.
    fn bundle(name: &str, engine: bool) -> PathBuf {
        let resources = std::env::temp_dir()
            .join(format!("openkos-locator-{name}-{}", std::process::id()))
            .join("OpenKOS.app/Contents/Resources");
        let _ = std::fs::remove_dir_all(&resources);
        std::fs::create_dir_all(resources.join(BUNDLED_ENGINE_DIR)).expect("resources");
        if engine {
            std::fs::write(bundled_engine(&resources), b"#!/bin/sh\n").expect("engine");
        }
        resources
    }

    #[test]
    fn the_default_is_the_dev_checkout_engine_under_uv() {
        let workspace = std::env::temp_dir();
        let command = locate(
            &env_of(&[(WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            None,
            &development(),
        )
        .expect("located");
        assert_eq!(command.program, OsString::from("uv"));
        assert_eq!(
            args(&command),
            [
                "run",
                "--project",
                "/src/openkos",
                "openkos",
                "serve",
                "--workspace",
                &workspace.to_string_lossy(),
            ]
        );
    }

    #[test]
    fn the_daemon_is_the_same_engine_run_in_the_workspace_with_its_lifeline() {
        let workspace = std::env::temp_dir();
        let command = locate_daemon(
            &env_of(&[(WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            None,
            &development(),
        )
        .expect("located");
        assert_eq!(command.program, OsString::from("uv"));
        assert_eq!(
            args(&command),
            [
                "run",
                "--project",
                "/src/openkos",
                "openkos",
                "daemon",
                "--stop-on-stdin-eof",
            ]
        );
        assert_eq!(command.cwd.as_deref(), Some(workspace.as_path()));
    }

    #[test]
    fn the_daemon_needs_a_chosen_workspace_like_the_engine_does() {
        let located = locate_daemon(&env_of(&[]), Path::new("/"), None, &development());
        assert_eq!(located, Err(LocateError::NoWorkspace));
    }

    #[test]
    fn the_engine_variable_overrides_the_program() {
        let workspace = std::env::temp_dir();
        let engine = Path::new("/opt/openkos/bin/openkos");
        let command = locate(
            &env_of(&[(ENGINE_ENV, engine), (WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            None,
            &development(),
        )
        .expect("located");
        assert_eq!(command.program, engine.as_os_str());
        assert_eq!(
            args(&command),
            ["serve", "--workspace", &workspace.to_string_lossy()]
        );
    }

    #[test]
    fn a_bundled_shell_runs_its_frozen_engine() {
        let resources = bundle("frozen", true);
        let workspace = std::env::temp_dir();
        let command = locate(
            &env_of(&[(WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            None,
            &EngineSource::Bundled {
                resources: resources.clone(),
            },
        )
        .expect("located");
        assert_eq!(
            command.program,
            resources.join("engine/openkos-engine").into_os_string()
        );
        assert_eq!(
            args(&command),
            ["serve", "--workspace", &workspace.to_string_lossy()]
        );
        assert_eq!(command.cwd.as_deref(), Some(workspace.as_path()));
    }

    #[test]
    fn a_bundle_without_its_engine_is_refused_and_never_falls_back() {
        let resources = bundle("missing", false);
        let err = locate(
            &env_of(&[(WORKSPACE_ENV, &std::env::temp_dir())]),
            Path::new("/"),
            None,
            &EngineSource::Bundled {
                resources: resources.clone(),
            },
        )
        .expect_err("refused");
        let missing = resources.join("engine/openkos-engine");
        assert_eq!(err, LocateError::BundledEngineMissing(missing.clone()));
        assert!(err.to_string().contains(&*missing.to_string_lossy()));
    }

    #[test]
    fn a_directory_in_place_of_the_bundled_engine_is_refused() {
        let resources = bundle("directory", false);
        std::fs::create_dir_all(bundled_engine(&resources)).expect("directory");
        let err = locate(
            &env_of(&[(WORKSPACE_ENV, &std::env::temp_dir())]),
            Path::new("/"),
            None,
            &EngineSource::Bundled {
                resources: resources.clone(),
            },
        )
        .expect_err("refused");
        assert_eq!(
            err,
            LocateError::BundledEngineMissing(bundled_engine(&resources))
        );
    }

    #[test]
    fn the_engine_variable_overrides_a_bundled_engine() {
        let resources = bundle("override", false);
        let engine = Path::new("/opt/openkos/bin/openkos");
        let command = locate(
            &env_of(&[(ENGINE_ENV, engine), (WORKSPACE_ENV, &std::env::temp_dir())]),
            Path::new("/"),
            None,
            &EngineSource::Bundled { resources },
        )
        .expect("located");
        assert_eq!(command.program, engine.as_os_str());
    }

    #[test]
    fn a_shell_inside_an_app_bundle_is_bundled() {
        let source = engine_source(
            Path::new("/Applications/OpenKOS.app/Contents/MacOS/openkos-desktop"),
            Path::new("/src/openkos"),
        );
        assert_eq!(
            source,
            EngineSource::Bundled {
                resources: PathBuf::from("/Applications/OpenKOS.app/Contents/Resources"),
            }
        );
    }

    #[test]
    fn a_shell_built_by_cargo_is_development() {
        let checkout = Path::new("/src/openkos");
        for exe in [
            "/src/openkos/desktop/src-tauri/target/debug/openkos-desktop",
            "/somewhere/MacOS/openkos-desktop",
            "/somewhere/Contents/openkos-desktop",
            "openkos-desktop",
            "",
        ] {
            assert_eq!(
                engine_source(Path::new(exe), checkout),
                development(),
                "{exe:?}"
            );
        }
    }

    #[test]
    fn the_workspace_variable_wins_over_the_working_directory() {
        let workspace = std::env::temp_dir();
        let command = locate(
            &env_of(&[(WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            None,
            &development(),
        )
        .expect("located");
        assert_eq!(
            args(&command).last().map(String::as_str),
            Some(&*workspace.to_string_lossy())
        );
        assert_eq!(command.cwd.as_deref(), Some(workspace.as_path()));
    }

    #[test]
    fn a_relative_workspace_is_made_absolute_against_the_working_directory() {
        let cwd = std::env::temp_dir();
        let command = locate(
            &env_of(&[(WORKSPACE_ENV, Path::new("."))]),
            &cwd,
            None,
            &development(),
        )
        .expect("located");
        let served = PathBuf::from(args(&command).last().expect("workspace arg"));
        assert!(served.is_absolute(), "{}", served.display());
    }

    #[test]
    fn a_missing_workspace_is_refused_before_any_spawn() {
        let missing = Path::new("/openkos-desktop/no/such/workspace");
        let err = locate(
            &env_of(&[(WORKSPACE_ENV, missing)]),
            Path::new("/"),
            None,
            &development(),
        )
        .expect_err("refused");
        assert_eq!(err, LocateError::WorkspaceMissing(missing.to_path_buf()));
        assert!(err.to_string().contains(WORKSPACE_ENV));
    }

    #[test]
    fn the_dev_checkout_is_the_repository_root() {
        assert!(Path::new(DEV_CHECKOUT).join("pyproject.toml").is_file());
    }

    /// The bundle overlay maps the freeze to the directory this locator reads,
    /// and the freeze is the one the recipe names: a rename on either side
    /// fails here, not in an installed app that cannot find its engine.
    #[test]
    fn the_bundle_configuration_ships_the_freeze_where_the_locator_looks() {
        let overlay = Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.bundle.conf.json");
        let config: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(overlay).expect("overlay"))
                .expect("overlay JSON");
        let resources = config["bundle"]["resources"]
            .as_object()
            .expect("a resources map");
        assert_eq!(resources.len(), 1, "{resources:?}");
        let (source, target) = resources.iter().next().expect("one resource");
        assert_eq!(target.as_str(), Some(&*format!("{BUNDLED_ENGINE_DIR}/")));
        assert!(
            source.ends_with(&format!("/{BUNDLED_ENGINE_NAME}/")),
            "{source}"
        );
        assert_eq!(config["bundle"]["active"], serde_json::Value::Bool(true));

        let recipe = Path::new(DEV_CHECKOUT).join("desktop/engine/openkos-engine.spec");
        let recipe = std::fs::read_to_string(recipe).expect("recipe");
        assert_eq!(
            recipe
                .matches(&format!("name=\"{BUNDLED_ENGINE_NAME}\""))
                .count(),
            2,
            "the recipe names its executable and its directory {BUNDLED_ENGINE_NAME}"
        );
    }

    /// A scratch directory; with `workspace`, it holds the workspace marker.
    fn scratch_dir(name: &str, workspace: bool) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("openkos-locator-ws-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("scratch");
        if workspace {
            std::fs::write(
                dir.join(crate::workspace::WORKSPACE_MARKER),
                b"backend: ollama\n",
            )
            .expect("marker");
        }
        dir
    }

    #[test]
    fn with_no_variable_and_nothing_remembered_there_is_no_workspace_yet() {
        let err = locate(&env_of(&[]), &std::env::temp_dir(), None, &development())
            .expect_err("no workspace");
        assert_eq!(err, LocateError::NoWorkspace);
    }

    #[test]
    fn the_remembered_workspace_is_served_when_the_variable_is_unset() {
        let workspace = scratch_dir("remembered", true);
        let command = locate(
            &env_of(&[]),
            Path::new("/"),
            Some(&workspace),
            &development(),
        )
        .expect("located");
        assert_eq!(
            args(&command).last().map(String::as_str),
            Some(&*workspace.to_string_lossy())
        );
        assert_eq!(command.cwd.as_deref(), Some(workspace.as_path()));
    }

    #[test]
    fn a_remembered_folder_that_is_no_longer_a_workspace_is_no_workspace_yet() {
        let gone = scratch_dir("gone", false);
        let err = locate(&env_of(&[]), Path::new("/"), Some(&gone), &development())
            .expect_err("no workspace");
        assert_eq!(err, LocateError::NoWorkspace);
    }

    #[test]
    fn the_variable_wins_over_the_remembered_workspace() {
        let remembered = scratch_dir("loser", true);
        let workspace = std::env::temp_dir();
        let command = locate(
            &env_of(&[(WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            Some(&remembered),
            &development(),
        )
        .expect("located");
        assert_eq!(command.cwd.as_deref(), Some(workspace.as_path()));
    }

    #[test]
    fn init_runs_the_same_engine_in_the_chosen_folder() {
        let folder = scratch_dir("init", false);
        let dev = init_command(&env_of(&[]), &development(), &folder).expect("dev");
        assert_eq!(dev.program, OsString::from("uv"));
        assert_eq!(
            args(&dev),
            ["run", "--project", "/src/openkos", "openkos", "init"]
        );
        assert_eq!(dev.cwd.as_deref(), Some(folder.as_path()));

        let engine = Path::new("/opt/openkos/bin/openkos");
        let explicit =
            init_command(&env_of(&[(ENGINE_ENV, engine)]), &development(), &folder).expect("env");
        assert_eq!(explicit.program, engine.as_os_str());
        assert_eq!(args(&explicit), ["init"]);

        let resources = bundle("init", true);
        let frozen = init_command(
            &env_of(&[]),
            &EngineSource::Bundled {
                resources: resources.clone(),
            },
            &folder,
        )
        .expect("bundled");
        assert_eq!(frozen.program, bundled_engine(&resources).into_os_string());
        assert_eq!(args(&frozen), ["init"]);
    }

    #[test]
    fn init_with_a_bundle_missing_its_engine_is_refused() {
        let resources = bundle("init-missing", false);
        let err = init_command(
            &env_of(&[]),
            &EngineSource::Bundled {
                resources: resources.clone(),
            },
            &std::env::temp_dir(),
        )
        .expect_err("refused");
        assert_eq!(
            err,
            LocateError::BundledEngineMissing(bundled_engine(&resources))
        );
    }

    #[test]
    fn the_workspace_variable_is_resolved_against_the_working_directory() {
        assert_eq!(workspace_from_env(&env_of(&[]), Path::new("/x")), None);
        assert_eq!(
            workspace_from_env(&env_of(&[(WORKSPACE_ENV, Path::new("w"))]), Path::new("/x")),
            Some(PathBuf::from("/x/w"))
        );
    }
}
