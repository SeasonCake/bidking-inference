// build.rs — 编译期把出厂数据主密钥 K 埋进 .pyd(反明文落盘)。
//
// K 来源: 环境变量 BIDKING_DATA_KEY(base64 of 32 bytes)。打包脚本从 -MasterKeyFile 读出后设进 env,
// 于是 maturin/cargo 编译内核时 build.rs 把它解码成 32 字节常量嵌进二进制; 源码/dev 编译(无 env)
// 则嵌 None -> data_key() 返回错误 -> resolve_key() 回退 env(groundwork/测试)。
//
// 这样 K 只在【编译后的二进制里】(strip+LTO 高逆向成本), 不在 git 源码、不在明文文件。
use std::env;
use std::fs;
use std::path::Path;
use sha2::{Digest, Sha256};

const LIFECYCLE_DOMAIN: &[u8] = b"bidking-client-lifecycle-v2\0";
const LIFECYCLE_SEAL_DOMAIN: &[u8] = b"bidking-client-lifecycle-seal-v2\0";

fn main() {
    // env 变了要重跑(否则 cargo 缓存旧 key)。
    println!("cargo:rerun-if-env-changed=BIDKING_DATA_KEY");
    println!("cargo:rerun-if-env-changed=BIDKING_INTEGRITY_PUBLIC_KEY_B64");

    let out_dir = env::var("OUT_DIR").expect("OUT_DIR");
    let dest = Path::new(&out_dir).join("embedded_key.rs");

    let data_key = match env::var("BIDKING_DATA_KEY") {
        Ok(b64) if !b64.trim().is_empty() => {
            let raw = base64_decode(b64.trim())
                .unwrap_or_else(|e| panic!("BIDKING_DATA_KEY base64 解码失败: {e}"));
            assert_eq!(raw.len(), 32, "BIDKING_DATA_KEY 必须是 32 字节(AES-256), 实为 {}", raw.len());
            let items = raw
                .iter()
                .map(|b| b.to_string())
                .collect::<Vec<_>>()
                .join(", ");
            format!("pub const DATA_KEY: Option<[u8; 32]> = Some([{items}]);\n")
        }
        _ => "pub const DATA_KEY: Option<[u8; 32]> = None;\n".to_string(),
    };
    let integrity_public_key = match env::var("BIDKING_INTEGRITY_PUBLIC_KEY_B64") {
        Ok(b64) if !b64.trim().is_empty() => {
            let raw = base64_decode(b64.trim()).unwrap_or_else(|e| {
                panic!("BIDKING_INTEGRITY_PUBLIC_KEY_B64 base64 解码失败: {e}")
            });
            assert_eq!(
                raw.len(),
                32,
                "BIDKING_INTEGRITY_PUBLIC_KEY_B64 必须是 32 字节 Ed25519 公钥, 实为 {}",
                raw.len()
            );
            let items = raw
                .iter()
                .map(|b| b.to_string())
                .collect::<Vec<_>>()
                .join(", ");
            format!(
                "pub const INTEGRITY_PUBLIC_KEY: Option<[u8; 32]> = Some([{items}]);\n"
            )
        }
        _ => "pub const INTEGRITY_PUBLIC_KEY: Option<[u8; 32]> = None;\n".to_string(),
    };
    let body = format!("{data_key}{integrity_public_key}");
    fs::write(&dest, body).expect("写 embedded_key.rs 失败");
    write_client_lifecycle(Path::new(&out_dir));
}

fn write_client_lifecycle(out_dir: &Path) {
    const LAUNCHER_ENV: &str = "BIDKING_LIFECYCLE_LAUNCHER_FILE";
    const DIAGNOSTICS_ENV: &str = "BIDKING_LIFECYCLE_DIAGNOSTICS_FILE";
    const REPAIR_ENV: &str = "BIDKING_LIFECYCLE_REPAIR_FILE";
    const GENERATION_ENV: &str = "BIDKING_LIFECYCLE_GENERATION";
    for name in [LAUNCHER_ENV, DIAGNOSTICS_ENV, REPAIR_ENV, GENERATION_ENV] {
        println!("cargo:rerun-if-env-changed={name}");
    }

    let launcher_path = env::var_os(LAUNCHER_ENV);
    let diagnostics_path = env::var_os(DIAGNOSTICS_ENV);
    let repair_path = env::var_os(REPAIR_ENV);
    let expected_generation = env::var(GENERATION_ENV).ok();
    let destination = out_dir.join("embedded_lifecycle.rs");
    if launcher_path.is_none()
        && diagnostics_path.is_none()
        && repair_path.is_none()
        && expected_generation.is_none()
    {
        fs::write(destination, empty_lifecycle_source())
            .expect("写空 client lifecycle 失败");
        return;
    }
    if launcher_path.is_none()
        || diagnostics_path.is_none()
        || repair_path.is_none()
        || expected_generation.is_none()
    {
        panic!("client lifecycle build inputs incomplete");
    }

    let launcher_path = launcher_path.expect("checked launcher path");
    let diagnostics_path = diagnostics_path.expect("checked diagnostics path");
    let repair_path = repair_path.expect("checked repair path");
    println!("cargo:rerun-if-changed={}", Path::new(&launcher_path).display());
    println!(
        "cargo:rerun-if-changed={}",
        Path::new(&diagnostics_path).display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        Path::new(&repair_path).display()
    );
    let launcher = fs::read(&launcher_path).expect("read lifecycle launcher input");
    let diagnostics = fs::read(&diagnostics_path).expect("read lifecycle diagnostics input");
    let repair = fs::read(&repair_path).expect("read lifecycle repair input");
    if launcher.is_empty() || diagnostics.is_empty() || repair.is_empty() {
        panic!("client lifecycle payload empty");
    }
    if launcher.len() > 4 * 1024 * 1024
        || diagnostics.len() > 4 * 1024 * 1024
        || repair.len() > 4 * 1024 * 1024
    {
        panic!("client lifecycle payload exceeds build contract");
    }

    let generation_bytes = lifecycle_generation(&launcher, &diagnostics, &repair);
    let generation = hex_lower(&generation_bytes);
    if expected_generation.as_deref() != Some(generation.as_str()) {
        panic!("client lifecycle generation mismatch");
    }
    let launcher_hash = sha256(&launcher);
    let diagnostics_hash = sha256(&diagnostics);
    let repair_hash = sha256(&repair);
    let launcher_sealed = seal_payload(&launcher, &generation_bytes, b"launcher");
    let diagnostics_sealed = seal_payload(&diagnostics, &generation_bytes, b"diagnostics");
    let repair_sealed = seal_payload(&repair, &generation_bytes, b"repair");

    let source = format!(
        "pub const CLIENT_LIFECYCLE_EMBEDDED: bool = true;\n\
         pub const CLIENT_LIFECYCLE_ABI: u32 = 2;\n\
         pub const CLIENT_LIFECYCLE_GENERATION: &str = {generation:?};\n\
         pub static CLIENT_LIFECYCLE_LAUNCHER_SEALED: &[u8] = &{launcher_sealed:?};\n\
         pub const CLIENT_LIFECYCLE_LAUNCHER_SHA256: [u8; 32] = {launcher_hash:?};\n\
         pub static CLIENT_LIFECYCLE_DIAGNOSTICS_SEALED: &[u8] = &{diagnostics_sealed:?};\n\
         pub const CLIENT_LIFECYCLE_DIAGNOSTICS_SHA256: [u8; 32] = {diagnostics_hash:?};\n\
         pub static CLIENT_LIFECYCLE_REPAIR_SEALED: &[u8] = &{repair_sealed:?};\n\
         pub const CLIENT_LIFECYCLE_REPAIR_SHA256: [u8; 32] = {repair_hash:?};\n"
    );
    fs::write(destination, source).expect("写 client lifecycle 失败");
}

