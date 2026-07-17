import {
  Box,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Divider,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {
  FIT_COMPONENTS,
  OVERALL_FIT,
  SKILLS,
  BOX_SCORE,
  GAP_FEATURES,
  SUB_METRICS,
  VALUE_PER_100,
  CONFIDENCE_INTERVAL,
  DATA_STATUS,
  type Definition,
} from '../constants/definitions';

function DefinitionRow({ def }: { def: Definition }) {
  return (
    <Box sx={{ py: 1 }}>
      <Typography variant="body2" fontWeight={700}>
        {def.label}
      </Typography>
      <Typography variant="body2" color="text.secondary">
        {def.short}
      </Typography>
    </Box>
  );
}

function Section({
  title,
  defs,
  defaultExpanded = false,
}: {
  title: string;
  defs: Definition[];
  defaultExpanded?: boolean;
}) {
  return (
    <Accordion defaultExpanded={defaultExpanded} variant="outlined" disableGutters sx={{ mb: 1.5 }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="subtitle1" fontWeight={700}>
          {title}
        </Typography>
      </AccordionSummary>
      <AccordionDetails>
        {defs.map((def, i) => (
          <Box key={def.label}>
            {i > 0 && <Divider />}
            <DefinitionRow def={def} />
          </Box>
        ))}
      </AccordionDetails>
    </Accordion>
  );
}

export default function GlossaryPage() {
  return (
    <Box maxWidth={720}>
      <Typography variant="h4" fontWeight={800} gutterBottom>
        Glossary
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        What every score and metric in PortalPoint means.
      </Typography>

      <Section
        title="Fit Components & Overall Fit"
        defaultExpanded
        defs={[OVERALL_FIT, ...Object.values(FIT_COMPONENTS), ...Object.values(SUB_METRICS)]}
      />
      <Section
        title="Projection & Value"
        defs={[VALUE_PER_100, CONFIDENCE_INTERVAL, ...Object.values(BOX_SCORE)]}
      />
      <Section title="Skill Percentiles" defs={Object.values(SKILLS)} />
      <Section title="Stat Gap Features" defs={Object.values(GAP_FEATURES)} />
      <Section title="Data Status" defs={[DATA_STATUS.live, DATA_STATUS.placeholder]} />
    </Box>
  );
}
