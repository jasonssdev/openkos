//! The engine locator: the one place that knows WHICH engine the shell runs.
//!
//! Three engines, in this order: any `openkos`-compatible executable named by
//! `OPENKOS_ENGINE`; inside a macOS app bundle, the frozen engine the bundle
//! ships under `Contents/Resources/engine` (ADR-0056); otherwise the
//! development engine of the source checkout this shell was built from, run as
//! `uv run --project <checkout> openkos serve --workspace <dir>`. Nothing
//! outside this module knows which one runs.

use std::ffi::OsString;
use std::fmt;
use std::path::{Path, PathBuf};

use crate::engine::EngineCommand;

/// An executable that accepts `serve --workspace <dir>`, overriding the default.
pub const ENGINE_ENV: &str = "OPENKOS_ENGINE";

/// The workspace to serve; the shell's working directory when unset.
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
    WorkspaceMissing(PathBuf),
    BundledEngineMissing(PathBuf),
}

impl fmt::Display for LocateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
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

/// Form the engine command from the environment.
///
/// `env` reads one variable, `cwd` is the shell's working directory and
/// `source` where the default engine comes from.
pub fn locate(
    env: &dyn Fn(&str) -> Option<OsString>,
    cwd: &Path,
    source: &EngineSource,
) -> Result<EngineCommand, LocateError> {
    let workspace = match env(WORKSPACE_ENV) {
        Some(path) => cwd.join(path),
        None => cwd.to_path_buf(),
    };
    if !workspace.is_dir() {
        return Err(LocateError::WorkspaceMissing(workspace));
    }
    let serve = [
        OsString::from("serve"),
        OsString::from("--workspace"),
        workspace.clone().into_os_string(),
    ];
    let (program, args) = match (env(ENGINE_ENV), source) {
        (Some(engine), _) => (engine, serve.to_vec()),
        (None, EngineSource::Bundled { resources }) => {
            // Never a fallback to `uv`: an installed app without its engine is
            // a broken build, and a checkout's engine would hide it.
            let engine = bundled_engine(resources);
            if !engine.is_file() {
                return Err(LocateError::BundledEngineMissing(engine));
            }
            (engine.into_os_string(), serve.to_vec())
        }
        (None, EngineSource::Development { checkout }) => {
            let mut args = vec![
                OsString::from("run"),
                OsString::from("--project"),
                checkout.as_os_str().to_owned(),
                OsString::from("openkos"),
            ];
            args.extend(serve);
            (OsString::from("uv"), args)
        }
    };
    Ok(EngineCommand {
        program,
        args,
        cwd: Some(workspace),
    })
}

/// [`locate`] against the real process environment and executable.
pub fn locate_from_process() -> Result<EngineCommand, LocateError> {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let shell_exe = std::env::current_exe().unwrap_or_default();
    locate(
        &|name| std::env::var_os(name),
        &cwd,
        &engine_source(&shell_exe, Path::new(DEV_CHECKOUT)),
    )
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
        let command = locate(&env_of(&[]), &workspace, &development()).expect("located");
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
    fn the_engine_variable_overrides_the_program() {
        let workspace = std::env::temp_dir();
        let engine = Path::new("/opt/openkos/bin/openkos");
        let command =
            locate(&env_of(&[(ENGINE_ENV, engine)]), &workspace, &development()).expect("located");
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
            &env_of(&[]),
            &workspace,
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
            &env_of(&[]),
            &std::env::temp_dir(),
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
            &env_of(&[]),
            &std::env::temp_dir(),
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
            &env_of(&[(ENGINE_ENV, engine)]),
            &std::env::temp_dir(),
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
}
