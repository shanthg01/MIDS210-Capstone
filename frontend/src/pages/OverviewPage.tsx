import { Box, Typography, Paper, Button, Divider } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import TrackChangesIcon from '@mui/icons-material/TrackChanges';
import InsightsIcon from '@mui/icons-material/Insights';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import MenuBookIcon from '@mui/icons-material/MenuBook';

const TILES = [
  {
    icon: <TrackChangesIcon color="primary" />,
    title: 'Rank the full transfer pool',
    body: "See every eligible transfer scored against your program's system and roster needs, not just raw stats.",
  },
  {
    icon: <InsightsIcon color="secondary" />,
    title: 'Understand the "why"',
    body: 'Every score breaks down into plain-language components - no black-box number to take on faith.',
  },
  {
    icon: <CompareArrowsIcon color="success" />,
    title: 'Compare candidates side-by-side',
    body: 'Shortlist a few players and see exactly where each one is stronger, and why.',
  },
];

export default function OverviewPage() {
  const navigate = useNavigate();

  return (
    <Box sx={{ width: '100%', maxWidth: { xs: '100%', xl: 1400 } }}>
      <Typography variant="h4" sx={{ fontWeight: 800 }} gutterBottom>
        Welcome to PortalPoint
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4, maxWidth: 640 }}>
        PortalPoint helps your program evaluate transfer-portal players quickly - combining how a
        player's stats fill a gap on your roster, how their style fits your system, and off-court
        factors like NIL and academics into one transparent fit score.
      </Typography>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' },
          gap: 2,
          mb: 4,
        }}
      >
        {TILES.map((tile) => (
          <Paper key={tile.title} variant="outlined" sx={{ p: 2.5 }}>
            <Box sx={{ mb: 1.5 }}>{tile.icon}</Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }} gutterBottom>
              {tile.title}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {tile.body}
            </Typography>
          </Paper>
        ))}
      </Box>

      <Divider sx={{ mb: 3 }} />

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        <Button variant="contained" size="large" onClick={() => navigate('/dashboard')}>
          Go to Recommendations
        </Button>
        <Button
          variant="outlined"
          size="large"
          startIcon={<MenuBookIcon />}
          onClick={() => navigate('/glossary')}
        >
          See how fit scoring works
        </Button>
      </Box>
    </Box>
  );
}
