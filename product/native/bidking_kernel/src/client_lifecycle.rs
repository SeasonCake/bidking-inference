//! Narrow native launcher/diagnostics/startup-repair lifecycle boundary.
//!
//! The Python/Cython entrypoint supplies only operational parameters.  Private
//! PowerShell source is sealed into this native module at build time, verified
//! before every use, and never returned through the Python ABI.

use crate::startup_timing;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::env;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

include!(concat!(env!("OUT_DIR"), "/embedded_lifecycle.rs"));

const EXPECTED_CALLER: &str = "_client_launcher";
const SEAL_DOMAIN: &[u8] = b"bidking-client-lifecycle-seal-v2\0";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const POWERSHELL_STDIN_BOOTSTRAP: &str = r#"
$ErrorActionPreference = "Stop"
$encodedSource = [Console]::In.ReadToEnd()
$sourceBytes = [Convert]::FromBase64String($encodedSource)
$sourceText = [Text.Encoding]::UTF8.GetString($sourceBytes)
$entry = [ScriptBlock]::Create($sourceText)
& $entry
"#;
const LAUNCH_WRAPPER_PREFIX: &[u8] =
    b"\n$ErrorActionPreference = \"Stop\"\n$sourceBytes = [Convert]::FromBase64String(\"";
const LAUNCH_WRAPPER_SUFFIX: &[u8] = br#"")
$sourceText = [Text.Encoding]::UTF8.GetString($sourceBytes)
$entry = [ScriptBlock]::Create($sourceText)
$params = @{
  PythonPath = $env:BIDKING_EMBED_PYTHON
  LogDir = $env:BIDKING_EMBED_LOG_DIR
  DiagnosticProfile = $env:BIDKING_EMBED_DIAGNOSTIC_PROFILE
  NoAutoElevate = $true
  ManagedLaunch = $true
}
if ($env:BIDKING_EMBED_RESTART -eq "1") { $params.Restart = $true }
if ($env:BIDKING_EMBED_NO_TASKBAR -eq "1") { $params.NoTaskbar = $true }
& $entry @params
"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LifecycleMode {
    Launch,
    Diagnostics,
    Repair,
}

impl LifecycleMode {
    fn parse(value: &str) -> Result<Self, LifecycleError> {
        match value {
            "launch" => Ok(Self::Launch),
            "diagnostics" => Ok(Self::Diagnostics),
            "repair" => Ok(Self::Repair),
            _ => Err(LifecycleError::contract()),
        }
    }

