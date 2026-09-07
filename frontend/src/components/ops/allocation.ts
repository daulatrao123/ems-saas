// Proportional unit->days allocation with largest-remainder rounding so the slots sum to the cycle length.
export function unitsToDays(units: Record<string, number>, cycleDays: number): Record<string, number> {
  const slots = Object.keys(units); const total = slots.reduce((a, s) => a + (units[s] || 0), 0);
  if (total <= 0 || cycleDays <= 0) return {};
  const exact: Record<string, number> = {}; const out: Record<string, number> = {}; let sum = 0;
  for (const s of slots) { exact[s] = ((units[s] || 0) / total) * cycleDays; out[s] = Math.round(exact[s]); sum += out[s]; }
  let diff = cycleDays - sum;
  const order = slots.slice().sort((a, b) => (diff > 0 ? (exact[b] % 1) - (exact[a] % 1) : (exact[a] % 1) - (exact[b] % 1)));
  for (let i = 0; diff !== 0 && i < order.length; i++) { out[order[i]] += diff > 0 ? 1 : -1; diff += diff > 0 ? -1 : 1; }
  for (const s of slots) if ((units[s] || 0) > 0 && out[s] < 1) out[s] = 1;
  return out;
}
