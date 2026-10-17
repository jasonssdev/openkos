//! The remote backend's API key (ADR-0069).
//!
//! The engine reads the key of its opt-in `openai-compatible` backend only from
//! the `OPENKOS_OPENAI_API_KEY` environment variable (ADR-0031). A person who
//! never opens a terminal cannot set one, so the shell keeps the key in the
//! operating system's keychain and hands it to the engine as that variable of
//! the process it supervises. The web view can set the key, clear it and ask
//! whether one is set; nothing here ever gives the key back to it, and the key
//! is never written to the workspace, a log or the engine state.

use std::fmt;
use std::sync::Mutex;

use serde::Serialize;

use crate::engine::EngineEnv;

/// The variable the engine reads the key from (`application/backends.py`).
pub const API_KEY_ENV: &str = "OPENKOS_OPENAI_API_KEY";

/// The keychain item: the app's identifier as its service, a fixed account.
pub const KEYCHAIN_SERVICE: &str = "io.github.jasonssdev.openkos";
pub const KEYCHAIN_ACCOUNT: &str = "openai-compatible-api-key";

/// Longer than any provider's key; refuses a pasted document, not a real key.
pub const MAX_KEY_BYTES: usize = 4096;

/// A key as the user gave it, trimmed. Its `Debug` never prints the secret, and
/// it is deliberately not `Serialize`, so it cannot reach the web view.
#[derive(Clone, PartialEq, Eq)]
pub struct ApiKey(String);

impl ApiKey {
    /// Accept a key the engine can receive as an environment variable value.
    pub fn parse(raw: &str) -> Result<Self, KeyError> {
        let key = raw.trim();
        if key.is_empty() {
            Err(KeyError::Empty)
        } else if key.chars().any(char::is_control) {
            Err(KeyError::ControlCharacter)
        } else if key.len() > MAX_KEY_BYTES {
            Err(KeyError::TooLong)
        } else {
            Ok(Self(key.to_owned()))
        }
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for ApiKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ApiKey(<redacted>)")
    }
}

/// Why a key was not accepted. Never carries the key.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyError {
    Empty,
    ControlCharacter,
    TooLong,
}

impl fmt::Display for KeyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Empty => "the key is empty",
            Self::ControlCharacter => "the key contains a line break or another control character",
            Self::TooLong => "the key is longer than any provider's key",
        })
    }
}

/// Why the store could not do what was asked. Never carries the key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StoreError {
    /// This platform has no keychain the shell can use.
    Unsupported,
    /// The keychain refused or failed: a fixed description, never the secret.
    Failed(String),
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsupported => {
                f.write_str("keeping the key in a keychain is not supported on this platform")
            }
            Self::Failed(detail) => write!(f, "the keychain failed: {detail}"),
        }
    }
}

/// Where the key is kept. Behind a trait so tests never touch a real keychain.
pub trait SecretStore: Send + Sync {
    /// The stored key, or `None` when there is none.
    fn read(&self) -> Result<Option<ApiKey>, StoreError>;
    /// Store `key`, replacing any earlier one.
    fn write(&self, key: &ApiKey) -> Result<(), StoreError>;
    /// Remove the key. Removing an absent key succeeds.
    fn delete(&self) -> Result<(), StoreError>;
}

/// The store of a platform with no keychain support: every call refuses.
pub struct UnsupportedStore;

impl SecretStore for UnsupportedStore {
    fn read(&self) -> Result<Option<ApiKey>, StoreError> {
        Err(StoreError::Unsupported)
    }

    fn write(&self, _: &ApiKey) -> Result<(), StoreError> {
        Err(StoreError::Unsupported)
    }

    fn delete(&self) -> Result<(), StoreError> {
        Err(StoreError::Unsupported)
    }
}

/// The macOS login keychain, one generic password item.
#[cfg(target_os = "macos")]
pub struct KeychainStore {
    entry: keyring_core::Entry,
}

