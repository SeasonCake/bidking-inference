//! Pre-Python release bootstrap.
//!
//! The executable verifies the offline Ed25519 release manifest and retains
//! non-share-write/non-share-delete handles for every preload trust root until
//! the product launcher returns.  Only then may Python/import execute.

#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

#[cfg(windows)]
mod startup_feedback;

#[cfg(windows)]
mod windows_bootstrap {
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};
    use serde::{Deserialize, Serialize};
    use sha2::{Digest, Sha256};
    use std::collections::{HashMap, HashSet};
    use std::fs::{self, File, Metadata, OpenOptions};
    use std::io::{Read, Write};
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use std::path::{Component, Path, PathBuf};
    use std::process::Command;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
    };
    use windows_sys::Win32::System::SystemInformation::GetTickCount64;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MB_ICONERROR, MB_OK, MB_SETFOREGROUND, MessageBoxW,
    };

    include!(concat!(env!("OUT_DIR"), "/embedded_trust.rs"));

    const MANIFEST_MEMBER: &str = "integrity/local_integrity_manifest.json";
    const SIGNATURE_MEMBER: &str = "integrity/local_integrity_manifest.sig";
    const MANIFEST_SCHEMA: &str = "bidking_local_integrity_manifest_v1";
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    const FAILURE_LOG_SCHEMA: &str = "bidking_public_startup_failure_v1";
    const TIMING_SCHEMA: &str = "bidking_startup_timing_v1";
    const TIMING_DIRECTORY: &str = "startup-timing-v1";
    const TIMING_RUNS_RETAINED: usize = 8;
    const TIMING_EVENTS: &[&str] = &[
        "root_entry", "preload", "python_spawn", "kernel_import",
        "kernel_manifest", "kernel_coverage", "kernel_namespace", "acl_inventory",
        "acl_journal", "acl_apply", "acl_attest", "integrity_recheck", "lifecycle",
        "powershell_spawn", "powershell_launch", "powershell_generation",
        "powershell_component", "powershell_certificate", "powershell_dependency",
        "powershell_process_identity", "powershell_monitor", "powershell_overlay",
        "tk_map", "tk_after_idle", "startup_guard", "startup_terminal",
        "acl_restore", "root_exit",
    ];
    const TIMING_RESULTS: &[&str] = &["begin", "ok", "failed", "skipped", "unavailable"];
    const TIMING_TOPOLOGIES: &[&str] = &[
        "unknown", "cold_start", "current_current", "current_absent",
        "current_stale", "current_unsafe", "rapid_reentry", "diagnostics", "repair",
    ];
    static TIMING_TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Manifest {
        schema: String,
        manifest_version: u32,
        public_key_sha256: String,
        release_id: String,
        entries: Vec<Entry>,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Entry {
        path: String,
        role: String,
        bytes: u64,
        sha256: String,
    }

    struct StableFile {
        bytes: Vec<u8>,
        handle: File,
    }

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

    fn valid_timing_run_id(value: &str) -> bool {
        value.len() == 32
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    }

    fn timing_owned_name(name: &str) -> Option<&str> {
        let stem = name.strip_suffix(".json")?;
        let mut parts = stem.split("--");
        let run_id = parts.next()?;
        let event = parts.next()?;
        let result = parts.next()?;
        if parts.next().is_some()
            || !valid_timing_run_id(run_id)
            || !TIMING_EVENTS.contains(&event)
            || !TIMING_RESULTS.contains(&result)
        {
            return None;
        }
        Some(run_id)
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
                || stored.schema != TIMING_SCHEMA
                || stored.schema_version != 1
                || stored.run_id != run_id
                || !TIMING_EVENTS.contains(&stored.event.as_str())
                || !TIMING_RESULTS.contains(&stored.result.as_str())
                || !TIMING_TOPOLOGIES.contains(&stored.topology.as_str())
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

    fn rotate_timing_runs(directory: &Path) {
        let Ok(entries) = fs::read_dir(directory) else { return };
        let mut newest: HashMap<String, SystemTime> = HashMap::new();
        let mut owned = Vec::new();
        for entry in entries.flatten() {
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                continue;
            };
            let Some(run_id) = timing_owned_name(name).map(str::to_owned) else {
                continue;
            };
            let modified = entry
                .metadata()
                .and_then(|metadata| metadata.modified())
                .unwrap_or(UNIX_EPOCH);
            newest
                .entry(run_id.clone())
                .and_modify(|current| *current = (*current).max(modified))
                .or_insert(modified);
            owned.push((entry.path(), run_id));
        }
        let mut runs = newest.into_iter().collect::<Vec<_>>();
        runs.sort_by(|left, right| right.1.cmp(&left.1));
        let retained = runs
            .into_iter()
            .take(TIMING_RUNS_RETAINED.saturating_sub(1))
            .map(|(run_id, _)| run_id)
            .collect::<HashSet<_>>();
        for (path, run_id) in owned {
            if !retained.contains(&run_id) {
                let _ = fs::remove_file(path);
            }
        }
    }

    pub(crate) fn start_timing_context() {
        let base_tick = unsafe { GetTickCount64() };
        let Some(local_app_data) = std::env::var_os("LOCALAPPDATA").filter(|v| !v.is_empty()) else {
            return;
        };
        let directory = PathBuf::from(local_app_data)
            .join("BidKingLive")
            .join("logs")
            .join("live")
            .join(TIMING_DIRECTORY);
        if fs::create_dir_all(&directory).is_err() {
            return;
        }
        let wall_nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let mut digest = Sha256::new();
        digest.update(b"bidking-startup-timing-run-v1\0");
        digest.update(base_tick.to_le_bytes());
        digest.update(wall_nanos.to_le_bytes());
        digest.update(std::process::id().to_le_bytes());
        let run_id = hex::encode(digest.finalize())[..32].to_string();
        rotate_timing_runs(&directory);
        unsafe {
            std::env::set_var("BIDKING_STARTUP_TIMING_RUN_ID", &run_id);
            std::env::set_var("BIDKING_STARTUP_TIMING_BASE_TICK_MS", base_tick.to_string());
            std::env::set_var("BIDKING_STARTUP_TIMING_DIR", &directory);
            std::env::set_var("BIDKING_STARTUP_TIMING_TOPOLOGY", "unknown");
        }
        let _ = write_timing_event("root_entry", "begin", "unknown");
    }

    pub(crate) fn write_timing_event(event: &str, result: &str, topology: &str) -> bool {
        let outcome = (|| -> Option<()> {
            if !TIMING_EVENTS.contains(&event)
                || !TIMING_RESULTS.contains(&result)
                || !TIMING_TOPOLOGIES.contains(&topology)
            {
                return None;
            }
            let run_id = std::env::var("BIDKING_STARTUP_TIMING_RUN_ID").ok()?;
            if !valid_timing_run_id(&run_id) {
                return None;
            }
            let base_tick = std::env::var("BIDKING_STARTUP_TIMING_BASE_TICK_MS")
                .ok()?
                .parse::<u64>()
                .ok()?;
            let current_tick = unsafe { GetTickCount64() };
            let elapsed_ms = current_tick.checked_sub(base_tick)?;
            if elapsed_ms > 7 * 24 * 60 * 60 * 1000 {
                return None;
            }
            let directory = PathBuf::from(std::env::var_os("BIDKING_STARTUP_TIMING_DIR")?);
            if !directory.is_absolute() {
                return None;
            }
            let mut topology = topology.to_string();
            let close_event =
                (event == "python_spawn" && result != "begin") || event == "root_exit";
            if topology == "unknown" && close_event {
                if let Some(persisted) = resolve_persisted_topology(&directory, &run_id) {
                    topology = persisted;
                }
            }
            let payload = TimingEvent {
                schema: TIMING_SCHEMA,
                schema_version: 1,
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
            let final_path = directory.join(format!("{run_id}--{event}--{result}.json"));
            let counter = TIMING_TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
            let temporary = directory.join(format!(".{run_id}--{event}--{result}--{counter}.tmp"));
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
        })();
        outcome.is_some()
    }

    fn file_identity(file: &File) -> Result<(u64, u64), &'static str> {
        let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        let ok = unsafe {
            GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut info)
        };
        if ok == 0 {
            return Err("bootstrap_file_identity_unavailable");
        }
        Ok((
            u64::from(info.dwVolumeSerialNumber),
            (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
        ))
    }

    fn safe_member(relative: &str) -> Result<PathBuf, &'static str> {
        if relative.is_empty() || relative.contains('\\') {
            return Err("bootstrap_member_invalid");
        }
        let path = Path::new(relative);
        if path.is_absolute()
            || path
                .components()
                .any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err("bootstrap_member_invalid");
        }
        Ok(path.to_path_buf())
    }

    fn reject_reparse(metadata: &Metadata) -> Result<(), &'static str> {
        if metadata.file_type().is_symlink()
            || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        {
            return Err("bootstrap_reparse_rejected");
        }
        Ok(())
    }

    fn read_stable(root: &Path, relative: &str) -> Result<StableFile, &'static str> {
        let path = root.join(safe_member(relative)?);
        let before = fs::symlink_metadata(&path).map_err(|_| "bootstrap_file_missing")?;
        reject_reparse(&before)?;
        if !before.is_file() {
            return Err("bootstrap_file_type_invalid");
        }
        let mut handle = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&path)
            .map_err(|_| "bootstrap_file_open_failed")?;
        let opened = handle
            .metadata()
            .map_err(|_| "bootstrap_file_identity_unavailable")?;
        let opened_identity = file_identity(&handle)?;
        let mut bytes = Vec::new();
        handle
            .read_to_end(&mut bytes)
            .map_err(|_| "bootstrap_file_read_failed")?;
        let after_handle = handle
            .metadata()
            .map_err(|_| "bootstrap_file_identity_unavailable")?;
        let after_path =
            fs::symlink_metadata(&path).map_err(|_| "bootstrap_file_identity_changed")?;
        reject_reparse(&after_path)?;
        let rebound = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(&path)
            .map_err(|_| "bootstrap_file_identity_changed")?;
        if file_identity(&handle)? != opened_identity
            || file_identity(&rebound)? != opened_identity
            || opened.len() != bytes.len() as u64
            || after_handle.len() != opened.len()
            || after_path.len() != opened.len()
        {
            return Err("bootstrap_file_identity_changed");
        }
        Ok(StableFile { bytes, handle })
    }

    fn decode_base64(input: &[u8]) -> Result<Vec<u8>, &'static str> {
        fn digit(byte: u8) -> Option<u8> {
            match byte {
                b'A'..=b'Z' => Some(byte - b'A'),
                b'a'..=b'z' => Some(byte - b'a' + 26),
                b'0'..=b'9' => Some(byte - b'0' + 52),
                b'+' => Some(62),
                b'/' => Some(63),
                _ => None,
            }
        }
        let compact: Vec<u8> = input
            .iter()
            .copied()
            .filter(|byte| !byte.is_ascii_whitespace())
            .collect();
        if compact.is_empty() || compact.len() % 4 != 0 {
            return Err("bootstrap_signature_encoding_invalid");
        }
        let mut output = Vec::with_capacity(compact.len() / 4 * 3);
        for chunk in compact.chunks_exact(4) {
            let first = digit(chunk[0]).ok_or("bootstrap_signature_encoding_invalid")?;
            let second = digit(chunk[1]).ok_or("bootstrap_signature_encoding_invalid")?;
            output.push((first << 2) | (second >> 4));
            if chunk[2] != b'=' {
                let third = digit(chunk[2]).ok_or("bootstrap_signature_encoding_invalid")?;
                output.push((second << 4) | (third >> 2));
                if chunk[3] != b'=' {
                    let fourth =
                        digit(chunk[3]).ok_or("bootstrap_signature_encoding_invalid")?;
                    output.push((third << 6) | fourth);
                }
            } else if chunk[3] != b'=' {
                return Err("bootstrap_signature_encoding_invalid");
            }
        }
        Ok(output)
    }

    fn verify_preload_roots(
        root: &Path,
        executable: &Path,
    ) -> Result<(Vec<File>, String, String), &'static str> {
        let public_key = INTEGRITY_PUBLIC_KEY.ok_or("bootstrap_public_key_missing")?;
        let manifest_file = read_stable(root, MANIFEST_MEMBER)?;
        let signature_file = read_stable(root, SIGNATURE_MEMBER)?;
        let signature = Signature::from_slice(&decode_base64(&signature_file.bytes)?)
            .map_err(|_| "bootstrap_signature_invalid")?;
        let verifying_key =
            VerifyingKey::from_bytes(&public_key).map_err(|_| "bootstrap_public_key_invalid")?;
        verifying_key
            .verify(&manifest_file.bytes, &signature)
            .map_err(|_| "bootstrap_signature_invalid")?;
        let manifest: Manifest = serde_json::from_slice(&manifest_file.bytes)
            .map_err(|_| "bootstrap_manifest_invalid")?;
        if manifest.schema != MANIFEST_SCHEMA
            || manifest.manifest_version != 1
            || manifest.release_id.is_empty()
            || manifest.public_key_sha256 != hex::encode(Sha256::digest(public_key))
            || manifest.entries.is_empty()
        {
            return Err("bootstrap_manifest_invalid");
        }
        let mut entries = HashMap::new();
        for entry in manifest.entries {
            let path = safe_member(&entry.path)?;
            if entry.role.is_empty()
                || entry.sha256.len() != 64
                || entries.insert(path.to_string_lossy().to_string(), entry).is_some()
            {
                return Err("bootstrap_manifest_invalid");
            }
        }
        let mut required = HashSet::from([
            "BidKingLive.exe".to_string(),
            "runtime/python/python.exe".to_string(),
            "runtime/python/python313.dll".to_string(),
            "runtime/python/python313.zip".to_string(),
            "runtime/python/python313._pth".to_string(),
        ]);
        let kernel = entries
            .keys()
            .filter(|path| path.starts_with("src/bidking_kernel") && path.ends_with(".pyd"))
            .cloned()
            .collect::<Vec<_>>();
        let launcher = entries
            .keys()
            .filter(|path| {
                path.starts_with("scripts/_client_launcher") && path.ends_with(".pyd")
            })
            .cloned()
            .collect::<Vec<_>>();
        if kernel.len() != 1 || launcher.len() != 1 {
            return Err("bootstrap_preload_inventory_invalid");
        }
        required.insert(kernel[0].clone());
        required.insert(launcher[0].clone());
        let mut handles = vec![manifest_file.handle, signature_file.handle];
        for relative in required {
            let entry = entries
                .get(&relative)
                .ok_or("bootstrap_preload_member_missing")?;
            let stable = read_stable(root, &relative)?;
            if stable.bytes.len() as u64 != entry.bytes
                || hex::encode(Sha256::digest(&stable.bytes)) != entry.sha256
            {
                return Err("bootstrap_preload_identity_mismatch");
            }
            if relative == "BidKingLive.exe" {
                let running = OpenOptions::new()
                    .read(true)
                    .share_mode(FILE_SHARE_READ)
                    .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
                    .open(executable)
                    .map_err(|_| "bootstrap_self_identity_mismatch")?;
                if file_identity(&running)? != file_identity(&stable.handle)? {
                    return Err("bootstrap_self_identity_mismatch");
                }
                handles.push(running);
            }
            handles.push(stable.handle);
        }
        Ok((handles, kernel[0].clone(), launcher[0].clone()))
    }

    pub(crate) fn product_failure_from_exit(code: i32) -> Option<&'static str> {
        match code {
            101 => Some("integrity_member_path_invalid"),
            102 => Some("integrity_reparse_or_symlink_rejected"),
            103 => Some("integrity_file_open_failed"),
            104 => Some("integrity_stream_namespace_invalid"),
            105 => Some("integrity_stream_inventory_unavailable"),
            106 => Some("integrity_stream_read_failed"),
            107 => Some("integrity_stream_identity_changed"),
            108 => Some("integrity_file_identity_unavailable"),
            109 => Some("integrity_file_missing"),
            110 => Some("integrity_file_type_invalid"),
            111 => Some("integrity_named_stream_rejected"),
            112 => Some("integrity_directory_missing"),
            113 => Some("integrity_directory_type_invalid"),
            114 => Some("integrity_directory_open_failed"),
            115 => Some("integrity_directory_identity_changed"),
            116 => Some("integrity_directory_identity_unavailable"),
            117 => Some("integrity_file_read_failed"),
            118 => Some("integrity_file_identity_changed"),
            119 => Some("integrity_coverage_invalid"),
            120 => Some("integrity_coverage_unreadable"),
            121 => Some("integrity_namespace_unreadable"),
            122 => Some("integrity_namespace_invalid"),
            123 => Some("integrity_member_identity_collision"),
            124 => Some("integrity_signature_encoding_invalid"),
            125 => Some("integrity_public_key_missing"),
            126 => Some("integrity_signature_invalid"),
            127 => Some("integrity_public_key_invalid"),
            128 => Some("integrity_manifest_invalid"),
            129 => Some("integrity_file_size_mismatch"),
            130 => Some("integrity_file_hash_mismatch"),
            131 => Some("integrity_coverage_mismatch"),
            132 => Some("integrity_handle_retention_failed"),
            133 => Some("integrity_namespace_retention_failed"),
            134 => Some("integrity_namespace_not_verified"),
            135 => Some("integrity_namespace_root_changed"),
            136 => Some("integrity_namespace_changed"),
            137 => Some("integrity_stream_namespace_changed"),
            138 => Some("integrity_handle_release_failed"),
            180 => Some("monitor_foreign_lock"),
            181 => Some("monitor_identity_unknown"),
            182 => Some("monitor_old_package_game_active"),
            183 => Some("monitor_old_package_changed"),
            184 => Some("monitor_old_package_stop_failed"),
            185 => Some("monitor_old_package_lock_changed"),
            186 => Some("monitor_old_package_conflict"),
            187 => Some("monitor_foreign_process"),
            188 => Some("package_generation_identity_component_missing"),
            189 => Some("package_generation_identity_unavailable"),
            190 => Some("cert_store_write_denied"),
            191 => Some("policy_locked"),
            192 => Some("profile_ephemeral"),
            193 => Some("uac_disabled"),
            194 => Some("component_write_denied"),
            195 => Some("restore_disk_suspected"),
            196 => Some("interactive_user_mismatch"),
            197 => Some("component_execution_blocked"),
            198 => Some("driver_load_denied"),
            199 => Some("package_root_unsupported"),
            200 => Some("foreign_cert_or_state"),
            201 => Some("user_cancelled"),
            202 => Some("unknown"),
            203 => Some("wss_same_path_mixed_generation"),
            204 => Some("wss_stale_product_service_conflict"),
            205 => Some("wss_stale_product_service_repaired_restart_required"),
            206 => Some("overlay_spawn_failed"),
            207 => Some("overlay_exception"),
            208 => Some("overlay_exited_cleanly"),
            209 => Some("monitor_exit"),
            210 => Some("process_query_unknown"),
            211 => Some("startup_cleanup_incomplete"),
            _ => None,
        }
    }

    fn run_product(
        root: &Path,
        kernel: &str,
        launcher: &str,
    ) -> Result<i32, &'static str> {
        const BOOTSTRAP: &str = r#"
