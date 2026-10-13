"""Build and verify the protected cross-runtime mitmproxy addon bootstrap.

The public client runs its monitor with the bundled Python 3.13 runtime, while
the pinned mitmproxy binary embeds its own Python runtime (currently 3.14).
Regular CPython extensions built for the client interpreter cannot be imported
by mitmproxy.  The release build therefore stores the stdlib-only sanitizer and
addon as AES-GCM ciphertext inside a Windows stable-ABI extension and ships only
a tiny interpreter-neutral Python loader.

This is release-at-rest protection, not a claim that runnable client code can
be made impossible to recover.  The native boundary prevents a ZIP recipient
from restoring the implementation with a simple base64/gzip decode, while the
existing runtime smoke proves that both interpreter sides can still load it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
import secrets
from pathlib import Path
import subprocess

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


WSS_OPERATIONAL_SCHEMA = "bidking_wss_operational_abi_v1"


@dataclass(frozen=True)
class WssOperationalContract:
    abi_version: str
    generation_sha256: str
    native_module: str
    entrypoint: str
    runtime_roles: dict[str, str]
    source_members: dict[str, str]
    bootstrap_sha256: str


_BOOTSTRAP = r"""from __future__ import annotations

import os
from pathlib import Path

from _local_compat_addon_loader import load_addons as _load_addons


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if type(value) is not str or not value:
        raise RuntimeError("wss operational identity unavailable")
    return value


_package_root = str(Path(__file__).resolve().parents[1])
addons = _load_addons(
    _required_environment("BIDKING_WSS_OPERATIONAL_ABI"),
    _package_root,
    _required_environment("BIDKING_WSS_PACKAGE_ROOT"),
    _required_environment("BIDKING_WSS_PACKAGE_GENERATION"),
    _required_environment("BIDKING_WSS_OPERATIONAL_GENERATION"),
    _required_environment("BIDKING_WSS_RUNTIME_ROLE"),
)
del _load_addons, _package_root, _required_environment
"""

_NATIVE_TEMPLATE = r"""#define PY_SSIZE_T_CLEAN
#define Py_LIMITED_API 0x030D0000
#include <Python.h>
#include <windows.h>
#include <bcrypt.h>
#include <string.h>

#pragma comment(lib, "bcrypt.lib")

__KEY_SHARE_A__
__KEY_SHARE_B__
__PAYLOAD_0__
__NONCE_0__
__TAG_0__
__PAYLOAD_1__
__NONCE_1__
__TAG_1__
__OPERATIONAL_ABI__
__OPERATIONAL_GENERATION__

static int decrypt_payload(
    const unsigned char *ciphertext,
    DWORD ciphertext_len,
    const unsigned char *nonce,
    DWORD nonce_len,
    const unsigned char *tag,
    DWORD tag_len,
    unsigned char **plaintext_out,
    DWORD *plaintext_len_out
) {
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_KEY_HANDLE key_handle = NULL;
    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO auth_info;
    unsigned char key_bytes[32];
    unsigned char *key_object = NULL;
    unsigned char *plaintext = NULL;
    DWORD key_object_len = 0;
    DWORD result_len = 0;
    DWORD property_len = 0;
    NTSTATUS status;
    int ok = 0;
    size_t index;

    for (index = 0; index < sizeof(key_bytes); ++index) {
        key_bytes[index] = KEY_SHARE_A[index] ^ KEY_SHARE_B[index];
    }
    status = BCryptOpenAlgorithmProvider(
        &algorithm, BCRYPT_AES_ALGORITHM, NULL, 0
    );
    if (status < 0) {
        goto cleanup;
    }
    status = BCryptSetProperty(
        algorithm,
        BCRYPT_CHAINING_MODE,
        (PUCHAR)BCRYPT_CHAIN_MODE_GCM,
        sizeof(BCRYPT_CHAIN_MODE_GCM),
        0
    );
    if (status < 0) {
        goto cleanup;
    }
    status = BCryptGetProperty(
        algorithm,
        BCRYPT_OBJECT_LENGTH,
        (PUCHAR)&key_object_len,
        sizeof(key_object_len),
        &property_len,
        0
    );
    if (status < 0 || key_object_len == 0) {
        goto cleanup;
    }
    key_object = (unsigned char *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, key_object_len
    );
    plaintext = (unsigned char *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, ciphertext_len + 1
    );
    if (key_object == NULL || plaintext == NULL) {
        goto cleanup;
    }
    status = BCryptGenerateSymmetricKey(
        algorithm,
        &key_handle,
        key_object,
        key_object_len,
        key_bytes,
        sizeof(key_bytes),
        0
    );
    if (status < 0) {
        goto cleanup;
    }
    BCRYPT_INIT_AUTH_MODE_INFO(auth_info);
    auth_info.pbNonce = (PUCHAR)nonce;
    auth_info.cbNonce = nonce_len;
    auth_info.pbTag = (PUCHAR)tag;
    auth_info.cbTag = tag_len;
    status = BCryptDecrypt(
        key_handle,
        (PUCHAR)ciphertext,
        ciphertext_len,
        &auth_info,
        NULL,
        0,
        plaintext,
        ciphertext_len,
        &result_len,
        0
    );
    if (status < 0 || result_len != ciphertext_len) {
        goto cleanup;
    }
    *plaintext_out = plaintext;
    *plaintext_len_out = result_len;
    plaintext = NULL;
    ok = 1;

cleanup:
    SecureZeroMemory(key_bytes, sizeof(key_bytes));
    if (plaintext != NULL) {
        SecureZeroMemory(plaintext, ciphertext_len + 1);
        HeapFree(GetProcessHeap(), 0, plaintext);
    }
    if (key_handle != NULL) {
        BCryptDestroyKey(key_handle);
    }
    if (key_object != NULL) {
        SecureZeroMemory(key_object, key_object_len);
        HeapFree(GetProcessHeap(), 0, key_object);
    }
    if (algorithm != NULL) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    return ok;
}

