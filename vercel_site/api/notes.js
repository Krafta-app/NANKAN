const {
  authOk,
  cleanPattern,
  handleOptions,
  parseJsonField,
  readBody,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
} = require("./_supabase");

// pattern_json 列が未追加の Supabase でも落ちないよう、列ありselectを先に試し、
// 「column ... does not exist」なら列なしselectでリトライする（Python側と同じ挙動）。
function isMissingPatternColumn(err) {
  return /pattern_json/.test(String(err?.message || "")) &&
    /(does not exist|schema cache|42703|PGRST204)/i.test(String(err?.message || "") + String(err?.data ? JSON.stringify(err.data) : ""));
}

async function findExisting(umaId) {
  for (const select of [
    "uma_id,horse_name,note_text,pattern_json,updated_at",
    "uma_id,horse_name,note_text,updated_at",
  ]) {
    try {
      const rows = await supabaseFetch("horse_notes", {
        query: { select, uma_id: `eq.${umaId}`, limit: "1" },
      });
      return rows?.[0] || null;
    } catch (err) {
      if (isMissingPatternColumn(err)) continue;
      throw err;
    }
  }
  return null;
}

async function listNotes(queryText) {
  const base = { order: "updated_at.desc", limit: "200" };
  if (queryText) base.horse_name = `ilike.*${String(queryText).replace(/\*/g, "")}*`;
  for (const select of [
    "uma_id,horse_name,note_text,pattern_json,updated_at",
    "uma_id,horse_name,note_text,updated_at",
  ]) {
    try {
      const rows = await supabaseFetch("horse_notes", { query: { ...base, select } });
      return (rows || []).map((row) => ({
        ...row,
        pattern: parseJsonField(row.pattern_json, {}),
      }));
    } catch (err) {
      if (isMissingPatternColumn(err)) continue;
      throw err;
    }
  }
  return [];
}

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (!requireSupabase(res)) return;

  try {
    if (req.method === "GET") {
      const notes = await listNotes(req.query.query || "");
      return sendJson(res, 200, { ok: true, notes });
    }

    if (req.method !== "POST") return sendError(res, 405, "GET/POSTだけ対応しています");
    if (!authOk(req)) return sendError(res, 401, "PINが違います");

    const body = await readBody(req);
    const umaId = String(body.uma_id || "").trim();
    const horseName = String(body.horse_name || "").trim();
    if (!umaId) return sendError(res, 400, "uma_id が必要です");

    const existing = await findExisting(umaId);
    const hasNote = Object.prototype.hasOwnProperty.call(body, "note_text");
    const hasPattern = Object.prototype.hasOwnProperty.call(body, "pattern");
    const nextNote = hasNote ? String(body.note_text || "").trim() : existing?.note_text || "";
    const nextPattern = hasPattern
      ? cleanPattern(body.pattern)
      : parseJsonField(existing?.pattern_json, {});
    const hasAnyPattern = Object.keys(nextPattern).length > 0;

    if (!nextNote && !hasAnyPattern) {
      await supabaseFetch("horse_notes", {
        method: "DELETE",
        query: { uma_id: `eq.${umaId}` },
      });
      return sendJson(res, 200, { ok: true, deleted: true });
    }

    const now = new Date().toISOString().slice(0, 19);
    const payload = {
      uma_id: umaId,
      horse_name: horseName || existing?.horse_name || "",
      note_text: nextNote,
      pattern_json: hasAnyPattern ? JSON.stringify(nextPattern) : null,
      updated_at: now,
    };

    let saved;
    try {
      saved = await supabaseFetch("horse_notes", {
        method: "POST",
        query: { on_conflict: "uma_id" },
        headers: { Prefer: "resolution=merge-duplicates,return=representation" },
        body: payload,
      });
    } catch (err) {
      // pattern_json 列が未追加の場合はテキストメモだけでも保存する（好走パターンはスキップ）。
      if (!isMissingPatternColumn(err)) throw err;
      const { pattern_json, ...rest } = payload;
      saved = await supabaseFetch("horse_notes", {
        method: "POST",
        query: { on_conflict: "uma_id" },
        headers: { Prefer: "resolution=merge-duplicates,return=representation" },
        body: rest,
      });
    }

    sendJson(res, 200, { ok: true, note: saved?.[0] || payload, pattern: nextPattern });
  } catch (err) {
    sendError(res, err.status || 500, "メモ保存に失敗しました", err.message);
  }
};
