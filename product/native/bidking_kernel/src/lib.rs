//! bidking_kernel — L4 安全内核(Rust -> .pyd, 经 maturin/PyO3)。
//!
//! 把【安全关键原语】放进 Rust native 码(优化+strip+LTO), 抬高逆向成本(Python/Cython 只到"中等",
//! 名字/结构都在; Rust 优化后无 Python 脚手架, AI 帮不上)。Python 侧用 secure/activation.py 的
//! RustProvider 包它并 set_provider 注入, 上层(data_crypt/integrity/antidebug)零改。
//!
//! 当前实现(L4 groundwork, 服务端激活在后续接):
//! - device_fingerprint(): Windows MachineGuid -> sha256 -> 设备ID(防一份激活转卖多机)。
//! - derive_data_key(token, device): HKDF-SHA256 派生 AES-256 data key。【key 在 native 算, 绝不落 Python 明文】。
//! - verify_activation(token, secret): HMAC-SHA256 校验激活 token 签名(正式版 secret/校验权威在服务端)。
//! - client lifecycle: launcher/diagnostics payload、ABI、generation 与并发状态只在 native 边界消费。

use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

mod client_lifecycle;
mod startup_timing;
use client_lifecycle::{client_lifecycle_contract, run_client_lifecycle};

type HmacSha256 = Hmac<Sha256>;

// 编译期由 build.rs 生成: pub const DATA_KEY: Option<[u8; 32]> = Some([...]) | None。
// 打包时 env BIDKING_DATA_KEY(=出厂主密钥)在 -> Some(嵌进二进制); dev 编译无 env -> None。
include!(concat!(env!("OUT_DIR"), "/embedded_key.rs"));

const LOCAL_INTEGRITY_MANIFEST: &str = "integrity/local_integrity_manifest.json";
const LOCAL_INTEGRITY_SIGNATURE: &str = "integrity/local_integrity_manifest.sig";
const LOCAL_INTEGRITY_SCHEMA: &str = "bidking_local_integrity_manifest_v1";
const WINDOWS_ZONE_IDENTIFIER_STREAM: &str = ":Zone.Identifier:$DATA";
const WINDOWS_ZONE_IDENTIFIER_MAX_BYTES: usize = 16 * 1024;
const LOCAL_INTEGRITY_TREES: &[&str] = &[
    "runtime/python",
    "scripts",
    "hero_ref/src",
    "hero_ref/tools",
    "src",
    "data/processed",
    "data/raw/tables",
];
const LOCAL_INTEGRITY_FIXED: &[&str] = &[
    "BidKingLive.exe",
    "activation_service_endpoints.json",
    "activation_http_e2ee.json",
    "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs",
];
static VERIFIED_LOCAL_INTEGRITY_HANDLES: Mutex<Vec<File>> = Mutex::new(Vec::new());
static VERIFIED_LOCAL_INTEGRITY_NAMESPACE: Mutex<Option<VerifiedLocalIntegrityNamespace>> =
    Mutex::new(None);

struct VerifiedLocalIntegrityNamespace {
    root: PathBuf,
    paths: HashSet<String>,
    package_namespace: HashSet<String>,
    file_streams: HashMap<String, StableFileStreamsSnapshot>,
}

#[derive(Clone, PartialEq, Eq)]
struct StableStreamSnapshot {
    identity: (u64, u64),
    size: u64,
    sha256: [u8; 32],
}

#[derive(Clone, PartialEq, Eq)]
struct StableFileStreamsSnapshot {
    data: StableStreamSnapshot,
    zone_identifier: Option<StableStreamSnapshot>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LocalIntegrityManifest {
    schema: String,
    manifest_version: u32,
    public_key_sha256: String,
    release_id: String,
    entries: Vec<LocalIntegrityEntry>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LocalIntegrityEntry {
    path: String,
    role: String,
    bytes: u64,
    sha256: String,
}

fn safe_member_path(relative: &str) -> Result<PathBuf, &'static str> {
    if relative.is_empty() || relative.contains('\\') {
        return Err("integrity_member_path_invalid");
    }
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("integrity_member_path_invalid");
    }
    Ok(path.to_path_buf())
}

#[cfg(windows)]
fn file_identity(file: &File) -> Option<(u64, u64)> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
    };

    let mut information: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe {
        GetFileInformationByHandle(file.as_raw_handle() as HANDLE, &mut information)
    };
    if ok == 0 {
        return None;
    }
    Some((
        u64::from(information.dwVolumeSerialNumber),
        (u64::from(information.nFileIndexHigh) << 32)
            | u64::from(information.nFileIndexLow),
    ))
}

#[cfg(unix)]
fn file_identity(file: &File) -> Option<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    let metadata = file.metadata().ok()?;
    Some((metadata.dev(), metadata.ino()))
}

#[cfg(not(any(windows, unix)))]
fn file_identity(_file: &File) -> Option<(u64, u64)> {
    None
}

