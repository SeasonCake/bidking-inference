//! Best-effort, privacy-safe startup timing events.
//!
//! Timing is observability only: every failure returns `false` and must never
//! participate in integrity, lifecycle, certificate, process, or UI decisions.

use serde::{Deserialize, Serialize};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const SCHEMA: &str = "bidking_startup_timing_v1";
const SCHEMA_VERSION: u32 = 1;
const RUN_ID_ENV: &str = "BIDKING_STARTUP_TIMING_RUN_ID";
const BASE_TICK_ENV: &str = "BIDKING_STARTUP_TIMING_BASE_TICK_MS";
const DIRECTORY_ENV: &str = "BIDKING_STARTUP_TIMING_DIR";
const TOPOLOGY_ENV: &str = "BIDKING_STARTUP_TIMING_TOPOLOGY";
static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

pub(crate) const EVENTS: &[&str] = &[
    "root_entry",
    "preload",
    "python_spawn",
    "kernel_import",
    "kernel_manifest",
    "kernel_coverage",
    "kernel_namespace",
    "acl_inventory",
    "acl_journal",
    "acl_apply",
    "acl_attest",
    "integrity_recheck",
    "lifecycle",
    "powershell_spawn",
    "powershell_launch",
    "powershell_generation",
    "powershell_component",
    "powershell_certificate",
    "powershell_dependency",
    "powershell_process_identity",
    "powershell_monitor",
    "powershell_overlay",
    "tk_map",
    "tk_after_idle",
    "startup_guard",
    "startup_terminal",
    "acl_restore",
    "root_exit",
];
pub(crate) const RESULTS: &[&str] = &["begin", "ok", "failed", "skipped", "unavailable"];
pub(crate) const TOPOLOGIES: &[&str] = &[
    "unknown",
    "cold_start",
    "current_current",
    "current_absent",
    "current_stale",
    "current_unsafe",
    "rapid_reentry",
    "diagnostics",
    "repair",
];

#[derive(Serialize)]
struct TimingEvent<'a> {
    schema: &'static str,
    schema_version: u32,
    run_id: &'a str,
    event: &'a str,
    result: &'a str,
    topology: &'a str,
    elapsed_ms: u64,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredTimingEvent {
    schema: String,
    schema_version: u32,
    run_id: String,
    event: String,
    result: String,
    topology: String,
    elapsed_ms: u64,
}

fn valid_run_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn event_directory() -> Option<PathBuf> {
    let value = env::var_os(DIRECTORY_ENV)?;
    if value.is_empty() {
        return None;
    }
    let path = PathBuf::from(value);
    (path.is_absolute() && path.components().count() > 1).then_some(path)
}

fn resolve_persisted_topology(directory: &Path, run_id: &str) -> Option<String> {
    let prefix = format!("{run_id}--");
    let mut resolved: Option<String> = None;
    let mut json_files = 0usize;
    for entry in fs::read_dir(directory).ok()?.flatten() {
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if !name.ends_with(".json") {
            continue;
        }
        json_files = json_files.checked_add(1)?;
        if json_files > 1280 {
            return None;
        }
        if !name.starts_with(&prefix) {
            continue;
        }
        let metadata = entry.metadata().ok()?;
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 1024 {
            return None;
        }
        let encoded = fs::read(entry.path()).ok()?;
        let stored = serde_json::from_slice::<StoredTimingEvent>(&encoded).ok()?;
        if serde_json::to_vec(&stored).ok()? != encoded
            || stored.schema != SCHEMA
            || stored.schema_version != SCHEMA_VERSION
            || stored.run_id != run_id
            || !EVENTS.contains(&stored.event.as_str())
            || !RESULTS.contains(&stored.result.as_str())
            || !TOPOLOGIES.contains(&stored.topology.as_str())
            || stored.elapsed_ms > 7 * 24 * 60 * 60 * 1000
            || name != format!("{}--{}--{}.json", run_id, stored.event, stored.result)
        {
            return None;
        }
        if stored.topology == "unknown" {
            continue;
        }
        if resolved
            .as_deref()
            .is_some_and(|current| current != stored.topology.as_str())
        {
            return None;
        }
        resolved = Some(stored.topology);
    }
    resolved
}

#[cfg(windows)]
fn tick_count_ms() -> u64 {
    unsafe { windows_sys::Win32::System::SystemInformation::GetTickCount64() }
}

#[cfg(not(windows))]
fn tick_count_ms() -> u64 {
    0
}

pub(crate) fn write_event(event: &str, result: &str, topology: Option<&str>) -> bool {
    write_event_inner(event, result, topology).is_some()
}

fn write_event_inner(event: &str, result: &str, topology: Option<&str>) -> Option<()> {
    if !EVENTS.contains(&event) || !RESULTS.contains(&result) {
        return None;
    }
    let mut topology = topology
        .map(str::to_owned)
        .or_else(|| env::var(TOPOLOGY_ENV).ok())
        .filter(|value| TOPOLOGIES.contains(&value.as_str()))
        .unwrap_or_else(|| "unknown".to_string());
    let run_id = env::var(RUN_ID_ENV).ok()?;
    if !valid_run_id(&run_id) {
        return None;
    }
    let base_tick = env::var(BASE_TICK_ENV).ok()?.parse::<u64>().ok()?;
    let current_tick = tick_count_ms();
    if current_tick < base_tick {
        return None;
    }
    let elapsed_ms = current_tick - base_tick;
    if elapsed_ms > 7 * 24 * 60 * 60 * 1000 {
        return None;
    }
    let directory = event_directory()?;
    fs::create_dir_all(&directory).ok()?;
    let close_event = (event == "lifecycle" && result != "begin") || event == "acl_restore";
    if topology == "unknown" && close_event {
        if let Some(persisted) = resolve_persisted_topology(&directory, &run_id) {
            topology = persisted;
        }
    }
    let final_path = directory.join(format!("{run_id}--{event}--{result}.json"));
    let counter = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temporary = directory.join(format!(".{run_id}--{event}--{result}--{counter}.tmp"));
    let payload = TimingEvent {
        schema: SCHEMA,
        schema_version: SCHEMA_VERSION,
        run_id: &run_id,
        event,
        result,
        topology: &topology,
        elapsed_ms,
    };
    let encoded = serde_json::to_vec(&payload).ok()?;
    if encoded.len() > 1024 {
        return None;
    }
    let write_result = (|| {
        let mut stream = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        stream.write_all(&encoded)?;
        drop(stream);
        fs::rename(&temporary, &final_path)
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary);
        return None;
    }
    Some(())
}