    fn label(self) -> &'static [u8] {
        match self {
            Self::Launch => b"launcher",
            Self::Diagnostics => b"diagnostics",
            Self::Repair => b"repair",
        }
    }

    fn mutex_suffix(self) -> &'static str {
        match self {
            Self::Launch | Self::Repair => "launch",
            Self::Diagnostics => "diagnostics",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LifecycleState {
    Idle,
    Running(LifecycleMode),
}

static LIFECYCLE_STATE: Mutex<LifecycleState> = Mutex::new(LifecycleState::Idle);

#[derive(Debug)]
struct LifecycleError(&'static str);

impl LifecycleError {
    fn unavailable() -> Self {
        Self("client_lifecycle_unavailable")
    }

    fn contract() -> Self {
        Self("client_lifecycle_request_rejected")
    }

    fn busy() -> Self {
        Self("client_lifecycle_busy")
    }

    fn payload() -> Self {
        Self("client_lifecycle_payload_invalid")
    }

    fn start() -> Self {
        Self("client_lifecycle_start_failed")
    }

    fn into_py(self) -> PyErr {
        PyRuntimeError::new_err(self.0)
    }
}

struct InProcessLifecycleGuard;

impl InProcessLifecycleGuard {
    fn acquire(mode: LifecycleMode) -> Result<Self, LifecycleError> {
        let mut state = LIFECYCLE_STATE
            .lock()
            .map_err(|_| LifecycleError::busy())?;
        if *state != LifecycleState::Idle {
            return Err(LifecycleError::busy());
        }
        *state = LifecycleState::Running(mode);
        Ok(Self)
    }
}

impl Drop for InProcessLifecycleGuard {
    fn drop(&mut self) {
        if let Ok(mut state) = LIFECYCLE_STATE.lock() {
            *state = LifecycleState::Idle;
        }
    }
}

#[cfg(windows)]
struct CrossProcessLifecycleGuard(windows_sys::Win32::Foundation::HANDLE);

#[cfg(windows)]
impl CrossProcessLifecycleGuard {
    fn acquire(mode: LifecycleMode) -> Result<Self, LifecycleError> {
        use windows_sys::Win32::Foundation::{
            CloseHandle, ERROR_ALREADY_EXISTS, GetLastError,
        };
        use windows_sys::Win32::System::Threading::CreateMutexW;

        // Deliberately generation-independent: two installed package generations
        // must not enter the same lifecycle mode concurrently.
        let name = format!("Local\\BidKingClientLifecycle-{}", mode.mutex_suffix());
        let wide: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
        let handle = unsafe { CreateMutexW(std::ptr::null(), 1, wide.as_ptr()) };
        if handle == 0 {
            return Err(LifecycleError::busy());
        }
        let last_error = unsafe { GetLastError() };
        if last_error == ERROR_ALREADY_EXISTS {
            unsafe { CloseHandle(handle) };
            return Err(LifecycleError::busy());
        }
        Ok(Self(handle))
    }
}

#[cfg(windows)]
impl Drop for CrossProcessLifecycleGuard {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::ReleaseMutex;

        unsafe {
            ReleaseMutex(self.0);
            CloseHandle(self.0);
        }
    }
}

#[cfg(not(windows))]
struct CrossProcessLifecycleGuard;

#[cfg(not(windows))]
impl CrossProcessLifecycleGuard {
    fn acquire(_mode: LifecycleMode) -> Result<Self, LifecycleError> {
        Err(LifecycleError::unavailable())
    }
}

#[pyfunction]
pub(crate) fn client_lifecycle_contract() -> (u32, &'static str, bool) {
    (
        CLIENT_LIFECYCLE_ABI,
        CLIENT_LIFECYCLE_GENERATION,
        CLIENT_LIFECYCLE_EMBEDDED,
    )
}

#[pyfunction]
#[pyo3(signature = (*, abi, generation, caller, mode, package_root, python_path, diagnostic_profile, restart, no_taskbar, repair_dry_run))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_client_lifecycle(
    abi: u32,
    generation: &str,
    caller: &str,
    mode: &str,
    package_root: &str,
    python_path: &str,
    diagnostic_profile: &str,
    restart: bool,
    no_taskbar: bool,
    repair_dry_run: bool,
) -> PyResult<i32> {
    startup_timing::write_event("lifecycle", "begin", None);
    let result = run_client_lifecycle_inner(
        abi,
        generation,
        caller,
        mode,
        package_root,
        python_path,
        diagnostic_profile,
        restart,
        no_taskbar,
        repair_dry_run,
    );
    startup_timing::write_event(
        "lifecycle",
        if result.is_ok() { "ok" } else { "failed" },
        None,
    );
    result.map_err(LifecycleError::into_py)
}