fn reject_reparse_or_symlink(metadata: &Metadata) -> Result<(), &'static str> {
    if metadata.file_type().is_symlink() {
        return Err("integrity_reparse_or_symlink_rejected");
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err("integrity_reparse_or_symlink_rejected");
        }
    }
    Ok(())
}

fn open_no_reparse(path: &Path) -> Result<File, &'static str> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.share_mode(FILE_SHARE_READ);
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    options
        .open(path)
        .map_err(|_| "integrity_file_open_failed")
}

#[cfg(windows)]
fn enumerate_stream_names(path: &Path) -> Result<Vec<(String, u64)>, &'static str> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Foundation::{
        ERROR_HANDLE_EOF, ERROR_NO_MORE_FILES, GetLastError, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        FindClose, FindFirstStreamW, FindNextStreamW, FindStreamInfoStandard,
        WIN32_FIND_STREAM_DATA,
    };

    let mut wide = path.as_os_str().encode_wide().collect::<Vec<_>>();
    if wide.contains(&0) {
        return Err("integrity_stream_namespace_invalid");
    }
    wide.push(0);
    let mut data: WIN32_FIND_STREAM_DATA = unsafe { std::mem::zeroed() };
    let search = unsafe {
        FindFirstStreamW(
            wide.as_ptr(),
            FindStreamInfoStandard,
            (&mut data as *mut WIN32_FIND_STREAM_DATA).cast(),
            0,
        )
    };
    if search == INVALID_HANDLE_VALUE {
        return Err("integrity_stream_inventory_unavailable");
    }
    let mut streams = Vec::new();
    loop {
        let Some(name_end) = data.cStreamName.iter().position(|unit| *unit == 0) else {
            unsafe {
                FindClose(search);
            }
            return Err("integrity_stream_namespace_invalid");
        };
        let Ok(name) = String::from_utf16(&data.cStreamName[..name_end]) else {
            unsafe {
                FindClose(search);
            }
            return Err("integrity_stream_namespace_invalid");
        };
        if name.is_empty() || data.StreamSize < 0 {
            unsafe {
                FindClose(search);
            }
            return Err("integrity_stream_namespace_invalid");
        }
        streams.push((name, data.StreamSize as u64));
        if unsafe {
            FindNextStreamW(
                search,
                (&mut data as *mut WIN32_FIND_STREAM_DATA).cast(),
            )
        } != 0
        {
            continue;
        }
        let error = unsafe { GetLastError() };
        unsafe {
            FindClose(search);
        }
        if error != ERROR_HANDLE_EOF && error != ERROR_NO_MORE_FILES {
            return Err("integrity_stream_inventory_unavailable");
        }
        break;
    }
    streams.sort_by(|left, right| left.0.to_lowercase().cmp(&right.0.to_lowercase()));
    if streams
        .windows(2)
        .any(|pair| pair[0].0.eq_ignore_ascii_case(&pair[1].0))
    {
        return Err("integrity_stream_namespace_invalid");
    }
    Ok(streams)
}

#[cfg(not(windows))]
fn enumerate_stream_names(path: &Path) -> Result<Vec<(String, u64)>, &'static str> {
    let size = fs::metadata(path)
        .map_err(|_| "integrity_stream_inventory_unavailable")?
        .len();
    Ok(vec![("::$DATA".to_string(), size)])
}

fn open_stable_stream(
    base_path: &Path,
    stream_name: &str,
    expected_size: u64,
) -> Result<(File, StableStreamSnapshot, Vec<u8>), &'static str> {
    let stream_path = if stream_name.eq_ignore_ascii_case("::$DATA") {
        base_path.to_path_buf()
    } else {
        let mut value = base_path.as_os_str().to_os_string();
        value.push(stream_name);
        PathBuf::from(value)
    };
    let mut file = open_no_reparse(&stream_path)?;
    let identity = file_identity(&file).ok_or("integrity_file_identity_unavailable")?;
    let mut payload = Vec::new();
    file.read_to_end(&mut payload)
        .map_err(|_| "integrity_stream_read_failed")?;
    if payload.len() as u64 != expected_size
        || file
            .metadata()
            .map_err(|_| "integrity_file_identity_unavailable")?
            .len()
            != expected_size
    {
        return Err("integrity_stream_identity_changed");
    }
    let rebound = open_no_reparse(&stream_path)?;
    if file_identity(&rebound) != Some(identity) {
        return Err("integrity_stream_identity_changed");
    }
    let snapshot = StableStreamSnapshot {
        identity,
        size: expected_size,
        sha256: Sha256::digest(&payload).into(),
    };
    Ok((file, snapshot, payload))
}

