const ORIGIN = "https://api.bidking.example.invalid";
const MAX_POST_BYTES = 16 * 1024;

const ALLOWED = new Map([
  ["/healthz", new Set(["GET", "HEAD"])],
  ["/activation-readiness", new Set(["GET", "HEAD"])],
  ["/version", new Set(["GET", "HEAD"])],
  ["/public", new Set(["GET", "HEAD"])],
  ["/activate", new Set(["POST"])],
  ["/telemetry", new Set(["POST"])],
]);

function jsonError(status, error) {
  return new Response(JSON.stringify({ ok: false, error }), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const methods = ALLOWED.get(incoming.pathname);
    if (!methods || incoming.search) {
      return jsonError(404, "not_found");
    }
    if (!methods.has(request.method)) {
      return new Response(null, {
        status: 405,
        headers: {
          Allow: [...methods].join(", "),
          "Cache-Control": "no-store",
        },
      });
    }

    let body;
    const headers = new Headers();
    if (request.method === "POST") {
      const declared = Number(request.headers.get("Content-Length") || "0");
      if (Number.isFinite(declared) && declared > MAX_POST_BYTES) {
        return jsonError(413, "payload_too_large");
      }
      body = await request.arrayBuffer();
      if (body.byteLength > MAX_POST_BYTES) {
        return jsonError(413, "payload_too_large");
      }
      headers.set("Content-Type", "application/json; charset=utf-8");
    }

    const upstreamMethod = request.method === "HEAD" ? "GET" : request.method;
    const upstream = await fetch(`${ORIGIN}${incoming.pathname}`, {
      method: upstreamMethod,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    const contentType = upstream.headers.get("Content-Type");
    if (contentType) responseHeaders.set("Content-Type", contentType);
    responseHeaders.set("Cache-Control", "no-store");
    responseHeaders.set("X-Content-Type-Options", "nosniff");

    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  },
};