static int exact_unicode_utf8(
    PyObject *value,
    const char **text_out,
    Py_ssize_t *length_out
) {
    if (!PyUnicode_CheckExact(value)) {
        return 0;
    }
    *text_out = PyUnicode_AsUTF8AndSize(value, length_out);
    return *text_out != NULL;
}

static int fixed_ascii_equal(
    PyObject *value,
    const char *expected
) {
    const char *actual = NULL;
    Py_ssize_t actual_len = 0;
    size_t expected_len = strlen(expected);
    if (!exact_unicode_utf8(value, &actual, &actual_len)) {
        return 0;
    }
    return actual_len == (Py_ssize_t)expected_len
        && memcmp(actual, expected, expected_len) == 0;
}

static int windows_path_equal(PyObject *left, PyObject *right) {
    const wchar_t *left_text = NULL;
    const wchar_t *right_text = NULL;
    Py_ssize_t left_len = 0;
    Py_ssize_t right_len = 0;
    int comparison;
    if (!PyUnicode_CheckExact(left) || !PyUnicode_CheckExact(right)) {
        return 0;
    }
    left_text = PyUnicode_AsWideCharString(left, &left_len);
    if (left_text == NULL) {
        return 0;
    }
    right_text = PyUnicode_AsWideCharString(right, &right_len);
    if (right_text == NULL) {
        PyMem_Free((void *)left_text);
        return 0;
    }
    comparison = (
        left_len == right_len
        ? CompareStringOrdinal(
              left_text,
              (int)left_len,
              right_text,
              (int)right_len,
              TRUE
          )
        : 0
    );
    PyMem_Free((void *)right_text);
    PyMem_Free((void *)left_text);
    return comparison == CSTR_EQUAL;
}

static int valid_generation(PyObject *value) {
    const char *text = NULL;
    Py_ssize_t length = 0;
    Py_ssize_t index;
    if (!exact_unicode_utf8(value, &text, &length) || length != 64) {
        return 0;
    }
    for (index = 0; index < length; ++index) {
        if (!((text[index] >= '0' && text[index] <= '9')
                || (text[index] >= 'a' && text[index] <= 'f'))) {
            return 0;
        }
    }
    return 1;
}

