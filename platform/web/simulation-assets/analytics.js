/* Pure, deterministic projections of actual SimulationRunner events. No invented observations. */
(function(root) {
  'use strict';
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const probability = value => number(value) && value >= 0 && value <= 1;
  function analyze(events, report) {
    const mastery = new Map(), seen = new Set(), independent = [], hints = [];
    let answerCount = 0, assistedCount = 0, independentCount = 0, independentPassed = 0;
    for (const row of events) {
      const turn = number(row.turn) ? row.turn + 1 : null;
      if (typeof row.kc_id === 'string' && turn !== null) {
        if (!mastery.has(row.kc_id)) mastery.set(row.kc_id, []);
        mastery.get(row.kc_id).push({turn,
          estimated: probability(row.estimated_mastery_after) ? row.estimated_mastery_after : null,
          latent: probability(row.latent_mastery_after) ? row.latent_mastery_after : null});
      }
      const first = typeof row.item_id === 'string' && !seen.has(row.item_id);
      if (typeof row.item_id === 'string') seen.add(row.item_id);
      if (row.action !== 'ANSWER' || typeof row.passed !== 'boolean') continue;
      const assisted = Boolean(row.hint_level || row.answer_exposed);
      answerCount += 1; assistedCount += Number(assisted);
      if (turn !== null) hints.push({turn, value: assistedCount / answerCount, count: answerCount});
      if (first && !assisted) {
        independentCount += 1; independentPassed += Number(row.passed);
        if (turn !== null) independent.push({turn, value: independentPassed / independentCount, count: independentCount});
      }
    }
    const keys = [...new Set([...mastery.keys(), ...Object.keys(report?.per_kc || {})])].sort();
    const perKC = keys.map(kc => {
      const points = mastery.get(kc) || [];
      const last = [...points].reverse().find(p => p.estimated !== null);
      const latent = report?.per_kc?.[kc]?.mastery;
      return {kc, points: points.length, estimated: last?.estimated ?? null,
        latent: probability(latent) ? latent : null};
    });
    return {mastery, independent, hints, perKC, answerCount, assistedCount, independentCount, independentPassed};
  }
  const api = {analyze, probability};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LabAnalytics = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