fn valid_windows_zone_identifier(payload: &[u8]) -> bool {
    if payload.is_empty() || payload.len() > WINDOWS_ZONE_IDENTIFIER_MAX_BYTES {
        return false;
    }
    // Some Windows download writers include one string terminator. Only the
    // parsing view omits it; callers retain the original stream bytes/identity.
    let payload = payload.strip_suffix(&[0]).unwrap_or(payload);
    let payload = payload.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(payload);
    let Ok(text) = std::str::from_utf8(payload) else {
        return false;
    };
    if text.contains('\0') {
        return false;
    }
    let mut section_seen = false;
    let mut zone_id_seen = false;
    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') {
            if section_seen || !line.eq_ignore_ascii_case("[ZoneTransfer]") {
                return false;
            }
            section_seen = true;
            continue;
        }
        if !section_seen {
            return false;
        }
        let Some((raw_key, raw_value)) = line.split_once('=') else {
            return false;
        };
        let key = raw_key.trim();
        if key.is_empty()
            || !key
                .bytes()
                .all(|byte| {
                    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.')
                })
        {
            return false;
        }
        if key.eq_ignore_ascii_case("ZoneId") {
            if zone_id_seen || !matches!(raw_value.trim(), "3" | "4") {
                return false;
            }
            zone_id_seen = true;
        }
    }
    section_seen && zone_id_seen
}

fn snapshot_file_streams(
    path: &Path,
) -> Result<(StableFileStreamsSnapshot, Vec<File>), &'static str> {
    let before = fs::symlink_metadata(path).map_err(|_| "integrity_file_missing")?;
    reject_reparse_or_symlink(&before)?;
    if !before.is_file() {
        return Err("integrity_file_type_invalid");
    }
    let mut data_snapshot = None;
    let mut zone_identifier_snapshot = None;
    let mut handles = Vec::new();
    for (name, size) in enumerate_stream_names(path)? {
        let is_data = name.eq_ignore_ascii_case("::$DATA");
        let is_zone_identifier = name.eq_ignore_ascii_case(WINDOWS_ZONE_IDENTIFIER_STREAM);
        if !is_data
            && (!is_zone_identifier
                || size == 0
                || size > WINDOWS_ZONE_IDENTIFIER_MAX_BYTES as u64)
        {
            return Err("integrity_named_stream_rejected");
        }
        let (handle, snapshot, payload) = open_stable_stream(path, &name, size)?;
        if is_data {
            if data_snapshot.replace(snapshot).is_some() {
                return Err("integrity_stream_namespace_invalid");
            }
        } else {
            if zone_identifier_snapshot.is_some()
                || !valid_windows_zone_identifier(&payload)
            {
                return Err("integrity_named_stream_rejected");
            }
            zone_identifier_snapshot = Some(snapshot);
        }
        handles.push(handle);
    }
    let data = data_snapshot.ok_or("integrity_stream_namespace_invalid")?;
    Ok((
        StableFileStreamsSnapshot {
            data,
            zone_identifier: zone_identifier_snapshot,
        },
        handles,
    ))
}

fn open_stable_directory(path: &Path) -> Result<(File, (u64, u64)), &'static str> {
    let before = fs::symlink_metadata(path).map_err(|_| "integrity_directory_missing")?;
    reject_reparse_or_symlink(&before)?;
    if !before.is_dir() {
        return Err("integrity_directory_type_invalid");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
        options.share_mode(FILE_SHARE_READ);
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
    }
    let directory = options
        .open(path)
        .map_err(|_| "integrity_directory_open_failed")?;
    let rebound = options
        .open(path)
        .map_err(|_| "integrity_directory_identity_changed")?;
    let after =
        fs::symlink_metadata(path).map_err(|_| "integrity_directory_identity_changed")?;
    reject_reparse_or_symlink(&after)?;
    let identities = [file_identity(&directory), file_identity(&rebound)];
    if identities.iter().any(Option::is_none)
        || identities[1] != identities[0]
        || !after.is_dir()
    {
        return Err("integrity_directory_identity_changed");
    }
    Ok((directory, identities[0].ok_or("integrity_directory_identity_unavailable")?))
}

struct StablePathGuard {
    root: PathBuf,
    directories: HashMap<String, (PathBuf, File, (u64, u64))>,
}