fn empty_lifecycle_source() -> &'static str {
    "pub const CLIENT_LIFECYCLE_EMBEDDED: bool = false;\n\
     pub const CLIENT_LIFECYCLE_ABI: u32 = 2;\n\
     pub const CLIENT_LIFECYCLE_GENERATION: &str = \"\";\n\
     pub static CLIENT_LIFECYCLE_LAUNCHER_SEALED: &[u8] = &[];\n\
     pub const CLIENT_LIFECYCLE_LAUNCHER_SHA256: [u8; 32] = [0; 32];\n\
     pub static CLIENT_LIFECYCLE_DIAGNOSTICS_SEALED: &[u8] = &[];\n\
     pub const CLIENT_LIFECYCLE_DIAGNOSTICS_SHA256: [u8; 32] = [0; 32];\n\
     pub static CLIENT_LIFECYCLE_REPAIR_SEALED: &[u8] = &[];\n\
     pub const CLIENT_LIFECYCLE_REPAIR_SHA256: [u8; 32] = [0; 32];\n"
}

fn lifecycle_generation(launcher: &[u8], diagnostics: &[u8], repair: &[u8]) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(LIFECYCLE_DOMAIN);
    for payload in [launcher, diagnostics, repair] {
        digest.update((payload.len() as u64).to_le_bytes());
        digest.update(payload);
    }
    digest.finalize().into()
}

fn sha256(payload: &[u8]) -> [u8; 32] {
    Sha256::digest(payload).into()
}

fn seal_payload(payload: &[u8], generation: &[u8; 32], label: &[u8]) -> Vec<u8> {
    let mut output = Vec::with_capacity(payload.len());
    for (counter, chunk) in payload.chunks(32).enumerate() {
        let mut digest = Sha256::new();
        digest.update(LIFECYCLE_SEAL_DOMAIN);
        digest.update(generation);
        digest.update((label.len() as u64).to_le_bytes());
        digest.update(label);
        digest.update((counter as u64).to_le_bytes());
        let key: [u8; 32] = digest.finalize().into();
        output.extend(chunk.iter().zip(key).map(|(byte, mask)| *byte ^ mask));
    }
    output
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

// 极简 base64 解码(标准字母表, 支持 '=' padding)。避免为 build 加第三方依赖。
fn base64_decode(s: &str) -> Result<Vec<u8>, String> {
    fn val(c: u8) -> Result<u8, String> {
        match c {
            b'A'..=b'Z' => Ok(c - b'A'),
            b'a'..=b'z' => Ok(c - b'a' + 26),
            b'0'..=b'9' => Ok(c - b'0' + 52),
            b'+' => Ok(62),
            b'/' => Ok(63),
            _ => Err(format!("非法 base64 字符: {c:#x}")),
        }
    }
    let bytes: Vec<u8> = s.bytes().filter(|&c| c != b'\n' && c != b'\r').collect();
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for chunk in bytes.chunks(4) {
        if chunk.len() < 2 {
            return Err("base64 长度不合法".into());
        }
        let b0 = val(chunk[0])?;
        let b1 = val(chunk[1])?;
        out.push((b0 << 2) | (b1 >> 4));
        if chunk.len() >= 3 && chunk[2] != b'=' {
            let b2 = val(chunk[2])?;
            out.push((b1 << 4) | (b2 >> 2));
            if chunk.len() >= 4 && chunk[3] != b'=' {
                let b3 = val(chunk[3])?;
                out.push((b2 << 6) | b3);
            }
        }
    }
    Ok(out)
}