#[allow(clippy::too_many_arguments)]
fn run_client_lifecycle_inner(
    abi: u32,
    generation: &str,
    caller: &str,
    mode: &str,
    package_root: &str,
    python_path: &str,
    diagnostic_profile: &str,
    restart: bool,
    no_taskbar: bool,
    repair_dry_run: bool,
) -> Result<i32, LifecycleError> {
    validate_contract(abi, generation, caller)?;
    let mode = LifecycleMode::parse(mode)?;
    let package_root = canonical_directory(package_root)?;
    let packaged_python = canonical_file(
        package_root
            .join("runtime")
            .join("python")
            .join("python.exe")
            .to_str()
            .ok_or_else(LifecycleError::contract)?,
    )?;
    let current_python = env::current_exe()
        .and_then(|path| path.canonicalize())
        .map_err(|_| LifecycleError::contract())?;
    if current_python != packaged_python {
        return Err(LifecycleError::contract());
    }
    let python_path = match mode {
        LifecycleMode::Launch | LifecycleMode::Repair => {
            let requested_python = canonical_file(python_path)?;
            if requested_python != packaged_python {
                return Err(LifecycleError::contract());
            }
            Some(requested_python)
        }
        LifecycleMode::Diagnostics => {
            if !python_path.is_empty() || restart || no_taskbar || repair_dry_run {
                return Err(LifecycleError::contract());
            }
            None
        }
    };
    match mode {
        LifecycleMode::Launch if repair_dry_run => return Err(LifecycleError::contract()),
        LifecycleMode::Repair if restart || no_taskbar => {
            return Err(LifecycleError::contract())
        }
        _ => {}
    }
    if !matches!(
        diagnostic_profile,
        "engineering" | "portable" | "public-safe" | "stable"
    ) {
        return Err(LifecycleError::contract());
    }
    let runtime_log_dir = external_runtime_log_dir(&package_root)?;

    let _in_process = InProcessLifecycleGuard::acquire(mode)?;
    let _cross_process = CrossProcessLifecycleGuard::acquire(mode)?;
    let mut payload = open_payload(mode)?;
    let result = execute_payload(
        mode,
        &mut payload,
        &package_root,
        &runtime_log_dir,
        python_path.as_deref(),
        diagnostic_profile,
        restart,
        no_taskbar,
        repair_dry_run,
    );
    payload.fill(0);
    result
}

fn validate_contract(
    abi: u32,
    generation: &str,
    caller: &str,
) -> Result<(), LifecycleError> {
    if !CLIENT_LIFECYCLE_EMBEDDED {
        return Err(LifecycleError::unavailable());
    }
    if abi != CLIENT_LIFECYCLE_ABI
        || generation != CLIENT_LIFECYCLE_GENERATION
        || caller != EXPECTED_CALLER
    {
        return Err(LifecycleError::contract());
    }
    Ok(())
}

fn canonical_directory(value: &str) -> Result<PathBuf, LifecycleError> {
    let path = canonical_path(value)?;
    if !path.is_dir() {
        return Err(LifecycleError::contract());
    }
    Ok(path)
}

fn canonical_file(value: &str) -> Result<PathBuf, LifecycleError> {
    let path = canonical_path(value)?;
    if !path.is_file() {
        return Err(LifecycleError::contract());
    }
    Ok(path)
}

fn canonical_path(value: &str) -> Result<PathBuf, LifecycleError> {
    if value.is_empty() || value.len() > 32_000 || value.contains('\0') {
        return Err(LifecycleError::contract());
    }
    let path = Path::new(value);
    if !path.is_absolute() {
        return Err(LifecycleError::contract());
    }
    path.canonicalize().map_err(|_| LifecycleError::contract())
}

fn external_runtime_log_dir(package_root: &Path) -> Result<PathBuf, LifecycleError> {
    let local_app_data = env::var_os("LOCALAPPDATA")
        .filter(|value| !value.is_empty())
        .ok_or_else(LifecycleError::contract)?;
    let local_app_data = canonical_directory(
        local_app_data
            .to_str()
            .ok_or_else(LifecycleError::contract)?,
    )?;
    let runtime_log_dir = local_app_data
        .join("BidKingLive")
        .join("logs")
        .join("live");
    let package_folded = package_root.to_string_lossy().to_lowercase();
    let log_folded = runtime_log_dir.to_string_lossy().to_lowercase();
    let package_prefix = format!(
        "{}{}",
        package_folded.trim_end_matches(|ch| ch == '/' || ch == '\\'),
        std::path::MAIN_SEPARATOR,
    );
    if log_folded == package_folded || log_folded.starts_with(&package_prefix) {
        return Err(LifecycleError::contract());
    }
    Ok(runtime_log_dir)
}