static int runtime_role_matches(PyObject *runtime_role) {
    PyObject *sys_module = NULL;
    PyObject *implementation = NULL;
    PyObject *cache_tag = NULL;
    int matched = 0;
    sys_module = PyImport_ImportModule("sys");
    if (sys_module == NULL) {
        goto cleanup;
    }
    implementation = PyObject_GetAttrString(sys_module, "implementation");
    if (implementation == NULL) {
        goto cleanup;
    }
    cache_tag = PyObject_GetAttrString(implementation, "cache_tag");
    if (cache_tag == NULL || !PyUnicode_CheckExact(cache_tag)) {
        goto cleanup;
    }
    if (fixed_ascii_equal(runtime_role, "main")) {
        matched = fixed_ascii_equal(cache_tag, "cpython-313");
    } else if (fixed_ascii_equal(runtime_role, "mitmdump")) {
        matched = fixed_ascii_equal(cache_tag, "cpython-314");
    }

cleanup:
    Py_XDECREF(cache_tag);
    Py_XDECREF(implementation);
    Py_XDECREF(sys_module);
    return matched;
}

static PyObject *execute_payload_module(
    const char *module_name,
    int payload_index
) {
    const unsigned char *ciphertext = NULL;
    const unsigned char *nonce = NULL;
    const unsigned char *tag = NULL;
    DWORD ciphertext_len = 0;
    unsigned char *plaintext = NULL;
    DWORD plaintext_len = 0;
    PyObject *code = NULL;
    PyObject *module = NULL;
    PyObject *module_dict = NULL;
    PyObject *modules = NULL;
    PyObject *evaluated = NULL;
    PyObject *module_name_object = NULL;
    PyObject *package_name = NULL;
    PyObject *source_name = NULL;
    int inserted = 0;

    if (payload_index == 0) {
        ciphertext = PAYLOAD_0;
        ciphertext_len = (DWORD)sizeof(PAYLOAD_0);
        nonce = NONCE_0;
        tag = TAG_0;
    } else if (payload_index == 1) {
        ciphertext = PAYLOAD_1;
        ciphertext_len = (DWORD)sizeof(PAYLOAD_1);
        nonce = NONCE_1;
        tag = TAG_1;
    } else {
        goto cleanup;
    }
    if (!decrypt_payload(
            ciphertext,
            ciphertext_len,
            nonce,
            12,
            tag,
            16,
            &plaintext,
            &plaintext_len
        )) {
        goto cleanup;
    }
    code = Py_CompileString(
        (const char *)plaintext,
        "<bidking-wss-operational>",
        Py_file_input
    );
    SecureZeroMemory(plaintext, plaintext_len);
    HeapFree(GetProcessHeap(), 0, plaintext);
    plaintext = NULL;
    if (code == NULL) {
        goto cleanup;
    }
    module = PyModule_New(module_name);
    if (module == NULL) {
        goto cleanup;
    }
    module_dict = PyModule_GetDict(module);
    if (module_dict == NULL) {
        goto cleanup;
    }
    package_name = PyUnicode_FromString("");
    source_name = PyUnicode_FromString("<bidking-wss-operational>");
    if (
        package_name == NULL
        || source_name == NULL
        || PyDict_SetItemString(module_dict, "__package__", package_name) < 0
        || PyDict_SetItemString(module_dict, "__file__", source_name) < 0
    ) {
        goto cleanup;
    }
    modules = PyImport_GetModuleDict();
    module_name_object = PyUnicode_FromString(module_name);
    if (modules == NULL || module_name_object == NULL) {
        goto cleanup;
    }
    if (PyDict_SetItem(modules, module_name_object, module) < 0) {
        goto cleanup;
    }
    inserted = 1;
    evaluated = PyEval_EvalCode(code, module_dict, module_dict);
    if (evaluated == NULL) {
        goto cleanup;
    }
    Py_DECREF(evaluated);
    Py_DECREF(code);
    Py_DECREF(module_name_object);
    Py_DECREF(package_name);
    Py_DECREF(source_name);
    return module;

cleanup:
    if (plaintext != NULL) {
        SecureZeroMemory(plaintext, plaintext_len);
        HeapFree(GetProcessHeap(), 0, plaintext);
    }
    if (inserted && modules != NULL && module_name_object != NULL) {
        if (PyDict_DelItem(modules, module_name_object) < 0) {
            PyErr_Clear();
        }
    }
    Py_XDECREF(evaluated);
    Py_XDECREF(code);
    Py_XDECREF(module_name_object);
    Py_XDECREF(package_name);
    Py_XDECREF(source_name);
    Py_XDECREF(module);
    return NULL;
}

