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
const NEXT_DAY_SWITCH_MINUTES = 22 * 60;

function jstParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

function jstDateKey(offsetDays = 0) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + Number(offsetDays || 0));
  const parts = jstParts(date);
  return `${parts.year}${parts.month}${parts.day}`;
}

function jstMinutesNow() {
  const parts = jstParts();
  return Number(parts.hour || 0) * 60 + Number(parts.minute || 0);
}

function pickDefaultDate(dates) {
  const available = new Set(dates || []);
  const today = jstDateKey(0);
  const tomorrow = jstDateKey(1);
  if (jstMinutesNow() >= NEXT_DAY_SWITCH_MINUTES && available.has(tomorrow)) return tomorrow;
  if (available.has(today)) return today;
  return dates?.[0] || "";
}

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
    const defaultDate = pickDefaultDate(dates);
    const selectedDate = requestedDate || defaultDate;

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
      defaultDate: defaultDate || null,
      selectedDate: selectedDate || null,
    });
  } catch (err) {
    sendError(res, err.status || 500, "レース一覧の読み込みに失敗しました", err.message);
  }
};
