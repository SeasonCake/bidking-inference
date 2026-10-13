use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn base64_32(name: &str) -> Option<[u8; 32]> {
    let value = env::var(name).ok()?;
    let compact: Vec<u8> = value
        .bytes()
        .filter(|byte| !byte.is_ascii_whitespace())
        .collect();
    if compact.len() != 44 || compact[43] != b'=' {
        panic!("{name} must be one base64-encoded 32-byte Ed25519 public key");
    }
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
    let mut decoded = Vec::with_capacity(32);
    for chunk in compact.chunks_exact(4) {
        let first = digit(chunk[0]).expect("validated base64");
        let second = digit(chunk[1]).expect("validated base64");
        decoded.push((first << 2) | (second >> 4));
        if chunk[2] != b'=' {
            let third = digit(chunk[2]).expect("validated base64");
            decoded.push((second << 4) | (third >> 2));
            if chunk[3] != b'=' {
                let fourth = digit(chunk[3]).expect("validated base64");
                decoded.push((third << 6) | fourth);
            }
        }
    }
    let decoded: [u8; 32] = decoded
        .try_into()
        .unwrap_or_else(|_| panic!("{name} must decode to exactly 32 bytes"));
    Some(decoded)
}

fn literal(value: Option<[u8; 32]>) -> String {
    match value {
        Some(bytes) => format!(
            "Some([{}])",
            bytes
                .iter()
                .map(u8::to_string)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        None => "None".to_string(),
    }
}

fn resource_compiler() -> PathBuf {
    if let Some(path) = env::var_os("PATH").and_then(|value| {
        env::split_paths(&value)
            .map(|directory| directory.join("rc.exe"))
            .find(|candidate| candidate.is_file())
    }) {
        return path;
    }
    let program_files = env::var_os("ProgramFiles(x86)")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Program Files (x86)"));
    let bin_root = program_files.join("Windows Kits").join("10").join("bin");
    let mut candidates = fs::read_dir(&bin_root)
        .unwrap_or_else(|_| panic!("Windows SDK resource compiler unavailable"))
        .filter_map(Result::ok)
        .map(|entry| entry.path().join("x64").join("rc.exe"))
        .filter(|candidate| candidate.is_file())
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| right.cmp(left));
    candidates
        .into_iter()
        .next()
        .unwrap_or_else(|| panic!("Windows SDK resource compiler unavailable"))
}

fn embed_windows_icon() {
    let manifest_dir = PathBuf::from(
        env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"),
    );
    let icon = manifest_dir
        .join("..")
        .join("..")
        .join("hero_ref")
        .join("assets")
        .join("bidking_cat.ico")
        .canonicalize()
        .unwrap_or_else(|_| panic!("BidKing launcher icon unavailable"));
    println!("cargo:rerun-if-changed={}", icon.display());
    let out = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR"));
    let script = out.join("bidking_launcher.rc");
    let resource = out.join("bidking_launcher.res");
    let icon_path = icon.to_string_lossy().replace('\\', "/");
    fs::write(&script, format!("1 ICON \"{icon_path}\"\r\n"))
        .expect("write BidKing launcher resource script");
    let status = Command::new(resource_compiler())
        .arg("/nologo")
        .arg(format!("/fo{}", resource.display()))
        .arg(&script)
        .status()
        .expect("run Windows SDK resource compiler");
    if !status.success() || !resource.is_file() {
        panic!("compile BidKing launcher resources failed");
    }
    println!(
        "cargo:rustc-link-arg-bin=bidking_trust_bootstrap={}",
        resource.display()
    );
}

fn main() {
    println!("cargo:rerun-if-env-changed=BIDKING_INTEGRITY_PUBLIC_KEY_B64");
    if cfg!(windows) {
        embed_windows_icon();
        println!(
            "cargo:rustc-link-arg-bin=bidking_trust_bootstrap=/MANIFEST:EMBED"
        );
        println!(
            "cargo:rustc-link-arg-bin=bidking_trust_bootstrap=/MANIFESTUAC:level='requireAdministrator' uiAccess='false'"
        );
    }
    let release = env::var("PROFILE").as_deref() == Ok("release");
    let public_key = base64_32("BIDKING_INTEGRITY_PUBLIC_KEY_B64");
    if release && public_key.is_none() {
        panic!("release bootstrap requires an explicit Ed25519 public-key identity");
    }
    let generated = format!(
        "pub const INTEGRITY_PUBLIC_KEY: Option<[u8; 32]> = {};\n",
        literal(public_key),
    );
    let out = env::var("OUT_DIR").expect("OUT_DIR");
    fs::write(Path::new(&out).join("embedded_trust.rs"), generated)
        .expect("write embedded trust constants");
}