static PyObject *load_addons(PyObject *self, PyObject *args) {
    PyObject *abi = NULL;
    PyObject *bootstrap_root = NULL;
    PyObject *package_root = NULL;
    PyObject *package_generation = NULL;
    PyObject *operational_generation = NULL;
    PyObject *runtime_role = NULL;
    PyObject *core_module = NULL;
    PyObject *addon_module = NULL;
    PyObject *addons = NULL;

    if (!PyArg_UnpackTuple(
            args,
            "load_addons",
            6,
            6,
            &abi,
            &bootstrap_root,
            &package_root,
            &package_generation,
            &operational_generation,
            &runtime_role
        )) {
        PyErr_Clear();
        PyErr_SetString(PyExc_RuntimeError, "wss operational identity rejected");
        return NULL;
    }
    if (
        !fixed_ascii_equal(abi, OPERATIONAL_ABI)
        || !windows_path_equal(bootstrap_root, package_root)
        || !valid_generation(package_generation)
        || !fixed_ascii_equal(operational_generation, OPERATIONAL_GENERATION)
        || !runtime_role_matches(runtime_role)
    ) {
        PyErr_Clear();
        PyErr_SetString(PyExc_RuntimeError, "wss operational identity rejected");
        return NULL;
    }

    core_module = execute_payload_module("_local_compat_core", 0);
    if (core_module == NULL) {
        goto unavailable;
    }
    addon_module = execute_payload_module("_bidking_embedded_addon", 1);
    if (addon_module == NULL) {
        goto unavailable;
    }
    addons = PyObject_GetAttrString(addon_module, "addons");
    if (addons == NULL || !PyList_CheckExact(addons) || PyList_Size(addons) <= 0) {
        goto unavailable;
    }
    Py_DECREF(addon_module);
    Py_DECREF(core_module);
    return addons;

unavailable:
    Py_XDECREF(addons);
    Py_XDECREF(addon_module);
    Py_XDECREF(core_module);
    PyErr_Clear();
    PyErr_SetString(PyExc_RuntimeError, "wss operational addon unavailable");
    return NULL;
}

