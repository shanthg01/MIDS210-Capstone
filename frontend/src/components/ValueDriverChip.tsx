import { Chip, Tooltip } from '@mui/material';
import { valueDriverLabel, valueDriverTooltip } from '../constants/definitions';

/** Labels mirror ProjectionCard / modeling SKILLS list. */
export const SKILL_LABELS: Record<string, string> = {
  shooting_3p: '3PT Shooting',
  shooting_2p_finishing: '2PT Finishing',
  free_throw_touch: 'Free Throw Touch',
  shot_creation_usage: 'Shot Creation',
  passing_creation: 'Passing / Creation',
  turnover_avoidance: 'Turnover Avoidance',
  offensive_rebounding: 'Off. Rebounding',
  defensive_rebounding: 'Def. Rebounding',
  steal_disruption: 'Steal Disruption',
  block_rim_protection: 'Rim Protection',
  foul_discipline: 'Foul Discipline',
};

export interface ValueDriver {
  feature: string;
  total_value_contribution: number;
}

interface Props {
  driver: ValueDriver;
  /** Positive drivers render green; negative drivers render red. */
  polarity: 'positive' | 'negative';
}

/**
 * Shared strength/weakness chip used on ProjectionCard and Dashboard tiles.
 * Tooltip guidance comes from definitions.valueDriverTooltip().
 */
export default function ValueDriverChip({ driver, polarity }: Props) {
  const label = valueDriverLabel(driver.feature);
  const tooltip = valueDriverTooltip(driver.feature);
  const value = driver.total_value_contribution;
  const signed =
    polarity === 'positive'
      ? `+${value.toFixed(2)}`
      : value.toFixed(2);

  return (
    <Tooltip title={tooltip}>
      <Chip
        size="small"
        color={polarity === 'positive' ? 'success' : 'error'}
        variant="outlined"
        label={`${label} ${signed}`}
      />
    </Tooltip>
  );
}
