//! The engine locator: the one place that knows WHICH engine the shell runs.
//!
//! For now that is the development engine of a source checkout, run as
//! `uv run --project <checkout> openkos serve --workspace <dir>`, or any
//! `openkos`-compatible executable named by `OPENKOS_ENGINE`. The freeze stage
//! (ADR-0056) replaces the development default with the frozen engine shipped
//! inside the app bundle, and nothing outside this module changes.

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

/// Why no engine command could be formed. Not retried: nothing changes on its own.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LocateError {
    WorkspaceMissing(PathBuf),
}

impl fmt::Display for LocateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::WorkspaceMissing(path) => write!(
                f,
                "the workspace {} is not a directory; set {WORKSPACE_ENV} to an OpenKOS workspace",
                path.display()
            ),
        }
    }
}

/// Form the engine command from the environment.
///
/// `env` reads one variable, `cwd` is the shell's working directory and
/// `dev_checkout` the source checkout whose engine is the default.
pub fn locate(
    env: &dyn Fn(&str) -> Option<OsString>,
    cwd: &Path,
    dev_checkout: &Path,
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
    let (program, args) = match env(ENGINE_ENV) {
        Some(engine) => (engine, serve.to_vec()),
        None => {
            let mut args = vec![
                OsString::from("run"),
                OsString::from("--project"),
                dev_checkout.as_os_str().to_owned(),
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

/// [`locate`] against the real process environment.
pub fn locate_from_process() -> Result<EngineCommand, LocateError> {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    locate(
        &|name| std::env::var_os(name),
        &cwd,
        Path::new(DEV_CHECKOUT),
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

    #[test]
    fn the_default_is_the_dev_checkout_engine_under_uv() {
        let workspace = std::env::temp_dir();
        let checkout = Path::new("/src/openkos");
        let command = locate(&env_of(&[]), &workspace, checkout).expect("located");
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
        let command = locate(
            &env_of(&[(ENGINE_ENV, engine)]),
            &workspace,
            Path::new("/src/openkos"),
        )
        .expect("located");
        assert_eq!(command.program, engine.as_os_str());
        assert_eq!(
            args(&command),
            ["serve", "--workspace", &workspace.to_string_lossy()]
        );
    }

    #[test]
    fn the_workspace_variable_wins_over_the_working_directory() {
        let workspace = std::env::temp_dir();
        let command = locate(
            &env_of(&[(WORKSPACE_ENV, &workspace)]),
            Path::new("/"),
            Path::new("/src/openkos"),
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
            Path::new("/src/openkos"),
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
            Path::new("/src/openkos"),
        )
        .expect_err("refused");
        assert_eq!(err, LocateError::WorkspaceMissing(missing.to_path_buf()));
        assert!(err.to_string().contains(WORKSPACE_ENV));
    }

    #[test]
    fn the_dev_checkout_is_the_repository_root() {
        assert!(Path::new(DEV_CHECKOUT).join("pyproject.toml").is_file());
    }
}