impl StablePathGuard {
    fn new(root: &Path) -> Result<Self, &'static str> {
        let mut guard = Self {
            root: root.to_path_buf(),
            directories: HashMap::new(),
        };
        guard.ensure_directory_path(Path::new(""))?;
        Ok(guard)
    }

    fn ensure_directory_path(&mut self, relative: &Path) -> Result<PathBuf, &'static str> {
        let mut current = self.root.clone();
        let mut relative_parts = Vec::new();
        let components = relative.components().collect::<Vec<_>>();
        for index in 0..=components.len() {
            if index > 0 {
                let Component::Normal(part) = components[index - 1] else {
                    return Err("integrity_member_path_invalid");
                };
                relative_parts.push(
                    part.to_str()
                        .ok_or("integrity_member_path_invalid")?
                        .to_string(),
                );
                current.push(part);
            }
            let key = relative_parts.join("/").to_lowercase();
            if self.directories.contains_key(&key) {
                continue;
            }
            let (handle, identity) = open_stable_directory(&current)?;
            self.directories
                .insert(key, (current.clone(), handle, identity));
        }
        Ok(current)
    }

    fn ensure_directory(&mut self, relative: &str) -> Result<PathBuf, &'static str> {
        let member = safe_member_path(relative)?;
        self.ensure_directory_path(&member)
    }

    fn read_stable_file(
        &mut self,
        relative: &str,
    ) -> Result<(Vec<u8>, File, (u64, u64)), &'static str> {
        let member = safe_member_path(relative)?;
        let parent = member.parent().unwrap_or_else(|| Path::new(""));
        self.ensure_directory_path(parent)?;
        let path = self.root.join(member);
        let before =
            fs::symlink_metadata(&path).map_err(|_| "integrity_file_missing")?;
        reject_reparse_or_symlink(&before)?;
        if !before.is_file() {
            return Err("integrity_file_type_invalid");
        }
        let mut file = open_no_reparse(&path)?;
        let opened = file
            .metadata()
            .map_err(|_| "integrity_file_identity_unavailable")?;
        let mut payload = Vec::new();
        file.read_to_end(&mut payload)
            .map_err(|_| "integrity_file_read_failed")?;
        let after_handle = file
            .metadata()
            .map_err(|_| "integrity_file_identity_unavailable")?;
        let after_path = fs::symlink_metadata(&path)
            .map_err(|_| "integrity_file_identity_changed")?;
        reject_reparse_or_symlink(&after_path)?;
        if !after_path.is_file()
            || opened.len() != payload.len() as u64
            || after_handle.len() != opened.len()
        {
            return Err("integrity_file_identity_changed");
        }
        let rebound = open_no_reparse(&path)?;
        let identities = [file_identity(&file), file_identity(&rebound)];
        if identities.iter().any(Option::is_none)
            || identities[1] != identities[0]
        {
            return Err("integrity_file_identity_changed");
        }
        let identity = identities[0].ok_or("integrity_file_identity_unavailable")?;
        Ok((payload, file, identity))
    }

    fn recheck(&self) -> Result<(), &'static str> {
        for (path, retained, retained_identity) in self.directories.values() {
            let (rebound, rebound_identity) = open_stable_directory(path)?;
            if file_identity(retained) != Some(*retained_identity)
                || rebound_identity != *retained_identity
            {
                return Err("integrity_directory_identity_changed");
            }
            drop(rebound);
        }
        Ok(())
    }

    fn into_handles(self) -> Vec<File> {
        self.directories
            .into_values()
            .map(|(_, handle, _)| handle)
            .collect()
    }
}

fn relative_member(root: &Path, path: &Path) -> Result<String, &'static str> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| "integrity_coverage_invalid")?;
    let mut parts = Vec::new();
    for component in relative.components() {
        let Component::Normal(value) = component else {
            return Err("integrity_coverage_invalid");
        };
        parts.push(
            value
                .to_str()
                .ok_or("integrity_coverage_invalid")?
                .to_string(),
        );
    }
    if parts.is_empty() {
        return Err("integrity_coverage_invalid");
    }
    Ok(parts.join("/"))
}

fn collect_covered_paths(
    root: &Path,
    guard: &mut StablePathGuard,
) -> Result<HashSet<String>, &'static str> {
    let mut covered = HashSet::new();
    for relative in LOCAL_INTEGRITY_FIXED {
        covered.insert(relative.to_lowercase());
    }
    for relative in LOCAL_INTEGRITY_TREES {
        let tree = guard.ensure_directory(relative)?;
        let mut pending = vec![tree];
        while let Some(directory) = pending.pop() {
            let entries = fs::read_dir(&directory)
                .map_err(|_| "integrity_coverage_unreadable")?;
            for entry in entries {
                let path = entry
                    .map_err(|_| "integrity_coverage_unreadable")?
                    .path();
                let metadata = fs::symlink_metadata(&path)
                    .map_err(|_| "integrity_coverage_unreadable")?;
                reject_reparse_or_symlink(&metadata)?;
                if metadata.is_dir() {
                    let child = relative_member(root, &path)?;
                    guard.ensure_directory(&child)?;
                    pending.push(path);
                } else if metadata.is_file() {
                    if !covered.insert(relative_member(root, &path)?.to_lowercase()) {
                        return Err("integrity_coverage_invalid");
                    }
                } else {
                    return Err("integrity_coverage_invalid");
                }
            }
        }
    }
    Ok(covered)
}

fn collect_package_namespace(
    root: &Path,
    guard: &mut StablePathGuard,
    signed_paths: &HashSet<String>,
    file_identities: &mut HashSet<(u64, u64)>,
    retained_handles: &mut Vec<File>,
) -> Result<
    (
        HashSet<String>,
        HashMap<String, StableFileStreamsSnapshot>,
    ),
    &'static str,