static PyMethodDef module_methods[] = {
    {"load_addons", load_addons, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_local_compat_addon_loader",
    NULL,
    -1,
    module_methods
};

PyMODINIT_FUNC PyInit__local_compat_addon_loader(void) {
    return PyModule_Create(&module_definition);
}
"""


def _c_array(name: str, value: bytes) -> str:
    encoded = ", ".join(f"0x{byte:02x}" for byte in value)
    return f"static const unsigned char {name}[] = {{{encoded}}};"


def _c_string(name: str, value: str) -> str:
    if not value or any(ord(character) > 0x7F for character in value):
        raise ValueError("native operational identity must be non-empty ASCII")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'static const char {name}[] = "{escaped}";'


def load_operational_contract(path: Path) -> WssOperationalContract:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        section = document["wss_operational_abi"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("WSS operational contract is unavailable") from exc
    if type(section) is not dict:
        raise ValueError("WSS operational contract is invalid")
    expected_fields = {
        "schema",
        "contract_version",
        "abi_version",
        "native_module",
        "entrypoint",
        "source_export",
        "runtime_roles",
        "source_members",
        "bootstrap_sha256",
        "generation_sha256",
    }
    if set(section) != expected_fields:
        raise ValueError("WSS operational contract fields are invalid")
    if (
        section.get("schema") != WSS_OPERATIONAL_SCHEMA
        or type(section.get("contract_version")) is not int
        or section["contract_version"] != 1
        or type(section.get("abi_version")) is not str
        or not section["abi_version"]
        or section.get("native_module") != "_local_compat_addon_loader"
        or section.get("entrypoint") != "load_addons"
        or section.get("source_export") is not False
    ):
        raise ValueError("WSS operational contract identity is invalid")
    runtime_roles_raw = section.get("runtime_roles")
    if (
        type(runtime_roles_raw) is not dict
        or set(runtime_roles_raw) != {"main", "mitmdump"}
        or runtime_roles_raw.get("main") != "cpython-313"
        or runtime_roles_raw.get("mitmdump") != "cpython-314"
    ):
        raise ValueError("WSS runtime role contract is invalid")
    source_members_raw = section.get("source_members")
    if type(source_members_raw) is not list or len(source_members_raw) != 2:
        raise ValueError("WSS protected source contract is invalid")
    source_members: dict[str, str] = {}
    for entry in source_members_raw:
        if type(entry) is not dict or set(entry) != {"path", "sha256"}:
            raise ValueError("WSS protected source entry is invalid")
        member_path = entry.get("path")
        digest = entry.get("sha256")
        if (
            type(member_path) is not str
            or member_path
            not in {
                "scripts/bidking_wss_capture_core.py",
                "scripts/mitm_bidking_wss_addon.py",
            }
            or type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or member_path in source_members
        ):
            raise ValueError("WSS protected source identity is invalid")
        source_members[member_path] = digest
    if set(source_members) != {
        "scripts/bidking_wss_capture_core.py",
        "scripts/mitm_bidking_wss_addon.py",
    }:
        raise ValueError("WSS protected source set is invalid")
    bootstrap_sha256 = section.get("bootstrap_sha256")
    generation_sha256 = section.get("generation_sha256")
    if any(
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in (bootstrap_sha256, generation_sha256)
    ):
        raise ValueError("WSS operational digest is invalid")
    canonical = dict(section)
    canonical.pop("generation_sha256")
    calculated_generation = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    if calculated_generation != generation_sha256:
        raise ValueError("WSS operational generation is inconsistent")
    if hashlib.sha256(_BOOTSTRAP.encode("utf-8")).hexdigest() != bootstrap_sha256:
        raise ValueError("WSS operational bootstrap identity is inconsistent")
    return WssOperationalContract(
        abi_version=section["abi_version"],
        generation_sha256=generation_sha256,
        native_module=section["native_module"],
        entrypoint=section["entrypoint"],
        runtime_roles=dict(runtime_roles_raw),
        source_members=source_members,
        bootstrap_sha256=bootstrap_sha256,
    )


def render_protected_addon(
    *,
    core_source: str,
    addon_source: str,
    content_key: bytes | None = None,
    key_share_a: bytes | None = None,
    nonces: tuple[bytes, bytes] | None = None,
    operational_contract: WssOperationalContract | None = None,
) -> tuple[str, str]:
    contract = operational_contract or load_operational_contract(
        Path(__file__).with_name("client_protection_manifest.json")
    )
    actual_sources = {
        "scripts/bidking_wss_capture_core.py": hashlib.sha256(
            core_source.encode("utf-8")
        ).hexdigest(),
        "scripts/mitm_bidking_wss_addon.py": hashlib.sha256(
            addon_source.encode("utf-8")
        ).hexdigest(),
    }
    if actual_sources != contract.source_members:
        raise ValueError("WSS protected source identity mismatch")
    key = bytes(content_key or AESGCM.generate_key(bit_length=256))
    if len(key) != 32:
        raise ValueError("content key must contain exactly 32 bytes")
    share_a = bytes(key_share_a or secrets.token_bytes(32))
    if len(share_a) != 32:
        raise ValueError("key share must contain exactly 32 bytes")
    share_b = bytes(left ^ right for left, right in zip(key, share_a))
    selected_nonces = nonces or (secrets.token_bytes(12), secrets.token_bytes(12))
    if any(len(nonce) != 12 for nonce in selected_nonces):
        raise ValueError("AES-GCM nonces must contain exactly 12 bytes")

    aesgcm = AESGCM(key)
    protected: list[tuple[bytes, bytes, bytes]] = []
    for source, nonce in zip((core_source, addon_source), selected_nonces):
        ciphertext_and_tag = aesgcm.encrypt(
            nonce, source.encode("utf-8"), associated_data=None
        )
        protected.append((ciphertext_and_tag[:-16], nonce, ciphertext_and_tag[-16:]))

    native = _NATIVE_TEMPLATE
    replacements = {
        "__KEY_SHARE_A__": _c_array("KEY_SHARE_A", share_a),
        "__KEY_SHARE_B__": _c_array("KEY_SHARE_B", share_b),
        "__PAYLOAD_0__": _c_array("PAYLOAD_0", protected[0][0]),
        "__NONCE_0__": _c_array("NONCE_0", protected[0][1]),
        "__TAG_0__": _c_array("TAG_0", protected[0][2]),
        "__PAYLOAD_1__": _c_array("PAYLOAD_1", protected[1][0]),
        "__NONCE_1__": _c_array("NONCE_1", protected[1][1]),
        "__TAG_1__": _c_array("TAG_1", protected[1][2]),
        "__OPERATIONAL_ABI__": _c_string(
            "OPERATIONAL_ABI", contract.abi_version
        ),
        "__OPERATIONAL_GENERATION__": _c_string(
            "OPERATIONAL_GENERATION", contract.generation_sha256
        ),
    }
    for marker, replacement in replacements.items():
        native = native.replace(marker, replacement)
    if any(marker in native for marker in replacements):
        raise RuntimeError("protected addon native marker remains")
    return _BOOTSTRAP, native


def write_addon_bootstrap(
    *,
    core_path: Path,
    addon_path: Path,
    output_path: Path,
    native_c_output_path: Path,
    contract_path: Path,
) -> None:
    contract = load_operational_contract(contract_path)
    bootstrap, native_source = render_protected_addon(
        core_source=core_path.read_text(encoding="utf-8"),
        addon_source=addon_path.read_text(encoding="utf-8"),
        operational_contract=contract,
    )
    output_path.write_text(bootstrap, encoding="utf-8", newline="\n")
    native_c_output_path.write_text(native_source, encoding="utf-8", newline="\n")


def smoke_mitmdump_addon(
    *,
    mitmdump_path: Path,
    addon_path: Path,
    confdir: Path,
    timeout: float = 3.0,
    contract_path: Path | None = None,
    package_root: Path | None = None,
    package_generation: str | None = None,
) -> tuple[bool, str]:
    """Prove mitmdump can import the staged addon with its embedded Python.

    Regular proxy mode deliberately avoids the administrator-only Windows local
    redirector.  A healthy process remains alive until this probe terminates it;
    an ABI/import/startup failure exits early and fails the build.  Child output
    is classified but never echoed because mitmproxy errors can contain private
    endpoint or filesystem context.
    """

    confdir.mkdir(parents=True, exist_ok=True)
    command = [
        str(mitmdump_path),
        "-q",
        "--mode",
        "regular",
        "--listen-host",
        "127.0.0.1",
        "--listen-port",
        "0",
        "--set",
        f"confdir={confdir}",
        "--set",
        "termlog_verbosity=error",
        "-s",
        str(addon_path),
    ]
    child_env = None
    if contract_path is not None:
        contract = load_operational_contract(contract_path)
        resolved_root = (package_root or addon_path.parent.parent).resolve()
        generation = package_generation
        if (
            type(generation) is not str
            or len(generation) != 64
            or any(character not in "0123456789abcdef" for character in generation)
        ):
            return False, "operational_identity_invalid"
        child_env = dict(os.environ)
        child_env.update(
            {
                "BIDKING_WSS_OPERATIONAL_ABI": contract.abi_version,
                "BIDKING_WSS_PACKAGE_ROOT": str(resolved_root),
                "BIDKING_WSS_PACKAGE_GENERATION": generation,
                "BIDKING_WSS_OPERATIONAL_GENERATION": (
                    contract.generation_sha256
                ),
                "BIDKING_WSS_RUNTIME_ROLE": "mitmdump",
            }
        )
    process = subprocess.Popen(
        command,
        cwd=str(addon_path.parent.parent),
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, stderr = process.communicate(timeout=max(0.5, float(timeout)))
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=3.0)
        return True, "ready"

    combined = f"{stdout}\n{stderr}"
    if "ModuleNotFoundError" in combined or "error in script" in combined:
        return False, "addon_import_failed"
    return False, f"helper_exited_early:{process.returncode}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render")
    render.add_argument("--core", type=Path, required=True)
    render.add_argument("--addon", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--native-c-output", type=Path, required=True)
    render.add_argument("--contract", type=Path, required=True)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--mitmdump", type=Path, required=True)
    smoke.add_argument("--addon", type=Path, required=True)
    smoke.add_argument("--confdir", type=Path, required=True)
    smoke.add_argument("--timeout", type=float, default=3.0)
    smoke.add_argument("--contract", type=Path)
    smoke.add_argument("--package-root", type=Path)
    smoke.add_argument("--package-generation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "render":
        write_addon_bootstrap(
            core_path=args.core,
            addon_path=args.addon,
            output_path=args.output,
            native_c_output_path=args.native_c_output,
            contract_path=args.contract,
        )
        return 0
    if args.command == "smoke":
        ok, reason = smoke_mitmdump_addon(
            mitmdump_path=args.mitmdump,
            addon_path=args.addon,
            confdir=args.confdir,
            timeout=args.timeout,
            contract_path=args.contract,
            package_root=args.package_root,
            package_generation=args.package_generation,
        )
        print(f"mitmproxy_addon_smoke={reason}")
        return 0 if ok else 1
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