#[cfg(target_os = "macos")]
impl KeychainStore {
    pub fn new() -> Result<Self, StoreError> {
        use apple_native_keyring_store::keychain::{Cred, MacKeychainDomain};
        Cred::build(MacKeychainDomain::User, KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
            .map(|entry| Self { entry })
            .map_err(store_failure)
    }
}

/// A keychain error as a fixed sentence. Variants that carry stored bytes
/// (`BadEncoding`, `BadDataFormat`) are never formatted, so a secret cannot
/// leak through an error.
#[cfg(target_os = "macos")]
fn store_failure(error: keyring_core::Error) -> StoreError {
    use keyring_core::Error;
    StoreError::Failed(match error {
        Error::PlatformFailure(e) => format!("the keychain reported an error ({e})"),
        Error::NoStorageAccess(e) => format!("the keychain could not be opened ({e})"),
        Error::BadEncoding(_) | Error::BadDataFormat(..) => {
            "the stored key is not readable text".to_owned()
        }
        _ => "the keychain refused the operation".to_owned(),
    })
}

#[cfg(target_os = "macos")]
impl SecretStore for KeychainStore {
    fn read(&self) -> Result<Option<ApiKey>, StoreError> {
        match self.entry.get_password() {
            Ok(stored) => Ok(ApiKey::parse(&stored).ok()),
            Err(keyring_core::Error::NoEntry) => Ok(None),
            Err(error) => Err(store_failure(error)),
        }
    }

    fn write(&self, key: &ApiKey) -> Result<(), StoreError> {
        self.entry.set_password(key.as_str()).map_err(store_failure)
    }

    fn delete(&self) -> Result<(), StoreError> {
        match self.entry.delete_credential() {
            Ok(()) | Err(keyring_core::Error::NoEntry) => Ok(()),
            Err(error) => Err(store_failure(error)),
        }
    }
}

/// The store this platform uses: the login keychain on macOS, none elsewhere.
pub fn platform_store() -> Box<dyn SecretStore> {
    #[cfg(target_os = "macos")]
    {
        match KeychainStore::new() {
            Ok(store) => Box::new(store),
            Err(_) => Box::new(UnsupportedStore),
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        Box::new(UnsupportedStore)
    }
}

/// What a key command answers the web view when it fails: a reason a screen
/// can branch on and an English detail. Never carries the key.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct KeyCommandError {
    pub reason: &'static str,
    pub detail: String,
}

impl From<KeyError> for KeyCommandError {
    fn from(error: KeyError) -> Self {
        Self {
            reason: "invalid_key",
            detail: error.to_string(),
        }
    }
}

impl From<StoreError> for KeyCommandError {
    fn from(error: StoreError) -> Self {
        Self {
            reason: match error {
                StoreError::Unsupported => "not_supported",
                StoreError::Failed(_) => "store_failed",
            },
            detail: error.to_string(),
        }
    }
}

/// The key the shell holds: read from the store once, then kept in memory, so
/// an engine restart does not ask the keychain again.
pub struct ApiKeys {
    store: Box<dyn SecretStore>,
    /// `None` until the store was read successfully.
    cached: Mutex<Option<Option<ApiKey>>>,
}

impl ApiKeys {
    pub fn new(store: Box<dyn SecretStore>) -> Self {
        Self {
            store,
            cached: Mutex::new(None),
        }
    }

    /// The current key, read from the store the first time. A failed read is
    /// not remembered, so the next call asks again.
    pub fn current(&self) -> Result<Option<ApiKey>, StoreError> {
        let mut cached = self.cached.lock().expect("key lock");
        if let Some(key) = cached.as_ref() {
            return Ok(key.clone());
        }
        let key = self.store.read()?;
        *cached = Some(key.clone());
        Ok(key)
    }

    /// Whether a key is set: all the web view may learn about it.
    pub fn is_set(&self) -> Result<bool, StoreError> {
        self.current().map(|key| key.is_some())
    }

    /// Validate and store `raw` as the key.
    pub fn set(&self, raw: &str) -> Result<(), KeyCommandError> {
        let key = ApiKey::parse(raw)?;
        let mut cached = self.cached.lock().expect("key lock");
        self.store.write(&key)?;
        *cached = Some(Some(key));
        Ok(())
    }