> {
    let mut namespace = HashSet::new();
    let mut file_streams = HashMap::new();
    namespace.insert("d:".to_string());
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let entries = fs::read_dir(&directory)
            .map_err(|_| "integrity_namespace_unreadable")?;
        for entry in entries {
            let path = entry
                .map_err(|_| "integrity_namespace_unreadable")?
                .path();
            let metadata = fs::symlink_metadata(&path)
                .map_err(|_| "integrity_namespace_unreadable")?;
            reject_reparse_or_symlink(&metadata)?;
            let relative = relative_member(root, &path)?;
            let folded = relative.to_lowercase();
            if metadata.is_dir() {
                if !namespace.insert(format!("d:{folded}")) {
                    return Err("integrity_namespace_invalid");
                }
                guard.ensure_directory(&relative)?;
                pending.push(path);
            } else if metadata.is_file() {
                if !namespace.insert(format!("f:{folded}")) {
                    return Err("integrity_namespace_invalid");
                }
                let (stream_snapshot, stream_handles) = snapshot_file_streams(&path)?;
                if signed_paths.contains(&folded) {
                    if !file_identities.contains(&stream_snapshot.data.identity) {
                        return Err("integrity_file_identity_changed");
                    }
                } else {
                    let identity = stream_snapshot.data.identity;
                    if !file_identities.insert(identity) {
                        return Err("integrity_member_identity_collision");
                    }
                }
                retained_handles.extend(stream_handles);
                if file_streams.insert(folded, stream_snapshot).is_some() {
                    return Err("integrity_stream_namespace_invalid");
                }
            } else {
                return Err("integrity_namespace_invalid");
            }
        }
    }
    Ok((namespace, file_streams))
}

fn decode_base64(input: &[u8]) -> Result<Vec<u8>, &'static str> {
    fn value(byte: u8) -> Option<u8> {
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
        return Err("integrity_signature_encoding_invalid");
    }
    let mut output = Vec::with_capacity(compact.len() / 4 * 3);
    for chunk in compact.chunks_exact(4) {
        let first = value(chunk[0]).ok_or("integrity_signature_encoding_invalid")?;
        let second = value(chunk[1]).ok_or("integrity_signature_encoding_invalid")?;
        output.push((first << 2) | (second >> 4));
        if chunk[2] != b'=' {
            let third = value(chunk[2]).ok_or("integrity_signature_encoding_invalid")?;
            output.push((second << 4) | (third >> 2));
            if chunk[3] != b'=' {
                let fourth =
                    value(chunk[3]).ok_or("integrity_signature_encoding_invalid")?;
                output.push((third << 6) | fourth);
            }
        } else if chunk[3] != b'=' {
            return Err("integrity_signature_encoding_invalid");
        }
    }
    Ok(output)
}

fn verify_local_integrity_impl(root: &Path) -> Result<(usize, String), &'static str> {
    startup_timing::write_event("kernel_manifest", "begin", None);
    let public_key = INTEGRITY_PUBLIC_KEY.ok_or("integrity_public_key_missing")?;
    let mut guard = StablePathGuard::new(root)?;
    let (manifest_bytes, manifest_handle, _) =
        guard.read_stable_file(LOCAL_INTEGRITY_MANIFEST)?;
    let (signature_encoded, signature_handle, _) =
        guard.read_stable_file(LOCAL_INTEGRITY_SIGNATURE)?;
    let mut verified_handles = vec![manifest_handle, signature_handle];
    let signature_bytes = decode_base64(&signature_encoded)?;
    let signature =
        Signature::from_slice(&signature_bytes).map_err(|_| "integrity_signature_invalid")?;
    let verifier =
        VerifyingKey::from_bytes(&public_key).map_err(|_| "integrity_public_key_invalid")?;
    verifier
        .verify(&manifest_bytes, &signature)
        .map_err(|_| "integrity_signature_invalid")?;

    let manifest: LocalIntegrityManifest =
        serde_json::from_slice(&manifest_bytes).map_err(|_| "integrity_manifest_invalid")?;
    if manifest.schema != LOCAL_INTEGRITY_SCHEMA
        || manifest.manifest_version != 1
        || manifest.release_id.is_empty()
        || manifest.entries.is_empty()
        || manifest.public_key_sha256 != hex::encode(Sha256::digest(public_key))
    {
        return Err("integrity_manifest_invalid");
    }
    startup_timing::write_event("kernel_manifest", "ok", None);

    startup_timing::write_event("kernel_coverage", "begin", None);
    let mut paths = HashSet::with_capacity(manifest.entries.len());
    let mut file_identities = HashSet::with_capacity(manifest.entries.len());
    for entry in &manifest.entries {
        if entry.role.is_empty()
            || entry.sha256.len() != 64
            || !paths.insert(entry.path.to_lowercase())
        {
            return Err("integrity_manifest_invalid");
        }
        let (payload, handle, identity) = guard.read_stable_file(&entry.path)?;
        if !file_identities.insert(identity) {
            return Err("integrity_member_identity_collision");
        }
        if payload.len() as u64 != entry.bytes {
            return Err("integrity_file_size_mismatch");
        }
        if hex::encode(Sha256::digest(&payload)) != entry.sha256 {
            return Err("integrity_file_hash_mismatch");
        }
        verified_handles.push(handle);
    }
    if collect_covered_paths(root, &mut guard)? != paths {
        return Err("integrity_coverage_mismatch");
    }
    startup_timing::write_event("kernel_coverage", "ok", None);
    startup_timing::write_event("kernel_namespace", "begin", None);
    let (package_namespace, file_streams) = collect_package_namespace(
        root,
        &mut guard,
        &paths,
        &mut file_identities,
        &mut verified_handles,
    )?;
    guard.recheck()?;
    verified_handles.extend(guard.into_handles());
    let manifest_id = hex::encode(Sha256::digest(&manifest_bytes));
    let mut retained = VERIFIED_LOCAL_INTEGRITY_HANDLES
        .lock()
        .map_err(|_| "integrity_handle_retention_failed")?;
    let mut retained_namespace = VERIFIED_LOCAL_INTEGRITY_NAMESPACE
        .lock()
        .map_err(|_| "integrity_namespace_retention_failed")?;
    *retained = verified_handles;
    *retained_namespace = Some(VerifiedLocalIntegrityNamespace {
        root: root.to_path_buf(),
        paths,
        package_namespace,
        file_streams,
    });
    startup_timing::write_event("kernel_namespace", "ok", None);
    Ok((manifest.entries.len(), manifest_id[..16].to_string()))
}

