const PLACE_BY_CODE = {
  "10": "大井",
  "11": "川崎",
  "12": "船橋",
  "13": "浦和",
};

const PATTERN_DIMS = ["逃げ", "番手", "内枠", "中枠", "外枠"];
const PATTERN_MARKS = ["◯", "△", "✕"];

function normalizeUrl(url) {
  if (!url) return "";
  let out = String(url).trim().replace(/\/+$/, "");
  for (const suffix of ["/rest/v1", "/rest", "/auth/v1"]) {
    if (out.endsWith(suffix)) out = out.slice(0, -suffix.length);
  }
  return out.replace(/\/+$/, "");
}

function getConfig() {
  const url = normalizeUrl(process.env.SUPABASE_URL);
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY ||
    process.env.SUPABASE_KEY ||
    process.env.SUPABASE_ANON_KEY ||
    "";

  return {
    url,
    key,
    projectRef: projectRefFromUrl(url),
    keyInfo: decodeSupabaseKey(key),
    pin: process.env.NANKAN_SITE_PIN || "",
    configured: Boolean(url && key),
    usingServiceRole: Boolean(
      process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY,
    ),
  };
}

function projectRefFromUrl(url) {
  try {
    const host = new URL(url).hostname;
    return host.endsWith(".supabase.co") ? host.split(".")[0] : host;
  } catch {
    return "";
  }
}

function decodeSupabaseKey(key) {
  if (!key || !key.includes(".")) return { role: "", ref: "" };
  try {
    const payload = key.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = payload.padEnd(payload.length + ((4 - (payload.length % 4)) % 4), "=");
    const data = JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
    return {
      role: data.role || "",
      ref: data.ref || data.project_ref || "",
    };
  } catch {
    return { role: "", ref: "" };
  }
}

function jsonHeaders(extra = {}) {
  return {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "s-maxage=8, stale-while-revalidate=60",
    ...extra,
  };
}

function sendJson(res, status, payload, extraHeaders = {}) {
  res.statusCode = status;
  for (const [key, value] of Object.entries(jsonHeaders(extraHeaders))) {
    res.setHeader(key, value);
  }
  res.end(JSON.stringify(payload));
}

function sendError(res, status, message, detail) {
  sendJson(res, status, {
    ok: false,
    error: message,
    detail: detail ? String(detail).slice(0, 900) : undefined,
  });
}

function handleOptions(req, res) {
  if (req.method !== "OPTIONS") return false;
  res.statusCode = 204;
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type,Authorization,x-nankan-pin");
  res.end();
  return true;
}

async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text.trim()) return {};
  return JSON.parse(text);
}

function requireSupabase(res) {
  const cfg = getConfig();
  if (!cfg.configured) {
    sendError(
      res,
      503,
      "Supabaseの環境変数が未設定です",
      "SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY を Vercel の Environment Variables に入れてください。",
    );
    return null;
  }
  return cfg;
}

function authOk(req) {
  const cfg = getConfig();
  if (!cfg.pin) return true;
  const headerPin = req.headers["x-nankan-pin"];
  const auth = req.headers.authorization || "";
  const bearer = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7) : "";
  return headerPin === cfg.pin || bearer === cfg.pin;
}

async function supabaseFetch(path, options = {}) {
  const cfg = getConfig();
  if (!cfg.configured) {
    throw new Error("Supabase is not configured");
  }

  const url = new URL(`${cfg.url}/rest/v1/${path.replace(/^\/+/, "")}`);
  const query = options.query || {};
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.append(key, value);
    }
  }

  const headers = {
    apikey: cfg.key,
    Authorization: `Bearer ${cfg.key}`,
    Accept: "application/json",
    ...options.headers,
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(url, {
    method: options.method || "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!response.ok) {
    const message =
      data && typeof data === "object"
        ? data.message || data.details || JSON.stringify(data)
        : text;
    const err = new Error(message || `Supabase error ${response.status}`);
    err.status = response.status;
    err.data = data;
    throw err;
  }
  return data;
}

function postgrestIn(values) {
  const cleaned = [...new Set((values || []).filter(Boolean).map(String))];
  if (!cleaned.length) return "";
  const quoted = cleaned.map((value) => `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`);
  return `in.(${quoted.join(",")})`;
}

function parseJsonField(value, fallback = {}) {
  if (!value) return fallback;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(value) || fallback;
  } catch {
    return fallback;
  }
}

function formatDateKeyJst(date) {
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}${byType.month}${byType.day}`;
}

function dateDaysAgoJst(days) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - Number(days || 0));
  return formatDateKeyJst(date);
}

function compactRace(row) {
  if (!row) return null;
  return {
    race_key: row.race_key,
    date: row.date,
    place_code: row.place_code,
    place_name: row.place_name || PLACE_BY_CODE[row.place_code] || "",
    race_num: row.race_num,
    race_id: row.race_id,
    course: row.course,
    dist: row.dist,
    post_time: row.post_time || "",
    race_name: row.race_name,
    generated_at: row.generated_at,
    has_result: Number(row.has_result || 0) === 1,
    grades: parseJsonField(row.grades_json, {}),
    uma_ids: parseJsonField(row.uma_ids_json, {}),
    eval_list_text: row.eval_list_text || "",
  };
}

function sortHorses(horses) {
  return [...horses].sort((a, b) => {
    const ar = a.finish_rank || 999;
    const br = b.finish_rank || 999;
    if (ar !== br) return ar - br;
    const au = a.umaban || 999;
    const bu = b.umaban || 999;
    if (au !== bu) return au - bu;
    return String(a.name || "").localeCompare(String(b.name || ""), "ja");
  });
}

function buildHorses(race, results = []) {
  const grades = race?.grades || {};
  const umaIds = race?.uma_ids || {};
  const horses = [];

  if (results.length) {
    for (const row of results) {
      const name = row.horse_name || "";
      horses.push({
        umaban: row.umaban,
        name,
        uma_id: row.uma_id || umaIds[name] || "",
        finish_rank: row.finish_rank,
        popularity: row.popularity,
        time_diff: row.time_diff,
        tier: grades[name] || "",
      });
    }
    return sortHorses(horses);
  }

  for (const [name, umaId] of Object.entries(umaIds)) {
    horses.push({
      umaban: null,
      name,
      uma_id: umaId || "",
      finish_rank: null,
      popularity: null,
      time_diff: null,
      tier: grades[name] || "",
    });
  }
  return sortHorses(horses);
}

function cleanPattern(pattern) {
  const out = {};
  const source = pattern && typeof pattern === "object" ? pattern : {};
  for (const dim of PATTERN_DIMS) {
    const mark = source[dim];
    if (PATTERN_MARKS.includes(mark)) out[dim] = mark;
  }
  return out;
}

module.exports = {
  PLACE_BY_CODE,
  PATTERN_DIMS,
  PATTERN_MARKS,
  authOk,
  buildHorses,
  cleanPattern,
  compactRace,
  dateDaysAgoJst,
  getConfig,
  handleOptions,
  parseJsonField,
  postgrestIn,
  readBody,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
};
