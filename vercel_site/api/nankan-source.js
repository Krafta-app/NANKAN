const crypto = require("node:crypto");
const { readBody } = require("./_supabase");

// 公開レース情報の読み取り専用。取得先を固定し、署名済みの呼び出しだけ受け付ける。
const PATH_PATTERN = /^\/(?:calendar\/\d{6}|program\/\d{14}|(?:syousai|uma_shosai|result|taisen)\/\d{16}|uma_info\/\d{6,20})\.do(?:\?type=[a-zA-Z0-9_]+)?$/;
const SIGN_CONTEXT = "nankan-fetch-v1";

function authOk(path, timestamp, signature, key, now = Date.now()) {
  if (!key || !/^\d{10}$/.test(timestamp || "") || !/^[a-f0-9]{64}$/.test(signature || "")) return false;
  if (Math.abs(now / 1000 - Number(timestamp)) > 120) return false;
  const expected = crypto.createHmac("sha256", key).update(`${SIGN_CONTEXT}\n${timestamp}\n${path}`).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(signature, "hex"));
}

async function handler(req, res) {
  const send = (status, body) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST") return send(405, { error: "POST required" });
  try {
    const { path } = await readBody(req);
    if (typeof path !== "string" || !PATH_PATTERN.test(path)) return send(400, { error: "Invalid race information path" });
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY;
    if (!authOk(path, req.headers["x-nankan-time"], req.headers["x-nankan-signature"], key)) {
      return send(401, { error: "Invalid request signature" });
    }
    const upstream = await fetch("https://www.nankankeiba.com" + path, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
      },
      redirect: "error",
      signal: AbortSignal.timeout(10000),
    });
    const body = Buffer.from(await upstream.arrayBuffer());
    if (body.length > 3000000) return send(502, { error: "Upstream response too large" });
    return send(200, {
      status: upstream.status,
      body: body.toString("base64"),
      contentType: upstream.headers.get("content-type") || "text/html",
      region: process.env.VERCEL_REGION || "unknown",
    });
  } catch {
    return send(502, { error: "Could not fetch official race information" });
  }
}

module.exports = handler;
module.exports.authOk = authOk;
module.exports.PATH_PATTERN = PATH_PATTERN;