fn open_payload(mode: LifecycleMode) -> Result<Vec<u8>, LifecycleError> {
    let (sealed, expected_hash) = match mode {
        LifecycleMode::Launch => (
            CLIENT_LIFECYCLE_LAUNCHER_SEALED,
            CLIENT_LIFECYCLE_LAUNCHER_SHA256,
        ),
        LifecycleMode::Diagnostics => (
            CLIENT_LIFECYCLE_DIAGNOSTICS_SEALED,
            CLIENT_LIFECYCLE_DIAGNOSTICS_SHA256,
        ),
        LifecycleMode::Repair => (
            CLIENT_LIFECYCLE_REPAIR_SEALED,
            CLIENT_LIFECYCLE_REPAIR_SHA256,
        ),
    };
    if sealed.is_empty() {
        return Err(LifecycleError::unavailable());
    }
    let generation = hex::decode(CLIENT_LIFECYCLE_GENERATION)
        .map_err(|_| LifecycleError::payload())?;
    if generation.len() != 32 {
        return Err(LifecycleError::payload());
    }
    let mut generation_bytes = [0u8; 32];
    generation_bytes.copy_from_slice(&generation);
    let payload = xor_payload(sealed, &generation_bytes, mode.label());
    let actual_hash: [u8; 32] = Sha256::digest(&payload).into();
    if actual_hash != expected_hash {
        return Err(LifecycleError::payload());
    }
    Ok(payload)
}

fn xor_payload(payload: &[u8], generation: &[u8; 32], label: &[u8]) -> Vec<u8> {
    let mut output = Vec::with_capacity(payload.len());
    for (counter, chunk) in payload.chunks(32).enumerate() {
        let mut digest = Sha256::new();
        digest.update(SEAL_DOMAIN);
        digest.update(generation);
        digest.update((label.len() as u64).to_le_bytes());
        digest.update(label);
        digest.update((counter as u64).to_le_bytes());
        let key: [u8; 32] = digest.finalize().into();
        output.extend(chunk.iter().zip(key).map(|(byte, mask)| *byte ^ mask));
    }
    output
}

fn execute_payload(
    mode: LifecycleMode,
    payload: &mut Vec<u8>,
    package_root: &Path,
    runtime_log_dir: &Path,
    python_path: Option<&Path>,
    diagnostic_profile: &str,
    restart: bool,
    no_taskbar: bool,
    repair_dry_run: bool,
) -> Result<i32, LifecycleError> {
    let mut source = match mode {
        LifecycleMode::Launch => {
            let mut encoded = base64_encode(payload)?;
            let mut wrapper = Vec::with_capacity(
                LAUNCH_WRAPPER_PREFIX.len() + encoded.len() + LAUNCH_WRAPPER_SUFFIX.len(),
            );
            wrapper.extend_from_slice(LAUNCH_WRAPPER_PREFIX);
            wrapper.extend_from_slice(&encoded);
            wrapper.extend_from_slice(LAUNCH_WRAPPER_SUFFIX);
            encoded.fill(0);
            wrapper
        }
        LifecycleMode::Diagnostics | LifecycleMode::Repair => std::mem::take(payload),
    };
    let result = run_powershell(
        mode,
        &source,
        package_root,
        runtime_log_dir,
        python_path,
        diagnostic_profile,
        restart,
        no_taskbar,
        repair_dry_run,
    );
    source.fill(0);
    result
}

