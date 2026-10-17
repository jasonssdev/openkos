//! The shell against the REAL engine of this checkout, not a fake.
//!
//! Ignored by default because it needs `uv` and the Python environment of the
//! checkout; CI runs it with `cargo test -- --ignored`. It proves the handshake
//! the fakes imitate is the one `openkos serve` actually speaks, and that a
//! request shaped like the web view's (its Origin, the bearer token, the CORS
//! preflight) is answered.

use std::ffi::OsString;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use std::sync::Arc;

use openkos_desktop::engine;
use openkos_desktop::locator::{self, DEV_CHECKOUT, EngineSource, WORKSPACE_ENV};
use openkos_desktop::supervisor::{EngineState, Supervisor, SupervisorConfig};
use openkos_desktop::workspace::{Choice, WORKSPACE_MARKER, WorkspaceCommandError, Workspaces};

/// The web view's origin on macOS, one of the engine's allowed origins.
const WEB_VIEW_ORIGIN: &str = "tauri://localhost";

fn fresh_workspace() -> PathBuf {
    // Canonical: the engine refuses a workspace path with a symlinked segment,
    // and the temporary directory is one on macOS (`/var` -> `/private/var`).
    let base = std::env::temp_dir().canonicalize().expect("temp dir");
    let dir = base.join(format!("openkos-desktop-live-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("workspace dir");
    let status = Command::new("uv")
        .args(["run", "--project", DEV_CHECKOUT, "openkos", "init"])
        .current_dir(&dir)
        .env("GIT_AUTHOR_NAME", "openkos-desktop-test")
        .env("GIT_AUTHOR_EMAIL", "test@example.invalid")
        .env("GIT_COMMITTER_NAME", "openkos-desktop-test")
        .env("GIT_COMMITTER_EMAIL", "test@example.invalid")
        .status()
        .expect("uv run openkos init");
    assert!(status.success(), "openkos init failed");
    dir
}

/// One HTTP/1.1 exchange over a fresh connection; returns status and raw head.
fn exchange(port: u16, request: &str) -> (u16, String) {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
    stream
        .set_read_timeout(Some(Duration::from_secs(30)))
        .expect("timeout");
    stream.write_all(request.as_bytes()).expect("send");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("receive");
    let status = response
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .expect("status line");
    (status, response)
}

fn port_of(base_url: &str) -> u16 {
    let authority = base_url
        .strip_prefix("http://127.0.0.1:")
        .expect("loopback base URL");
    authority[..authority.find('/').expect("base path")]
        .parse()
        .expect("port")
}

#[test]
#[ignore = "needs uv and the checkout's Python environment"]
fn live_the_web_views_request_reads_the_workspace_status() {
    let workspace = fresh_workspace();
    let env_workspace = workspace.clone();
    let supervisor = Supervisor::start(
        Box::new(move || {
            let lookup = |name: &str| -> Option<OsString> {
                (name == WORKSPACE_ENV).then(|| env_workspace.clone().into_os_string())
            };
            let source = EngineSource::Development {
                checkout: PathBuf::from(DEV_CHECKOUT),
            };
            locator::locate(&lookup, Path::new("/"), None, &source).map_err(Into::into)
        }),
        SupervisorConfig::default(),
    );
    let state = supervisor
        .wait_for(Duration::from_secs(120), |s| {
            matches!(s, EngineState::Ready { .. } | EngineState::Failed { .. })
        })
        .expect("the engine settled");
    let EngineState::Ready {
        base_url,
        token,
        pid,
        ..
    } = state
    else {
        panic!("the engine did not start: {state:?}");
    };
    let port = port_of(&base_url);
    let host = format!("127.0.0.1:{port}");

    let (status, head) = exchange(
        port,
        &format!(
            "OPTIONS /v1/status HTTP/1.1\r\nHost: {host}\r\nOrigin: {WEB_VIEW_ORIGIN}\r\n\
             Access-Control-Request-Method: GET\r\n\
             Access-Control-Request-Headers: authorization\r\nConnection: close\r\n\r\n"
        ),
    );
    assert_eq!(status, 204, "preflight: {head}");

    let (status, response) = exchange(
        port,
        &format!(
            "GET /v1/status HTTP/1.1\r\nHost: {host}\r\nOrigin: {WEB_VIEW_ORIGIN}\r\n\
             Authorization: Bearer {}\r\nConnection: close\r\n\r\n",
            token.as_str()
        ),
    );
    assert_eq!(status, 200, "status: {response}");
    let lower = response.to_ascii_lowercase();
    assert!(
        lower.contains(&format!("access-control-allow-origin: {WEB_VIEW_ORIGIN}")),
        "{response}"
    );
    assert!(response.contains("\"concepts\""), "{response}");

    let (status, _) = exchange(
        port,
        &format!("GET /v1/status HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"),
    );
    assert_eq!(status, 401, "no token must be refused");

    supervisor.shutdown();
    assert_eq!(supervisor.state(), EngineState::Stopped);
    let probe = Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .expect("kill -0");
    assert!(!probe.success(), "the engine {pid} outlived the shutdown");
    let _ = std::fs::remove_dir_all(&workspace);
}

/// A first-run user: nothing chosen, so no engine runs; creating a workspace
/// in a new folder runs the real `init` with no terminal, which writes the
/// recommended models without prompting, and the engine then serves it.
#[test]
#[ignore = "needs uv and the checkout's Python environment"]
fn live_a_created_workspace_is_initialised_without_prompting_and_served() {
    let base = std::env::temp_dir().canonicalize().expect("temp dir");
    let root = base.join(format!(
        "openkos-desktop-live-create-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).expect("root");
    let folder = root.join("my-knowledge");
    let source = EngineSource::Development {
        checkout: PathBuf::from(DEV_CHECKOUT),
    };
    let no_env = |_: &str| -> Option<OsString> { None };

    let workspaces = Arc::new(Workspaces::new(Choice::new(&root.join("config"))));
    let chosen = Arc::clone(&workspaces);
    let serve_source = source.clone();
    let supervisor = Supervisor::start(
        Box::new(move || {
            locator::locate(
                &no_env,
                Path::new("/"),
                chosen.remembered().as_deref(),
                &serve_source,
            )
            .map_err(Into::into)
        }),
        SupervisorConfig::default(),
    );
    supervisor
        .wait_for(Duration::from_secs(30), |s| {
            matches!(s, EngineState::NoWorkspace)
        })
        .expect("no workspace yet");

    let init = |dir: &Path| -> Result<(), WorkspaceCommandError> {
        let command = locator::init_command(&no_env, &source, dir)
            .map_err(|e| WorkspaceCommandError::new("engine_not_located", e.to_string()))?;
        engine::run_once(&command, Duration::from_secs(180))
            .map_err(|e| WorkspaceCommandError::new("init_failed", e.detail()))
    };
    let status = workspaces
        .create(None, folder.to_str().expect("utf-8"), &init, &|| {
            supervisor.restart()
        })
        .expect("created");
    assert!(status.valid, "{status:?}");
    let config = std::fs::read_to_string(folder.join(WORKSPACE_MARKER)).expect("openkos.yaml");
    assert!(config.contains("qwen3:8b"), "{config}");
    assert!(config.contains("bge-m3"), "{config}");
    assert!(!folder.join("config").exists());

    let state = supervisor
        .wait_for(Duration::from_secs(120), |s| {
            matches!(s, EngineState::Ready { .. } | EngineState::Failed { .. })
        })
        .expect("the engine settled");
    assert!(matches!(state, EngineState::Ready { .. }), "{state:?}");
    supervisor.shutdown();
    let _ = std::fs::remove_dir_all(&root);
}