fn recheck_local_integrity_impl(root: &Path) -> Result<(), &'static str> {
    startup_timing::write_event("integrity_recheck", "begin", None);
    let retained = VERIFIED_LOCAL_INTEGRITY_NAMESPACE
        .lock()
        .map_err(|_| "integrity_namespace_retention_failed")?;
    let namespace = retained
        .as_ref()
        .ok_or("integrity_namespace_not_verified")?;
    if namespace.root != root {
        return Err("integrity_namespace_root_changed");
    }
    let mut guard = StablePathGuard::new(root)?;
    if collect_covered_paths(root, &mut guard)? != namespace.paths {
        return Err("integrity_coverage_mismatch");
    }
    let mut identities = HashSet::new();
    let mut handles = Vec::new();
    let (package_namespace, file_streams) = collect_package_namespace(
        root,
        &mut guard,
        &HashSet::new(),
        &mut identities,
        &mut handles,
    )?;
    if package_namespace != namespace.package_namespace {
        return Err("integrity_namespace_changed");
    }
    if file_streams != namespace.file_streams {
        return Err("integrity_stream_namespace_changed");
    }
    guard.recheck()?;
    startup_timing::write_event("integrity_recheck", "ok", None);
    Ok(())
}

#[pyfunction]
fn startup_timing_event(event: &str, result: &str, topology: &str) -> bool {
    startup_timing::write_event(event, result, Some(topology))
}

/// Verify the signed Stage manifest before any mutable product Python is imported.
/// The result contains only a fixed diagnostic code, count and short manifest id.
#[pyfunction]
fn verify_local_integrity(package_root: &str) -> (bool, String, usize, String) {
    match verify_local_integrity_impl(Path::new(package_root)) {
        Ok((count, manifest_id)) => (true, "ok".to_string(), count, manifest_id),
        Err(code) => (false, code.to_string(), 0, String::new()),
    }
}

/// Re-enumerate signed coverage after the launcher has sealed every package
/// directory DACL.  This catches a child inserted between the first coverage pass
/// and the parent-before-child namespace seal before product code is imported.
#[pyfunction]
fn recheck_local_integrity(package_root: &str) -> (bool, String) {
    match recheck_local_integrity_impl(Path::new(package_root)) {
        Ok(()) => (true, "ok".to_string()),
        Err(code) => (false, code.to_string()),
    }
}

/// Drop retained file/directory handles only after the namespace lease restored the
/// original DACLs.  The generated launcher owns that ordering in a finally block.
#[pyfunction]
fn release_local_integrity() -> (bool, String) {
    let handle_result = VERIFIED_LOCAL_INTEGRITY_HANDLES.lock();
    let namespace_result = VERIFIED_LOCAL_INTEGRITY_NAMESPACE.lock();
    match (handle_result, namespace_result) {
        (Ok(mut handles), Ok(mut namespace)) => {
            handles.clear();
            *namespace = None;
            (true, "ok".to_string())
        }
        _ => (false, "integrity_handle_release_failed".to_string()),
    }
}

#[pyfunction]
fn kernel_version() -> &'static str {
    "bidking_kernel 0.1.0"
}

