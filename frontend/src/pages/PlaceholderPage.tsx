import { Box, Typography, Chip } from '@mui/material';
import ConstructionIcon from '@mui/icons-material/Construction';

interface Props {
  title: string;
  description?: string;
}

export default function PlaceholderPage({ title, description }: Props) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        gap: 2,
        color: 'text.secondary',
      }}
    >
      <ConstructionIcon sx={{ fontSize: 56, color: 'warning.main' }} />
      <Typography variant="h5" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      {description && (
        <Typography variant="body1" sx={{ textAlign: 'center', maxWidth: 480 }}>
          {description}
        </Typography>
      )}
      <Chip label="Coming in next session" color="warning" variant="outlined" />
    </Box>
  );
}
