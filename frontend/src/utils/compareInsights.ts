import type { CompareResponse } from '../types/api';

export interface Verdict {
  headline: string;
  bullets: string[];
}

// A flat margin below this is treated as "too close to call" rather than
// declaring a winner — avoids overstating confidence on a near-tied comparison.
const CLOSE_MARGIN = 3;

// Derived entirely from an already-fetched CompareResponse — no new backend call.
export function buildVerdict(result: CompareResponse): Verdict {
  const row = result.comparison_matrix.overall_fit;
  const sorted = Object.entries(row).sort((a, b) => b[1] - a[1]);
  const [topName, topVal] = sorted[0];
  const second = sorted[1];
  const margin = second ? topVal - second[1] : Infinity;

  const headline =
    second && margin < CLOSE_MARGIN
      ? `${topName} and ${second[0]} are roughly even overall (${topVal.toFixed(0)} vs ${second[1].toFixed(0)}) - see the breakdown below.`
      : `${topName} is the stronger fit overall (${topVal.toFixed(0)}/100).`;

  const bullets = result.trade_offs
    .filter((t) => t.best_player_name === topName)
    .slice(0, 2)
    .map((t) => `${t.factor}: ${t.description}`);

  return { headline, bullets };
}