fn run_powershell(
    mode: LifecycleMode,
    source: &[u8],
    package_root: &Path,
    runtime_log_dir: &Path,
    python_path: Option<&Path>,
    diagnostic_profile: &str,
    restart: bool,
    no_taskbar: bool,
    repair_dry_run: bool,
) -> Result<i32, LifecycleError> {
    let system_root = env::var_os("WINDIR").ok_or_else(LifecycleError::start)?;
    let powershell = Path::new(&system_root)
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe");
    if !powershell.is_file() {
        return Err(LifecycleError::start());
    }
    let root_executable = canonical_file(
        package_root
            .join("BidKingLive.exe")
            .to_str()
            .ok_or_else(LifecycleError::contract)?,
    )?;

    let mut bootstrap_utf16 = Vec::with_capacity(POWERSHELL_STDIN_BOOTSTRAP.len() * 2);
    for unit in POWERSHELL_STDIN_BOOTSTRAP.encode_utf16() {
        bootstrap_utf16.extend_from_slice(&unit.to_le_bytes());
    }
    let mut encoded_bootstrap = base64_encode(&bootstrap_utf16)?;
    bootstrap_utf16.fill(0);
    let bootstrap_argument =
        String::from_utf8(encoded_bootstrap.clone()).map_err(|_| LifecycleError::start())?;
    encoded_bootstrap.fill(0);
    let mut encoded_source = base64_encode(source)?;

    let mut command = Command::new(powershell);
    command
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            &bootstrap_argument,
        ])
        .current_dir(package_root)
        .env("BIDKING_PACKAGE_ROOT", package_root)
        .env("BIDKING_EMBED_LOG_DIR", runtime_log_dir)
        .env("BIDKING_EMBED_DIAGNOSTIC_PROFILE", diagnostic_profile)
        .env("BIDKING_EMBED_RESTART", if restart { "1" } else { "0" })
        .env(
            "BIDKING_EMBED_NO_TASKBAR",
            if no_taskbar { "1" } else { "0" },
        )
        .env(
            "BIDKING_EMBED_REPAIR_DRY_RUN",
            if repair_dry_run { "1" } else { "0" },
        )
        .env("BIDKING_EMBED_ROOT_EXE", root_executable)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::piped());
    if mode == LifecycleMode::Diagnostics {
        // This child has no interactive console; do not rely on a flag surviving
        // an earlier shell/UAC boundary to keep the diagnostic lease finite.
        command.env("BIDKING_DIAG_NONINTERACTIVE", "1");
    }
    if let Some(path) = python_path {
        command.env("BIDKING_EMBED_PYTHON", path);
    } else {
        command.env_remove("BIDKING_EMBED_PYTHON");
    }
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    startup_timing::write_event("powershell_spawn", "begin", None);
    let mut child = match command.spawn() {
        Ok(child) => {
            startup_timing::write_event("powershell_spawn", "ok", None);
            child
        }
        Err(_) => {
            startup_timing::write_event("powershell_spawn", "failed", None);
            return Err(LifecycleError::start());
        }
    };
    let write_result = child
        .stdin
        .take()
        .ok_or_else(LifecycleError::start)
        .and_then(|mut stdin| {
            stdin
                .write_all(&encoded_source)
                .map_err(|_| LifecycleError::start())
        });
    encoded_source.fill(0);
    if let Err(error) = write_result {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    let status = match child.wait() {
        Ok(status) => status,
        Err(_) => {
            // The retained Child is this invocation's PowerShell object.  Do
            // not fall back to a name/PID search or kill its descendant tree.
            // Its missing terminal receipt remains unknown to the root UI.
            let _ = child.kill();
            let _ = child.wait();
            return Err(LifecycleError::start());
        }
    };
    Ok(status.code().unwrap_or(1))
}

fn base64_encode(input: &[u8]) -> Result<Vec<u8>, LifecycleError> {
    const TABLE: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let capacity = input
        .len()
        .checked_add(2)
        .and_then(|value| value.checked_div(3))
        .and_then(|value| value.checked_mul(4))
        .ok_or_else(LifecycleError::payload)?;
    let mut output = Vec::with_capacity(capacity);
    for chunk in input.chunks(3) {
        let first = chunk[0];
        let second = *chunk.get(1).unwrap_or(&0);
        let third = *chunk.get(2).unwrap_or(&0);
        output.push(TABLE[(first >> 2) as usize]);
        output.push(TABLE[(((first & 0x03) << 4) | (second >> 4)) as usize]);
        output.push(if chunk.len() > 1 {
            TABLE[(((second & 0x0f) << 2) | (third >> 6)) as usize]
        } else {
            b'='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(third & 0x3f) as usize]
        } else {
            b'='
        });
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_vectors_match_rfc4648() {
        assert_eq!(base64_encode(b"").unwrap(), b"");
        assert_eq!(base64_encode(b"f").unwrap(), b"Zg==");
        assert_eq!(base64_encode(b"fo").unwrap(), b"Zm8=");
        assert_eq!(base64_encode(b"foo").unwrap(), b"Zm9v");
    }

    #[test]
    fn payload_bitflip_is_not_hash_equivalent() {
        let mut payload = b"private lifecycle".to_vec();
        let expected: [u8; 32] = Sha256::digest(&payload).into();
        payload[3] ^= 0x40;
        let actual: [u8; 32] = Sha256::digest(&payload).into();
        assert_ne!(actual, expected);
    }
}