/// 返回编译期内嵌的出厂数据主密钥 K(32 字节 AES-256), 用于解密包内 moat 数据(掉率/物品表/先验)。
/// 启动即可用(不依赖激活)-> moat 解密无竞态。dev 编译未嵌 key -> 抛错 -> resolve_key() 回退 env。
/// K 只存在于编译后的二进制(strip+LTO 高逆向成本), 不落 git 源码 / 明文文件。
#[pyfunction]
fn data_key<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
    match DATA_KEY {
        Some(k) => Ok(PyBytes::new(py, &k)),
        None => Err(pyo3::exceptions::PyRuntimeError::new_err(
            "kernel data_key not embedded (dev build)",
        )),
    }
}

/// 设备指纹: Windows MachineGuid(注册表) 的 sha256 hex 前 32 字符。取不到 -> "unknown-device" 的 hash。
#[pyfunction]
fn device_fingerprint() -> PyResult<String> {
    let raw = read_machine_guid().unwrap_or_else(|| "unknown-device".to_string());
    let mut h = Sha256::new();
    h.update(b"bidking-dev-v1:");
    h.update(raw.as_bytes());
    Ok(hex::encode(h.finalize())[..32].to_string())
}

/// 从激活 token + 设备指纹 派生 32 字节 AES-256 data key(HKDF-SHA256, 确定性)。
/// key 在 native 计算后才回 Python(bytes), 不存在 Python 明文常量里。
#[pyfunction]
fn derive_data_key<'py>(py: Python<'py>, token: &str, device: &str) -> PyResult<Bound<'py, PyBytes>> {
    let ikm = format!("{token}|{device}");
    let hk = Hkdf::<Sha256>::new(Some(b"bidking-data-key-v1"), ikm.as_bytes());
    let mut okm = [0u8; 32];
    hk.expand(b"aes-256-data", &mut okm)
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("hkdf expand failed"))?;
    Ok(PyBytes::new(py, &okm))
}

/// 校验激活 token 的 HMAC 签名。token = "payload.sighex"; 验 HMAC-SHA256(secret, payload) == sig。
/// groundwork: secret 由调用方给(后续服务端权威 + 设备绑定); 常数时间比较防时序。
#[pyfunction]
fn verify_activation(token: &str, secret: &[u8]) -> bool {
    let mut it = token.rsplitn(2, '.');
    let sig_hex = match it.next() {
        Some(s) => s,
        None => return false,
    };
    let payload = match it.next() {
        Some(p) => p,
        None => return false,
    };
    let sig = match hex::decode(sig_hex) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let mut mac = match HmacSha256::new_from_slice(secret) {
        Ok(m) => m,
        Err(_) => return false,
    };
    mac.update(payload.as_bytes());
    mac.verify_slice(&sig).is_ok()
}

#[cfg(windows)]
fn read_machine_guid() -> Option<String> {
    use winreg::RegKey;
    use winreg::enums::HKEY_LOCAL_MACHINE;
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let key = hklm.open_subkey(r"SOFTWARE\Microsoft\Cryptography").ok()?;
    key.get_value::<String, _>("MachineGuid").ok()
}

#[cfg(not(windows))]
fn read_machine_guid() -> Option<String> {
    None
}

