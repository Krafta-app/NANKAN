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

async function findExisting(umaId) {
  const rows = await supabaseFetch("horse_notes", {
    query: {
      select: "uma_id,horse_name,note_text,pattern_json,updated_at",
      uma_id: `eq.${umaId}`,
      limit: "1",
    },
  });
  return rows?.[0] || null;
}

async function listNotes(queryText) {
  const query = {
    select: "uma_id,horse_name,note_text,pattern_json,updated_at",
    order: "updated_at.desc",
    limit: "200",
  };
  if (queryText) query.horse_name = `ilike.*${String(queryText).replace(/\*/g, "")}*`;
  const rows = await supabaseFetch("horse_notes", { query });
  return (rows || []).map((row) => ({
    ...row,
    pattern: parseJsonField(row.pattern_json, {}),
  }));
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

    const saved = await supabaseFetch("horse_notes", {
      method: "POST",
      query: { on_conflict: "uma_id" },
      headers: { Prefer: "resolution=merge-duplicates,return=representation" },
      body: payload,
    });

    sendJson(res, 200, { ok: true, note: saved?.[0] || payload, pattern: nextPattern });
  } catch (err) {
    sendError(res, err.status || 500, "メモ保存に失敗しました", err.message);
  }
};
