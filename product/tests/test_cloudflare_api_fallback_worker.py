from pathlib import Path
import shutil
import subprocess

import pytest


WORKER = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "cloudflare_api_fallback_worker.js"
)


def test_worker_uses_fixed_https_origin_and_strict_api_allowlist():
    text = WORKER.read_text(encoding="utf-8")
    assert 'const ORIGIN = "https://api.bidking.example.invalid";' in text
    for path in (
        "/healthz", "/activation-readiness", "/version", "/public", "/activate", "/telemetry"
    ):
        assert f'["{path}"' in text
    assert "incoming.search" in text
    assert "request.url" in text
    assert "fetch(`${ORIGIN}${incoming.pathname}`" in text
    assert "console.log" not in text
    assert "caches.default" not in text


def test_worker_has_no_store_body_limit_and_method_rejection():
    text = WORKER.read_text(encoding="utf-8")
    assert "MAX_POST_BYTES = 16 * 1024" in text
    assert "payload_too_large" in text
    assert '"Cache-Control": "no-store"' in text
    assert "status: 405" in text


def test_worker_routes_and_rejections_execute_in_node(tmp_path):
    """Exercise the exported Worker against a mocked fixed origin, not only source strings."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")

    module = tmp_path / "worker.mjs"
    module.write_text(WORKER.read_text(encoding="utf-8"), encoding="utf-8")
    runner = tmp_path / "runner.mjs"
    runner.write_text(
        """
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const worker = (await import(pathToFileURL(process.argv[2]).href)).default;
const calls = [];
globalThis.fetch = async (url, init) => {
  const body = init.body ? Buffer.from(init.body).toString("utf8") : "";
  calls.push({ url: String(url), method: init.method, body });
  return new Response("upstream-ok", {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

let response = await worker.fetch(new Request("https://fallback.example/unknown"));
assert.equal(response.status, 404);
assert.equal(response.headers.get("Cache-Control"), "no-store");

response = await worker.fetch(new Request("https://fallback.example/version?target=evil"));
assert.equal(response.status, 404);

response = await worker.fetch(new Request("https://fallback.example/public?x=1"));
assert.equal(response.status, 404);

response = await worker.fetch(new Request("https://fallback.example/activate"));
assert.equal(response.status, 405);
assert.equal(response.headers.get("Allow"), "POST");

response = await worker.fetch(new Request("https://fallback.example/public", {
  method: "POST",
}));
assert.equal(response.status, 405);
assert.equal(response.headers.get("Allow"), "GET, HEAD");

response = await worker.fetch(new Request("https://fallback.example/version"));
assert.equal(response.status, 200);
assert.equal(await response.text(), "upstream-ok");
assert.deepEqual(calls.at(-1), {
  url: "https://api.bidking.example.invalid/version",
  method: "GET",
  body: "",
});

response = await worker.fetch(new Request("https://fallback.example/public"));
assert.equal(response.status, 200);
assert.equal(await response.text(), "upstream-ok");
assert.deepEqual(calls.at(-1), {
  url: "https://api.bidking.example.invalid/public",
  method: "GET",
  body: "",
});

response = await worker.fetch(new Request("https://fallback.example/public", {
  method: "HEAD",
}));
assert.equal(response.status, 200);
assert.equal(await response.text(), "");
assert.equal(response.headers.get("Cache-Control"), "no-store");
assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
assert.deepEqual(calls.at(-1), {
  url: "https://api.bidking.example.invalid/public",
  method: "GET",
  body: "",
});

response = await worker.fetch(new Request("https://fallback.example/healthz", {
  method: "HEAD",
}));
assert.equal(response.status, 200);
assert.equal(await response.text(), "");
assert.equal(calls.at(-1).url, "https://api.bidking.example.invalid/healthz");
assert.equal(calls.at(-1).method, "GET");

response = await worker.fetch(new Request("https://fallback.example/activation-readiness"));
assert.equal(response.status, 200);
assert.equal(calls.at(-1).url, "https://api.bidking.example.invalid/activation-readiness");
assert.equal(calls.at(-1).method, "GET");

const activationBody = JSON.stringify({ device: "D", client_security_epoch: 1 });
response = await worker.fetch(new Request("https://fallback.example/activate", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: activationBody,
}));
assert.equal(response.status, 200);
assert.equal(calls.at(-1).url, "https://api.bidking.example.invalid/activate");
assert.equal(calls.at(-1).method, "POST");
assert.equal(calls.at(-1).body, activationBody);

response = await worker.fetch(new Request("https://fallback.example/telemetry", {
  method: "POST",
  body: "x".repeat(16 * 1024 + 1),
}));
assert.equal(response.status, 413);
assert.equal(calls.length, 6);
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node, str(runner), str(module)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
