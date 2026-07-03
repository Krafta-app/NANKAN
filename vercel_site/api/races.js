const {
  compactRace,
  dateDaysAgoJst,
  handleOptions,
  requireSupabase,
  sendError,
  sendJson,
  supabaseFetch,
} = require("./_supabase");

const RACE_RETENTION_DAYS = 7;

module.exports = async function handler(req, res) {
  if (handleOptions(req, res)) return;
  if (req.method !== "GET") return sendError(res, 405, "GETだけ対応しています");
  if (!requireSupabase(res)) return;

  try {
    const cutoffDate = dateDaysAgoJst(RACE_RETENTION_DAYS);
    const requestedDate = String(req.query.date || "");
    const [dateRows, placeRows] = await Promise.all([
      supabaseFetch("races", {
        query: {
          select: "date",
          date: `gte.${cutoffDate}`,
          order: "date.desc",
          limit: "500",
        },
      }).catch(() => []),
      supabaseFetch("races", {
        query: {
          select: "place_code,place_name",
          date: `gte.${cutoffDate}`,
          order: "place_code.asc",
          limit: "500",
        },
      }).catch(() => []),
    ]);
    const dates = [...new Set((dateRows || []).map((race) => race.date).filter(Boolean))];
    const selectedDate = requestedDate || dates[0] || "";

    const query = {
      select:
        "race_key,date,place_code,place_name,race_num,race_id,course,dist,post_time,race_name,grades_json,uma_ids_json,eval_list_text,generated_at,has_result",
      order: "date.desc,place_code.asc,race_num.asc",
      limit: "500",
      date: selectedDate ? `eq.${selectedDate}` : `gte.${cutoffDate}`,
    };
    if (requestedDate && requestedDate < cutoffDate) query.date = "eq.__expired__";
    if (req.query.place_code) query.place_code = `eq.${req.query.place_code}`;
    if (req.query.only_with_result === "1") query.has_result = "eq.1";

    const rows = await supabaseFetch("races", { query });
    const races = (rows || []).map(compactRace);
    const places = [
      ...new Map(
        (placeRows || [])
          .filter((race) => race.place_code)
          .map((race) => [race.place_code, race.place_name]),
      ).entries(),
    ].map(([place_code, place_name]) => ({ place_code, place_name }));

    sendJson(res, 200, {
      ok: true,
      supabaseProjectRef: require("./_supabase").getConfig().projectRef,
      races,
      dates,
      places,
      latestDate: dates[0] || null,
      selectedDate: selectedDate || null,
    });
  } catch (err) {
    sendError(res, err.status || 500, "レース一覧の読み込みに失敗しました", err.message);
  }
};
