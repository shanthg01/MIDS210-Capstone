import type { FitScoreResponse, PlayerProjectionResponse } from '../types/api';
import { FIT_COMPONENTS, GAP_FEATURES, SKILLS } from '../constants/definitions';

export interface Insight {
  headline: string;
  bullets: string[];
}

// Derived entirely from an already-fetched FitScoreResponse — no new fetch.
export function buildFitInsight(fit: FitScoreResponse): Insight {
  const components = [
    { key: 'gap_match' as const, value: fit.gap_match },
    { key: 'scheme_fit' as const, value: fit.scheme_fit },
    { key: 'role_fit' as const, value: fit.role_fit },
    { key: 'program_fit' as const, value: fit.program_fit },
  ];
  const strongest = components.reduce((a, b) => (b.value > a.value ? b : a));
  const weakest = components.reduce((a, b) => (b.value < a.value ? b : a));

  const headline =
    `Overall fit ${Math.round(fit.overall_fit)}/100 — strongest in ${FIT_COMPONENTS[strongest.key].label} ` +
    `(${Math.round(strongest.value)}), weakest in ${FIT_COMPONENTS[weakest.key].label} (${Math.round(weakest.value)}).`;

  const bullets: string[] = [];
  const topGap = fit.breakdown.gap.top_gap_features[0];
  if (topGap) {
    const label = GAP_FEATURES[topGap.feature]?.label ?? topGap.feature;
    bullets.push(`Fills a roster need in ${label} (gap score ${topGap.gap.toFixed(2)}).`);
  }
  if (fit.breakdown.role_fit.starter_probability >= 0.5) {
    bullets.push(
      `Projects as a likely starter (${Math.round(fit.breakdown.role_fit.starter_probability * 100)}% probability).`,
    );
  }

  return { headline, bullets };
}

interface ValueDriver {
  feature: string;
  component: string;
  total_value_contribution: number;
}

function skillLabel(feature: string): string {
  const key = feature.replace('skill_', '');
  return SKILLS[key]?.label ?? feature;
}

// Scoped to context-neutral projection data only — PlayerProfilePage never
// fetches the 4 fit components, so this can't speak to Gap/Scheme/Role/Program.
export function buildProjectionInsight(projection: PlayerProjectionResponse): Insight {
  const ciText =
    projection.value_ci_lower !== null && projection.value_ci_upper !== null
      ? ` (90% CI ${projection.value_ci_lower.toFixed(1)} to ${projection.value_ci_upper.toFixed(1)})`
      : '';
  const headline =
    `Projects at ${projection.value_per_100 >= 0 ? '+' : ''}${projection.value_per_100.toFixed(1)} value ` +
    `per 100 possessions${ciText}, independent of any specific program.`;

  const bullets: string[] = [];
  const valueDrivers = projection.explanation?.value_drivers as
    | { top_positive?: ValueDriver[]; top_negative?: ValueDriver[] }
    | undefined;
  const topPos = valueDrivers?.top_positive?.[0];
  const topNeg = valueDrivers?.top_negative?.[0];
  if (topPos) {
    bullets.push(`Biggest strength: ${skillLabel(topPos.feature)} (+${topPos.total_value_contribution.toFixed(2)}).`);
  }
  if (topNeg) {
    bullets.push(`Biggest weakness: ${skillLabel(topNeg.feature)} (${topNeg.total_value_contribution.toFixed(2)}).`);
  }

  return { headline, bullets };
}
