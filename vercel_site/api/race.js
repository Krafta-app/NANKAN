const {
  buildHorses,
  compactRace,
  handleOptions,
  parseJsonField,
  postgrestIn,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
} = require("./_supabase");

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "GET") return sendError(res, 405, "GETだけ対応しています");
  if (!requireSupabase(res)) return;

  const raceKey = String(req.query.race_key || "");
  if (!raceKey) return sendError(res, 400, "race_key が必要です");
  const includePage = req.query.include_page !== "0";

  try {
    const raceRows = await supabaseFetch("races", {
      query: {
        select: "*",
        race_key: `eq.${raceKey}`,
        limit: "1",
      },
    });
    if (!raceRows || !raceRows.length) return sendError(res, 404, "レースが見つかりません");

    const race = compactRace(raceRows[0]);
    const [pageRows, resultRows] = await Promise.all([
      includePage
        ? supabaseFetch("race_pages", {
            query: {
              select: "data_html,data_text",
              race_key: `eq.${raceKey}`,
              limit: "1",
            },
          }).catch(() => [])
        : Promise.resolve([]),
      supabaseFetch("race_results", {
        query: {
          select: "*",
          race_key: `eq.${raceKey}`,
          order: "finish_rank.asc",
        },
      }).catch(() => []),
    ]);

    const results = resultRows || [];
    const horses = buildHorses(race, results);
    const umaIds = horses.map((horse) => horse.uma_id).filter(Boolean);
    let noteRows = [];
    if (umaIds.length) {
      noteRows = await supabaseFetch("horse_notes", {
        query: {
          select: "uma_id,horse_name,note_text,pattern_json,updated_at",
          uma_id: postgrestIn(umaIds),
        },
      }).catch(() => []);
    }

    const notes = {};
    const patterns = {};
    for (const row of noteRows || []) {
      notes[row.uma_id] = {
        uma_id: row.uma_id,
        horse_name: row.horse_name,
        note_text: row.note_text || "",
        updated_at: row.updated_at || "",
      };
      patterns[row.uma_id] = parseJsonField(row.pattern_json, {});
    }

    sendJson(res, 200, {
      ok: true,
      race,
      page: {
        data_html: pageRows?.[0]?.data_html || "",
        data_text: pageRows?.[0]?.data_text || "",
      },
      pageLoaded: includePage,
      horses,
      results,
      notes,
      patterns,
    });
  } catch (err) {
    sendError(res, err.status || 500, "レース詳細の読み込みに失敗しました", err.message);
  }
};