    /// Remove the key.
    pub fn clear(&self) -> Result<(), KeyCommandError> {
        let mut cached = self.cached.lock().expect("key lock");
        self.store.delete()?;
        *cached = Some(None);
        Ok(())
    }

    /// The engine's extra environment: the key as `OPENKOS_OPENAI_API_KEY`, or
    /// nothing. When the store cannot be read the engine starts without the
    /// key, so a local backend keeps working; the reason goes to stderr.
    pub fn engine_env(&self) -> EngineEnv {
        let mut env = EngineEnv::default();
        match self.current() {
            Ok(Some(key)) => env.set(API_KEY_ENV, key.as_str()),
            Ok(None) | Err(StoreError::Unsupported) => {}
            Err(error) => {
                eprintln!("openkos-desktop: starting the engine without the API key: {error}")
            }
        }
        env
    }
}

/// `set_api_key`: store the key, then restart the engine so it takes effect.
/// A key that is refused leaves the store and the engine as they were.
pub fn set_and_restart(
    keys: &ApiKeys,
    raw: &str,
    restart: &dyn Fn(),
) -> Result<(), KeyCommandError> {
    keys.set(raw)?;
    restart();
    Ok(())
}

/// `clear_api_key`: remove the key, then restart the engine without it.
pub fn clear_and_restart(keys: &ApiKeys, restart: &dyn Fn()) -> Result<(), KeyCommandError> {
    keys.clear()?;
    restart();
    Ok(())
}

#[cfg(test)]
pub(crate) mod fake {
    //! An in-memory store that counts its reads and can be made to fail.

    use super::{ApiKey, SecretStore, StoreError};
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    #[derive(Default)]
    pub struct MemoryStore {
        pub key: Mutex<Option<String>>,
        pub reads: AtomicUsize,
        pub failing: AtomicBool,
    }

    impl MemoryStore {
        fn check(&self) -> Result<(), StoreError> {
            if self.failing.load(Ordering::SeqCst) {
                Err(StoreError::Failed("the fake store is failing".into()))
            } else {
                Ok(())
            }
        }
    }

    impl SecretStore for std::sync::Arc<MemoryStore> {
        fn read(&self) -> Result<Option<ApiKey>, StoreError> {
            self.reads.fetch_add(1, Ordering::SeqCst);
            self.check()?;
            Ok(self
                .key
                .lock()
                .expect("key")
                .as_deref()
                .map(|k| ApiKey(k.to_owned())))
        }

        fn write(&self, key: &ApiKey) -> Result<(), StoreError> {
            self.check()?;
            *self.key.lock().expect("key") = Some(key.as_str().to_owned());
            Ok(())
        }

