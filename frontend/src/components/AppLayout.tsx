import {
  Box,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  AppBar,
  IconButton,
  Tooltip,
  Divider,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import SearchIcon from '@mui/icons-material/Search';
import BookmarkIcon from '@mui/icons-material/Bookmark';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import SettingsIcon from '@mui/icons-material/Settings';
import LogoutIcon from '@mui/icons-material/Logout';
import { Outlet, NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useNavigate } from 'react-router-dom';

const DRAWER_WIDTH = 228;

const NAV_ITEMS = [
  { label: 'Dashboard',     icon: <DashboardIcon fontSize="small" />,     to: '/dashboard' },
  { label: 'Player Search', icon: <SearchIcon fontSize="small" />,        to: '/players/search' },
  { label: 'Pipeline',      icon: <BookmarkIcon fontSize="small" />,      to: '/pipeline' },
  { label: 'Compare',       icon: <CompareArrowsIcon fontSize="small" />, to: '/compare' },
  { label: 'Settings',      icon: <SettingsIcon fontSize="small" />,      to: '/settings' },
];

export default function AppLayout() {
  const { clearSession } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    clearSession();
    navigate('/login', { replace: true });
  }

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'background.default' }}>
      {/* ── Top bar ─────────────────────────────────────────────────────────── */}
      <AppBar position="fixed" sx={{ zIndex: (t) => t.zIndex.drawer + 1 }}>
        <Toolbar disableGutters sx={{ minHeight: '56px !important' }}>
          {/* Logo panel — exact same width as the drawer */}
          <Box
            sx={{
              width: DRAWER_WIDTH,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              px: 2.5,
              borderRight: '1px solid rgba(255,255,255,0.10)',
              height: 56,
              boxSizing: 'border-box',
            }}
          >
            <img
              src="/portalpoint_textonly_logo_transparent.png"
              alt="PortalPoint"
              style={{ height: 38, objectFit: 'contain', display: 'block' }}
            />
          </Box>

          {/* Spacer */}
          <Box sx={{ flexGrow: 1 }} />

          {/* Logout */}
          <Tooltip title="Logout">
            <IconButton
              color="inherit"
              onClick={handleLogout}
              size="small"
              sx={{ mr: 1, color: '#B0C4DE', '&:hover': { color: '#FF6B35' } }}
            >
              <LogoutIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>

      {/* ── Sidebar drawer ──────────────────────────────────────────────────── */}
      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        {/* Spacer beneath the fixed AppBar */}
        <Toolbar sx={{ minHeight: '56px !important' }} />

        <Divider />

        <List
          disablePadding
          sx={{ pt: 1, px: 1 }}
        >
          {NAV_ITEMS.map(({ label, icon, to }) => (
            <ListItem key={to} disablePadding sx={{ mb: 0.5 }}>
              <ListItemButton
                component={NavLink}
                to={to}
                sx={{
                  py: 0.9,
                  px: 1.5,
                  color: '#B0C4DE',
                  '& .MuiListItemIcon-root': { color: '#B0C4DE' },
                  '& .MuiListItemText-primary': {
                    fontSize: '0.85rem',
                    fontWeight: 400,
                    color: '#B0C4DE',
                  },
                  '&.active': {
                    bgcolor: 'rgba(255, 107, 53, 0.10)',
                    borderLeft: '3px solid #FF6B35',
                    pl: '9px',
                    '& .MuiListItemIcon-root': { color: '#FF6B35' },
                    '& .MuiListItemText-primary': {
                      color: '#FF6B35',
                      fontWeight: 600,
                    },
                  },
                  '&:hover': {
                    bgcolor: 'rgba(255, 255, 255, 0.05)',
                    '& .MuiListItemIcon-root': { color: '#FFFFFF' },
                    '& .MuiListItemText-primary': { color: '#FFFFFF' },
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 36 }}>{icon}</ListItemIcon>
                <ListItemText primary={label} />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      </Drawer>

      {/* ── Main content ────────────────────────────────────────────────────── */}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 3,
          bgcolor: 'background.default',
          minHeight: '100vh',
        }}
      >
        <Toolbar sx={{ minHeight: '56px !important' }} />
        <Outlet />
      </Box>
    </Box>
  );
}
