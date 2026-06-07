import { Box, Typography, Skeleton, Alert } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { getRecommendations } from '../api/recommendations';
import { useAuth } from '../context/AuthContext';
import RecommendationCard from '../components/RecommendationCard';

export default function DashboardPage() {
  const { userId } = useAuth();

  const { data, isLoading, error } = useQuery({
    queryKey: ['recommendations', userId],
    queryFn: () => getRecommendations(userId!),
    enabled: userId !== null,
  });

  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" fontWeight={700}>
          Portal Recommendations
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Top matches ranked by composite fit score
          {data && ` · ${data.total} players`}
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load recommendations. Make sure the API server is running.
        </Alert>
      )}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: '1fr 1fr 1fr' },
          gap: 2,
        }}
      >
        {isLoading
          ? Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} variant="rectangular" height={300} sx={{ borderRadius: 1 }} />
            ))
          : data?.recommendations.map((item) => (
              <RecommendationCard key={item.player_id} item={item} />
            ))}
      </Box>
    </Box>
  );
}