        fn delete(&self) -> Result<(), StoreError> {
            self.check()?;
            *self.key.lock().expect("key") = None;
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::fake::MemoryStore;
    use super::*;
    use std::cell::Cell;
    use std::sync::Arc;
    use std::sync::atomic::Ordering;

    const SECRET: &str = "sk-test-0123456789";

    fn keys() -> (Arc<MemoryStore>, ApiKeys) {
        let store = Arc::new(MemoryStore::default());
        (Arc::clone(&store), ApiKeys::new(Box::new(store)))
    }

    #[test]
    fn a_key_is_trimmed_and_never_printed() {
        let key = ApiKey::parse(&format!("  {SECRET}\n")).expect("key");
        assert_eq!(key.as_str(), SECRET);
        assert!(!format!("{key:?}").contains(SECRET));
    }

    #[test]
    fn an_unusable_key_is_refused() {
        assert_eq!(ApiKey::parse(""), Err(KeyError::Empty));
        assert_eq!(ApiKey::parse(" \t\n"), Err(KeyError::Empty));
        assert_eq!(ApiKey::parse("sk-a\nsk-b"), Err(KeyError::ControlCharacter));
        assert_eq!(ApiKey::parse("sk-\0x"), Err(KeyError::ControlCharacter));
        assert_eq!(
            ApiKey::parse(&"k".repeat(MAX_KEY_BYTES + 1)),
            Err(KeyError::TooLong)
        );
        assert!(ApiKey::parse(&"k".repeat(MAX_KEY_BYTES)).is_ok());
    }

    #[test]
    fn a_set_key_is_stored_and_reaches_the_engine_environment() {
        let (store, keys) = keys();
        assert_eq!(keys.is_set(), Ok(false));
        assert!(keys.engine_env().is_empty());
        keys.set(SECRET).expect("set");
        assert_eq!(store.key.lock().expect("key").as_deref(), Some(SECRET));
        assert_eq!(keys.is_set(), Ok(true));
        let mut expected = EngineEnv::default();
        expected.set(API_KEY_ENV, SECRET);
        assert_eq!(keys.engine_env(), expected);
    }

    #[test]
    fn a_cleared_key_is_removed_from_the_store_and_the_environment() {
        let (store, keys) = keys();
        keys.set(SECRET).expect("set");
        keys.clear().expect("clear");
        assert_eq!(*store.key.lock().expect("key"), None);
        assert_eq!(keys.is_set(), Ok(false));
        assert!(keys.engine_env().is_empty());
        keys.clear().expect("clearing an absent key succeeds");
    }

    #[test]
    fn the_store_is_read_once_then_the_key_is_kept_in_memory() {
        let (store, keys) = keys();
        *store.key.lock().expect("key") = Some(SECRET.into());
        for _ in 0..3 {
            assert_eq!(keys.is_set(), Ok(true));
            assert!(!keys.engine_env().is_empty());
        }
        assert_eq!(store.reads.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn a_failed_read_is_not_remembered_and_the_engine_starts_without_a_key() {
        let (store, keys) = keys();
        *store.key.lock().expect("key") = Some(SECRET.into());
        store.failing.store(true, Ordering::SeqCst);
        assert!(matches!(keys.is_set(), Err(StoreError::Failed(_))));
        assert!(keys.engine_env().is_empty());
        store.failing.store(false, Ordering::SeqCst);
        assert_eq!(keys.is_set(), Ok(true));
    }

    #[test]
    fn a_failed_write_keeps_the_key_that_was_there() {
        let (store, keys) = keys();
        keys.set(SECRET).expect("set");
        store.failing.store(true, Ordering::SeqCst);
        let error = keys.set("sk-other").expect_err("refused");
        assert_eq!(error.reason, "store_failed");
        assert!(!error.detail.contains("sk-other"), "{}", error.detail);
        store.failing.store(false, Ordering::SeqCst);
        assert_eq!(keys.current().expect("read"), ApiKey::parse(SECRET).ok());
    }

    #[test]
    fn an_unsupported_platform_says_so() {
        let keys = ApiKeys::new(Box::new(UnsupportedStore));
        assert_eq!(keys.is_set(), Err(StoreError::Unsupported));
        assert_eq!(
            keys.set(SECRET).expect_err("refused").reason,
            "not_supported"
        );
        assert_eq!(keys.clear().expect_err("refused").reason, "not_supported");
        assert!(keys.engine_env().is_empty());
    }

    #[test]
    fn a_command_error_serialises_without_the_key() {
        let (_, keys) = keys();
        let error = keys
            .set(&format!("{SECRET}\n{SECRET}"))
            .expect_err("refused");
        let json = serde_json::to_value(&error).expect("json");
        assert_eq!(json["reason"], "invalid_key");
        assert!(!json.to_string().contains(SECRET), "{json}");
    }

    #[test]
    fn the_engine_is_restarted_only_after_a_change_succeeded() {
        let (store, keys) = keys();
        let restarts = Cell::new(0);
        let restart = || restarts.set(restarts.get() + 1);
        set_and_restart(&keys, "", &restart).expect_err("empty");
        assert_eq!(restarts.get(), 0);
        set_and_restart(&keys, SECRET, &restart).expect("set");
        assert_eq!(restarts.get(), 1);
        store.failing.store(true, Ordering::SeqCst);
        clear_and_restart(&keys, &restart).expect_err("failing");
        assert_eq!(restarts.get(), 1);
        store.failing.store(false, Ordering::SeqCst);
        clear_and_restart(&keys, &restart).expect("clear");
        assert_eq!(restarts.get(), 2);
    }
}