import importlib.util, sys
root, kernel_path, launcher_path, mode, *forwarded = sys.argv[1:]
_MODULE_MISSING = object()
def load_exact(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("bootstrap_exact_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name, _MODULE_MISSING)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is _MODULE_MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module
kernel = None
timing_topology = "diagnostics" if mode == "diagnostics" else ("repair" if mode == "repair" else "unknown")
def timing_event(event, result):
    try:
        kernel.startup_timing_event(event, result, timing_topology)
    except BaseException:
        # Observability is never a safety or availability input.
        pass
namespace_lease = None
namespace_guard_exception_types = ()
namespace_lease_started = False
managed_launch_entered = False
managed_exit_code = 1
primary_error = None
primary_exit_code = None
acl_restore_state = "not_acquired"
acl_restore_failed_count = None
acl_restore_attempted_count = None
handle_release_state = "unknown"

def write_cleanup_receipt():
    # This bounded, per-root-run observation is display-only.  Never consume it
    # as process ownership, an ACL descriptor, or permission to mutate anything.
    if mode != "launch":
        return
    try:
        import json, os, re
        from pathlib import Path
        run_id = os.environ.get("BIDKING_STARTUP_TIMING_RUN_ID", "")
        directory = Path(os.environ.get("BIDKING_STARTUP_TIMING_DIR", ""))
        if not re.fullmatch("[0-9a-f]{32}", run_id) or not directory.is_absolute():
            return
        child = {}
        child_path = directory / (run_id + "--cleanup-powershell.json")
        try:
            if child_path.stat().st_size <= 4096 and not child_path.is_symlink():
                with child_path.open("rb") as stream:
                    encoded_child = stream.read(4097)
                if len(encoded_child) > 4096:
                    raise ValueError("cleanup observation exceeds bound")
                candidate = json.loads(encoded_child.decode("utf-8-sig"))
                if (type(candidate) is dict and candidate.get("schema") == "bidking_startup_cleanup_v1"
                        and candidate.get("run_id") == run_id and candidate.get("phase") == "powershell"):
                    child = candidate
        except (OSError, ValueError, UnicodeError):
            pass
        states = {"not_created", "exited", "reclaimed", "alive", "reused_preserved",
                  "preserved_unknown", "preserved_changed", "preserved_game", "cleanup_failed"}
        if not managed_launch_entered:
            # The live control flow, not a missing-record fallback, proves that
            # no managed launch was called in this root attempt.
            child = {"monitor": "not_created", "overlay": "not_created"}
        payload = {"schema": "bidking_startup_cleanup_v1", "run_id": run_id,
                   "phase": "bootstrap", "acl_restore": acl_restore_state,
                   "handle_release": handle_release_state,
                   "primary_error": primary_error is not None or primary_exit_code not in (None, 0),
                   "primary_exit_code": primary_exit_code,
                   "powershell_attempted": managed_launch_entered,
                   "acl_restore_failed_count": acl_restore_failed_count,
                   "acl_restore_attempted_count": acl_restore_attempted_count,
                   "primary_exception_preserved": primary_error is not None,
                   "monitor": child.get("monitor") if child.get("monitor") in states else "unknown",
                   "overlay": child.get("overlay") if child.get("overlay") in states else "unknown"}
        message_keys = {"startup_failed", "unsupported_system", "runtime_missing", "component_missing",
                        "runtime_probe_failed", "close_game_first", "lock_changed", "lock_cleanup_failed",
                        "npcap_install_incomplete", "npcap_installer_missing"}
        payload["message_key"] = child.get("message_key") if child.get("message_key") in message_keys else "startup_failed"
        payload.update(primary_projection)
        final = directory / (run_id + "--cleanup-bootstrap.json")
        temporary = directory / (run_id + "--cleanup-bootstrap.tmp")
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) <= 4096:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
            os.replace(temporary, final)
    except BaseException:
        pass

INTEGRITY_FAILURE_EXITS = {
    "integrity_member_path_invalid": 101,
    "integrity_reparse_or_symlink_rejected": 102,
    "integrity_file_open_failed": 103,
    "integrity_stream_namespace_invalid": 104,
    "integrity_stream_inventory_unavailable": 105,
    "integrity_stream_read_failed": 106,
    "integrity_stream_identity_changed": 107,
    "integrity_file_identity_unavailable": 108,
    "integrity_file_missing": 109,
    "integrity_file_type_invalid": 110,
    "integrity_named_stream_rejected": 111,
    "integrity_directory_missing": 112,
    "integrity_directory_type_invalid": 113,
    "integrity_directory_open_failed": 114,
    "integrity_directory_identity_changed": 115,
    "integrity_directory_identity_unavailable": 116,
    "integrity_file_read_failed": 117,
    "integrity_file_identity_changed": 118,
    "integrity_coverage_invalid": 119,
    "integrity_coverage_unreadable": 120,
    "integrity_namespace_unreadable": 121,
    "integrity_namespace_invalid": 122,
    "integrity_member_identity_collision": 123,
    "integrity_signature_encoding_invalid": 124,
    "integrity_public_key_missing": 125,
    "integrity_signature_invalid": 126,
    "integrity_public_key_invalid": 127,
    "integrity_manifest_invalid": 128,
    "integrity_file_size_mismatch": 129,
    "integrity_file_hash_mismatch": 130,
    "integrity_coverage_mismatch": 131,
    "integrity_handle_retention_failed": 132,
    "integrity_namespace_retention_failed": 133,
    "integrity_namespace_not_verified": 134,
    "integrity_namespace_root_changed": 135,
    "integrity_namespace_changed": 136,
    "integrity_stream_namespace_changed": 137,
    "integrity_handle_release_failed": 138,
}

SAFE_PRIMARY_REASONS = frozenset(INTEGRITY_FAILURE_EXITS) | {
    "bootstrap_exact_loader_unavailable", "managed_exit_nonzero",
    "namespace_guard_windows_only", "namespace_directory_unreadable",
    "namespace_reparse_rejected", "namespace_directory_type_invalid",
    "namespace_file_type_invalid", "namespace_owner_unreadable",
    "namespace_security_read_failed", "namespace_security_policy_invalid",
    "namespace_security_apply_failed", "namespace_security_apply_unverified",
    "namespace_root_identity_unavailable", "namespace_named_stream_rejected",
    "namespace_stream_inventory_unavailable", "namespace_stream_inventory_invalid",
    "namespace_recovery_path_invalid", "namespace_recovery_invalid",
    "namespace_restore_verification_failed", "namespace_recovery_acl_invalid",
    "namespace_write_denial_unproven", "namespace_stream_write_denial_unproven",
    "namespace_lock_attestation_failed", "namespace_file_inventory_empty",
    "namespace_recovery_cleanup_failed", "namespace_guard_elevation_required",
    "namespace_recovery_root_missing", "namespace_restore_incomplete",
    "client_lifecycle_unavailable", "client_lifecycle_request_rejected",
    "client_lifecycle_busy", "client_lifecycle_payload_invalid",
    "client_lifecycle_start_failed", "native_module_missing",
    "native_module_load_failed", "native_symbol_missing", "native_access_denied",
    "native_exception", "native_status_invalid", "native_status_nonzero",
}
SAFE_PRIMARY_STAGES = frozenset({
    "none", "unknown", "kernel_import", "integrity_verify", "namespace_guard_import",
    "namespace_lease", "integrity_recheck", "launcher_import", "managed_launch",
})

def project_primary_exception(error, stage):
    # Display-only fixed projection. Never stringify an exception, inspect a
    # traceback/cause, or coerce user-defined numeric/string subclasses.
    projected = {
        "failure_projection_version": 1,
        "primary_failure_stage": stage if type(stage) is str and stage in SAFE_PRIMARY_STAGES else "unknown",
        "primary_exception_class": "none" if error is None else "unknown",
        "primary_reason_code": "none" if error is None else "unknown",
        "primary_errno": None, "primary_winerror": None,
    }
    if error is None:
        return projected
    try:
        builtin_types = (
            (RuntimeError, "RuntimeError"), (PermissionError, "PermissionError"),
            (FileNotFoundError, "FileNotFoundError"), (FileExistsError, "FileExistsError"),
            (NotADirectoryError, "NotADirectoryError"), (IsADirectoryError, "IsADirectoryError"),
            (TimeoutError, "TimeoutError"), (OSError, "OSError"),
            (ModuleNotFoundError, "ModuleNotFoundError"), (ImportError, "ImportError"),
            (ValueError, "ValueError"), (TypeError, "TypeError"), (SystemExit, "SystemExit"),
        )
        for allowed_type, fixed_name in builtin_types + namespace_guard_exception_types:
            if type(error) is allowed_type:
                projected["primary_exception_class"] = fixed_name
                break
        if projected["primary_exception_class"] == "unknown":
            return projected
        arguments = error.args
        if type(arguments) is tuple and len(arguments) == 1:
            reason = arguments[0]
            if type(reason) is str and reason in SAFE_PRIMARY_REASONS:
                projected["primary_reason_code"] = reason
        if type(error) is SystemExit and type(error.code) is int:
            for reason, code in INTEGRITY_FAILURE_EXITS.items():
                if error.code == code:
                    projected["primary_reason_code"] = reason
                    break
        if any(type(error) is allowed for allowed in (
                PermissionError, FileNotFoundError, FileExistsError, NotADirectoryError,
                IsADirectoryError, TimeoutError, OSError)):
            for attribute, field in (("errno", "primary_errno"), ("winerror", "primary_winerror")):
                value = getattr(error, attribute, None)
                if type(value) is int and 0 <= value <= 2147483647:
                    projected[field] = value
    except BaseException:
        # A diagnostic projection cannot replace the initiating error or cleanup.
        pass
    return projected

primary_projection = project_primary_exception(None, "none")
active_startup_stage = "kernel_import"
def fail_with_safe_reason(code):
    exit_code = INTEGRITY_FAILURE_EXITS.get(code) if type(code) is str else None
    if exit_code is not None:
        print("BIDKING_STARTUP_FAILURE=" + code, flush=True)
        raise SystemExit(exit_code)
    raise RuntimeError(code)
try:
    kernel = load_exact("bidking_kernel", kernel_path)
    timing_event("kernel_import", "ok")
    active_startup_stage = "integrity_verify"
    ok, code, _count, _manifest_id = kernel.verify_local_integrity(root)
    if not ok:
        fail_with_safe_reason(code)
    active_startup_stage = "namespace_guard_import"
    guard = load_exact(
        "_bidking_local_integrity_namespace_guard",
        root + "/scripts/local_integrity_namespace_guard.py",
    )
    try:
        namespace_guard_exception_types = (
            (getattr(guard, "NamespaceGuardError", None), "NamespaceGuardError"),
            (getattr(guard, "NamespaceRestoreError", None), "NamespaceRestoreError"),
        )
    except BaseException:
        pass
    active_startup_stage = "namespace_lease"
    namespace_lease_started = True
    namespace_lease = guard.begin_namespace_lease(
        root,
        timing_event=timing_event,
    )
    active_startup_stage = "integrity_recheck"
    namespace_ok, namespace_code = kernel.recheck_local_integrity(root)
    if not namespace_ok:
        fail_with_safe_reason(namespace_code)
    active_startup_stage = "launcher_import"
    launcher = load_exact("_client_launcher", launcher_path)
    # The lifecycle ABI waits for the managed PowerShell startup/guard to return,
    # not for the complete lifetime of a successfully detached monitor/overlay.
    try:
        active_startup_stage = "managed_launch"
        if mode == "diagnostics":
            if forwarded:
                raise RuntimeError("client_lifecycle_request_rejected")
            managed_exit_code = launcher.diagnostics_main()
        elif mode == "repair":
            managed_exit_code = launcher.repair_main(forwarded)
        elif mode == "launch":
            managed_launch_entered = True
            managed_exit_code = launcher.main(forwarded)
        else:
            raise RuntimeError("client_lifecycle_request_rejected")
    except RuntimeError as lifecycle_error:
        # A second root EXE may finish its own integrity check while the first
        # launch still owns the native lifecycle mutex.  That click is an
        # idempotent request, not a product failure and not client_exit_1.
        if mode == "launch" and str(lifecycle_error) == "client_lifecycle_busy":
            timing_topology = "rapid_reentry"
            timing_event("startup_terminal", "skipped")
            managed_exit_code = 0
        else:
            raise
except BaseException as startup_error:
    primary_error = startup_error
    primary_projection = project_primary_exception(startup_error, active_startup_stage)
    if isinstance(startup_error, SystemExit) and type(startup_error.code) is int:
        primary_exit_code = startup_error.code if 0 <= startup_error.code <= 255 else None
    if namespace_lease_started and namespace_lease is None:
        acl_restore_state = getattr(startup_error, "namespace_restore_state", "unknown")
        if acl_restore_state not in {"not_acquired", "restored", "failed"}:
            acl_restore_state = "unknown"
        restore_error = getattr(startup_error, "namespace_restore_error", startup_error)
        failed = getattr(restore_error, "failed_count", None)
        attempted = getattr(restore_error, "attempted_count", None)
        acl_restore_failed_count = failed if type(failed) is int and 0 <= failed <= 100000 else None
        acl_restore_attempted_count = attempted if type(attempted) is int and 0 <= attempted <= 100000 else None
        if acl_restore_failed_count:
            acl_restore_state = "failed"
finally:
    if primary_error is None and type(managed_exit_code) is int and 0 <= managed_exit_code <= 255:
        primary_exit_code = managed_exit_code
        if managed_exit_code != 0:
            primary_projection = project_primary_exception(None, active_startup_stage)
            primary_projection["primary_reason_code"] = "managed_exit_nonzero"
    try:
        if namespace_lease is not None:
            namespace_lease.close()
            acl_restore_state = "restored"
    except BaseException as restore_error:
        acl_restore_state = "failed"
        failed = getattr(restore_error, "failed_count", None)
        attempted = getattr(restore_error, "attempted_count", None)
        acl_restore_failed_count = failed if type(failed) is int and 0 <= failed <= 100000 else None
        acl_restore_attempted_count = attempted if type(attempted) is int and 0 <= attempted <= 100000 else None
        if primary_error is None and managed_exit_code == 0:
            managed_exit_code = 211
    finally:
        try:
            if kernel is not None:
                released, release_code = kernel.release_local_integrity()
                handle_release_state = "released" if released else "failed"
                if not released and primary_error is None and managed_exit_code == 0:
                    managed_exit_code = INTEGRITY_FAILURE_EXITS.get(release_code, 138)
        except BaseException:
            handle_release_state = "failed"
            if primary_error is None and managed_exit_code == 0:
                managed_exit_code = 138
        write_cleanup_receipt()
if primary_error is not None:
    raise primary_error
raise SystemExit(managed_exit_code)
"#;
        let arguments = std::env::args().skip(1).collect::<Vec<_>>();
        let diagnostics = arguments.iter().any(|arg| arg == "--diagnostics");
        let repair = arguments.iter().any(|arg| arg == "--repair");
        let mode = match (diagnostics, repair) {
            (true, false) => "diagnostics",
            (false, true) => "repair",
            (false, false) => "launch",
            (true, true) => "invalid",
        };
        let forwarded = arguments
            .into_iter()
            .filter(|arg| arg != "--diagnostics" && arg != "--repair")
            .collect::<Vec<_>>();
        let topology = match mode {
            "diagnostics" => "diagnostics",
            "repair" => "repair",
            _ => "unknown",
        };
        unsafe { std::env::set_var("BIDKING_STARTUP_TIMING_TOPOLOGY", topology) };
        let _ = write_timing_event("kernel_import", "begin", topology);
        let _ = write_timing_event("python_spawn", "begin", topology);
        let status = Command::new(root.join("runtime/python/python.exe"))
            .args(["-E", "-s", "-S", "-B", "-c", BOOTSTRAP])
            .arg(root)
            .arg(root.join(kernel))
            .arg(root.join(launcher))
            .arg(mode)
            .args(forwarded)
            .current_dir(root)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .creation_flags(CREATE_NO_WINDOW)
            .status();
        let status = match status {
            Ok(status) => {
                let _ = write_timing_event("python_spawn", "ok", topology);
                status
            }
            Err(_) => {
                let _ = write_timing_event("python_spawn", "failed", topology);
                return Err("bootstrap_python_spawn_failed");
            }
        };
        Ok(status.code().unwrap_or(1))
    }

    pub fn entry() -> Result<i32, &'static str> {
        let executable = std::env::current_exe().map_err(|_| "bootstrap_self_path_failed")?;
        let root = executable.parent().ok_or("bootstrap_root_invalid")?;
        if !executable
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.eq_ignore_ascii_case("BidKingLive.exe"))
        {
            return Err("bootstrap_self_name_invalid");
        }
        let root_metadata = fs::symlink_metadata(root).map_err(|_| "bootstrap_root_invalid")?;
        reject_reparse(&root_metadata)?;
        let _ = write_timing_event("preload", "begin", "unknown");
        let (_retained_handles, kernel, launcher) = verify_preload_roots(root, &executable)?;
        let _ = write_timing_event("preload", "ok", "unknown");
        run_product(root, &kernel, &launcher)
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn cleanup_observation() -> Option<serde_json::Value> {
        // Display-only evidence from this root invocation; never an authority
        // input for child termination, ACL restore, or package deletion.
        let run_id = std::env::var("BIDKING_STARTUP_TIMING_RUN_ID").ok()?;
        if run_id.len() != 32 || !run_id.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
            return None;
        }
        let directory = PathBuf::from(std::env::var_os("BIDKING_STARTUP_TIMING_DIR")?);
        if !directory.is_absolute() {
            return None;
        }
        reject_reparse(&fs::symlink_metadata(&directory).ok()?).ok()?;
        let path = directory.join(format!("{run_id}--cleanup-bootstrap.json"));
        let metadata = fs::symlink_metadata(&path).ok()?;
        reject_reparse(&metadata).ok()?;
        if !metadata.is_file() || metadata.len() > 4096 {
            return None;
        }
        let file = fs::File::open(path).ok()?;
        let mut bytes = Vec::new();
        file.take(4097).read_to_end(&mut bytes).ok()?;
        if bytes.len() > 4096 {
            return None;
        }
        let value: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
        if value.get("schema")?.as_str()? != "bidking_startup_cleanup_v1"
            || value.get("run_id")?.as_str()? != run_id
            || value.get("phase")?.as_str()? != "bootstrap"
        {
            return None;
        }
        Some(value)
    }

    fn cleanup_display_state(acl: &str, handles: &str, monitor: &str, overlay: &str) -> &'static str {
        if acl == "failed" || handles == "failed" || monitor == "cleanup_failed" || overlay == "cleanup_failed" {
            return "incomplete";
        }
        if monitor == "preserved_game" || overlay == "preserved_game" {
            return "game_preserved";
        }
        let finished = |value: &str| matches!(value, "not_created" | "exited" | "reclaimed");
        if matches!(acl, "restored" | "not_acquired") && handles == "released"
            && finished(monitor) && finished(overlay)
        {
            return "complete";
        }
        "unknown"
    }

    fn failure_reason(code: &str, message_key: &str) -> &'static str {
        match message_key {
            "unsupported_system" => return "需要 Windows 10 或更高版本的64位系统。",
            "runtime_missing" | "component_missing" => return "程序运行组件不完整。",
            "runtime_probe_failed" => return "运行环境自检未通过，请检查 VC++ x64运行库和系统要求。",
            "close_game_first" => return "计算器需要先于游戏连接启动，本次没有关闭游戏。",
            "lock_changed" => return "启动锁在安全清理期间发生变化，本次没有继续启动。",
            "lock_cleanup_failed" => return "启动锁未能安全清理，本次没有继续启动。",
            "npcap_install_incomplete" => return "未检测到 Npcap 安装完成。",
            "npcap_installer_missing" => return "未找到 Npcap 抓包驱动安装包。",
            _ => {}
        }
        match code {
            "integrity_coverage_mismatch" => "程序包在运行后产生了额外文件，或目录混入了其他文件。",
            "integrity_named_stream_rejected" => "程序文件的附加数据格式不受支持，完整性检查未通过。",
            "integrity_file_missing" | "integrity_directory_missing" | "package_generation_identity_component_missing" => "程序包文件不完整。",
            "integrity_file_size_mismatch" | "integrity_file_hash_mismatch" => "程序文件内容已变化或损坏。",
            "monitor_foreign_lock" | "monitor_identity_unknown" | "monitor_foreign_process" | "process_query_unknown" => "后台进程身份或状态无法完整确认，未终止身份不明的进程。",
            "monitor_old_package_game_active" => "游戏仍在使用旧程序包后台，本次保留对局连接。",
            "monitor_old_package_changed" | "monitor_old_package_stop_failed" | "monitor_old_package_lock_changed" | "monitor_old_package_conflict" => "旧程序包后台冲突尚未安全解除。",
            "overlay_spawn_failed" => "悬浮窗未能启动。",
            "overlay_exception" => "悬浮窗启动后异常退出。",
            "overlay_exited_cleanly" => "悬浮窗在启动完成前已关闭。",
            "monitor_exit" => "后台读取在启动完成前已退出。",
            "startup_cleanup_incomplete" => "本次启动的资源收尾未完成。",
            "user_cancelled" => "本次启动已取消。",
            "driver_load_denied" | "component_execution_blocked" | "policy_locked" => "本地兼容组件被系统权限或策略阻止。",
            "cert_store_write_denied" | "component_write_denied" | "uac_disabled" | "interactive_user_mismatch" => "本地兼容初始化未完成，请按诊断说明检查运行权限。",
            "foreign_cert_or_state" | "wss_same_path_mixed_generation" | "wss_stale_product_service_conflict" => "本地兼容状态或旧程序包身份冲突，本次保留现场。",
            "wss_stale_product_service_repaired_restart_required" => "旧版产品兼容服务已按原规则修复，需要重启 Windows 后继续。",
            _ if code.starts_with("integrity_") => "程序包完整性验证未通过。",
            _ => "启动未完成，请保留错误代码和本次诊断。"
        }
    }

    pub fn report_failure(code: &str) {
        if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA").filter(|v| !v.is_empty()) {
            let directory = PathBuf::from(local_app_data).join("BidKingLive").join("logs");
            if fs::create_dir_all(&directory).is_ok() {
                let payload = format!("schema={FAILURE_LOG_SCHEMA}\ncode={code}\n");
                let _ = fs::write(directory.join("startup_failure.log"), payload);
            }
        }
        if cfg!(debug_assertions) {
            eprintln!("{code}");
            return;
        }
        let title = wide("BidKing Live · 启动失败");
        let observation = cleanup_observation();
        let field = |key: &str| observation.as_ref().and_then(|v| v.get(key)).and_then(|v| v.as_str()).unwrap_or("unknown");
        let state = cleanup_display_state(field("acl_restore"), field("handle_release"), field("monitor"), field("overlay"));
        let cleanup = match state {
            "complete" => "本次启动的收尾已完成；关闭本提示后启动器退出。",
            "game_preserved" => "为避免中断对局，仍保留后台读取。",
            "incomplete" => "部分资源未能完成收尾，请暂时保留程序目录。",
            _ => "部分后台仍保留或状态未确认，请暂时保留程序目录。",
        };
        // A cleanup warning concerns the current directory/resources.  It must
        // not hide a safe, cause-specific action on the ZIP or a new directory.
        let next = if code == "integrity_named_stream_rejected" {
            "先关闭本提示。打开原官方 ZIP 的“属性”，若有“解除锁定”请勾选并应用，再解压到新的空目录，不要覆盖旧目录。仍失败时，请保留原目录和启动日志并私发支持人员。"
        } else if code.starts_with("integrity_") || code == "package_generation_identity_component_missing"
            || matches!(field("message_key"), "component_missing" | "runtime_missing") {
            "先关闭本提示，从官方 ZIP 完整解压到新的空目录，不要覆盖旧目录。"
        } else if field("message_key") == "npcap_install_incomplete" {
            "先关闭本提示，手动运行包内 vendor\\npcap-installer.exe 完成安装后重试。"
        } else if field("message_key") == "npcap_installer_missing" {
            "先关闭本提示，从 Npcap 官方网站 https://npcap.com 下载安装后重试。"
        } else if state == "incomplete" || state == "unknown" {
            "先关闭本提示和游戏；仍无法退出时，运行“诊断工具\\收集诊断.bat”并私发支持人员。"
        } else if state == "game_preserved" || field("message_key") == "close_game_first" {
            "先关闭本提示，等游戏完全退出后再启动计算器。"
        } else {
            "先关闭本提示；游戏退出后重试，仍失败时运行“诊断工具\\启动修复.bat”或收集诊断。"
        };
        let reason = failure_reason(code, field("message_key"));
        let message = wide(&format!(
            "{reason}\r\n\r\n收尾：{cleanup}\r\n下一步：{next}\r\n\r\n错误代码：{code}\r\n诊断位置：本机 BidKingLive 日志目录。"
        ));
        unsafe {
            MessageBoxW(
                0,
                message.as_ptr(),
                title.as_ptr(),
                MB_OK | MB_ICONERROR | MB_SETFOREGROUND,
            );
        }
    }
}

fn main() {
    #[cfg(windows)]
    {
        windows_bootstrap::start_timing_context();
        let feedback = startup_feedback::is_plain_launch(std::env::args_os());
        match startup_feedback::run_with_feedback(feedback, windows_bootstrap::entry) {
            Ok(0) => {
                windows_bootstrap::write_timing_event("root_exit", "ok", "unknown");
                std::process::exit(0)
            }
            Ok(code) => {
                windows_bootstrap::write_timing_event("root_exit", "failed", "unknown");
                let failure = windows_bootstrap::product_failure_from_exit(code)
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("client_exit_{code}"));
                windows_bootstrap::report_failure(&failure);
                std::process::exit(code);
            }
            Err(code) => {
                windows_bootstrap::write_timing_event("root_exit", "failed", "unknown");
                windows_bootstrap::report_failure(code);
                std::process::exit(1);
            }
        }
    }

    #[cfg(not(windows))]
    {
        eprintln!("bootstrap_platform_unsupported");
        std::process::exit(1);
    }
}