#[pymodule]
fn bidking_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(kernel_version, m)?)?;
    m.add_function(wrap_pyfunction!(device_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(derive_data_key, m)?)?;
    m.add_function(wrap_pyfunction!(data_key, m)?)?;
    m.add_function(wrap_pyfunction!(verify_activation, m)?)?;
    m.add_function(wrap_pyfunction!(verify_local_integrity, m)?)?;
    m.add_function(wrap_pyfunction!(recheck_local_integrity, m)?)?;
    m.add_function(wrap_pyfunction!(release_local_integrity, m)?)?;
    m.add_function(wrap_pyfunction!(startup_timing_event, m)?)?;
    m.add_function(wrap_pyfunction!(client_lifecycle_contract, m)?)?;
    m.add_function(wrap_pyfunction!(run_client_lifecycle, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{WINDOWS_ZONE_IDENTIFIER_MAX_BYTES, valid_windows_zone_identifier};

    #[test]
    fn accepts_bounded_internet_zone_metadata() {
        for payload in [
            b"[ZoneTransfer]\r\nZoneId=3\r\n".as_slice(),
            b"\xef\xbb\xbf[zonetransfer]\nzoneid = 4\nHostUrl=https://example.test/a?b=c\n"
                .as_slice(),
            b"; browser metadata\n[ZoneTransfer]\nReferrerUrl=https://example.test/\nZoneId=3\n"
                .as_slice(),
        ] {
            assert!(valid_windows_zone_identifier(payload));
        }
    }

    #[test]
    fn rejects_non_internet_or_malformed_zone_metadata() {
        for payload in [
            b"".as_slice(),
            b"[ZoneTransfer]\nZoneId=2\n".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\nZoneId=4\n".as_slice(),
            b"[Other]\nZoneId=3\n".as_slice(),
            b"ZoneId=3\n".as_slice(),
            b"[ZoneTransfer]\nBad Key=value\nZoneId=3\n".as_slice(),
            b"[ZoneTransfer]\0ZoneId=3\n".as_slice(),
            b"\xff[ZoneTransfer]\nZoneId=3\n".as_slice(),
        ] {
            assert!(!valid_windows_zone_identifier(payload));
        }
        assert!(!valid_windows_zone_identifier(&vec![
            b'A';
            WINDOWS_ZONE_IDENTIFIER_MAX_BYTES + 1
        ]));
    }

    #[test]
    fn accepts_one_terminal_nul_without_mutating_input() {
        for payload in [
            b"[ZoneTransfer]\r\nZoneId=3\r\n\0".as_slice(),
            b"\xef\xbb\xbf[zonetransfer]\nzoneid = 4\nHostUrl=https://example.test/a?b=c\0"
                .as_slice(),
            "[ZoneTransfer]\nZoneId=3\nHostUrl=https://example.test/中文.zip\n\0"
                .as_bytes(),
        ] {
            let original = payload.to_vec();
            assert!(valid_windows_zone_identifier(payload));
            assert!(valid_windows_zone_identifier(&payload[..payload.len() - 1]));
            assert_eq!(payload, original.as_slice());
        }
    }

    #[test]
    fn rejects_embedded_repeated_or_hidden_suffix_nul() {
        for payload in [
            b"\0".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\n\0\0".as_slice(),
            b"[ZoneTransfer]\0\nZoneId=3\n".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\n\0hidden".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\n\0hidden\0".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\n\0\r\n".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\nZoneId=4\n\0".as_slice(),
            b"[ZoneTransfer]\nZoneId=2\n\0".as_slice(),
            b"[ZoneTransfer]\nZoneId=3\nHostUrl=\xff\0".as_slice(),
        ] {
            assert!(!valid_windows_zone_identifier(payload));
        }
    }

    #[test]
    fn terminal_nul_counts_toward_stream_byte_limit() {
        let mut payload = b"[ZoneTransfer]\nZoneId=3\n;".to_vec();
        payload.resize(WINDOWS_ZONE_IDENTIFIER_MAX_BYTES - 1, b'a');
        payload.push(0);
        assert_eq!(payload.len(), WINDOWS_ZONE_IDENTIFIER_MAX_BYTES);
        assert!(valid_windows_zone_identifier(&payload));
        payload.insert(payload.len() - 1, b'a');
        assert_eq!(payload.len(), WINDOWS_ZONE_IDENTIFIER_MAX_BYTES + 1);
        assert!(!valid_windows_zone_identifier(&payload));
    }

    #[cfg(windows)]
    #[test]
    fn windows_stream_snapshot_accepts_only_stable_zone_identifier() {
        use super::snapshot_file_streams;
        use std::ffi::OsString;
        use std::fs;
        use std::time::{SystemTime, UNIX_EPOCH};

        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "bidking-zone-stream-test-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir(&root).expect("create test root");
        let member = root.join("member.bin");
        fs::write(&member, b"SIGNED-DATA").expect("write member");
        let stream_path = |suffix: &str| {
            let mut value = OsString::from(member.as_os_str());
            value.push(suffix);
            std::path::PathBuf::from(value)
        };
        let zone = stream_path(":Zone.Identifier");
        fs::write(&zone, b"[ZoneTransfer]\r\nZoneId=3\r\n").expect("write zone");

        let (before, handles) = snapshot_file_streams(&member).expect("valid zone");
        assert!(before.zone_identifier.is_some());
        assert!(fs::write(&zone, b"[ZoneTransfer]\r\nZoneId=4\r\n").is_err());
        drop(handles);
        fs::write(&zone, b"[ZoneTransfer]\r\nZoneId=4\r\n").expect("change zone");
        let (after, handles) = snapshot_file_streams(&member).expect("valid changed zone");
        assert!(before.zone_identifier != after.zone_identifier);
        drop(handles);

        fs::write(&zone, b"[ZoneTransfer]\r\nZoneId=2\r\n").expect("write invalid zone");
        assert_eq!(
            snapshot_file_streams(&member).err(),
            Some("integrity_named_stream_rejected")
        );
        fs::write(&zone, b"[ZoneTransfer]\r\nZoneId=3\r\n").expect("restore zone");
        let custom = stream_path(":hidden");
        fs::write(&custom, b"HIDDEN").expect("write custom stream");
        assert_eq!(
            snapshot_file_streams(&member).err(),
            Some("integrity_named_stream_rejected")
        );

        fs::remove_file(&member).expect("remove member and streams");
        fs::remove_dir(&root).expect("remove test root");
    }
}
