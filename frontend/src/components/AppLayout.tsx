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
  Typography,
  IconButton,
  Tooltip,
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

const DRAWER_WIDTH = 220;

const NAV_ITEMS = [
  { label: 'Dashboard', icon: <DashboardIcon />, to: '/dashboard' },
  { label: 'Player Search', icon: <SearchIcon />, to: '/players/search' },
  { label: 'Pipeline', icon: <BookmarkIcon />, to: '/pipeline' },
  { label: 'Compare', icon: <CompareArrowsIcon />, to: '/compare' },
  { label: 'Settings', icon: <SettingsIcon />, to: '/settings' },
];

export default function AppLayout() {
  const { clearSession } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    clearSession();
    navigate('/login', { replace: true });
  }

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        sx={{ zIndex: (theme) => theme.zIndex.drawer + 1, bgcolor: 'primary.dark' }}
      >
        <Toolbar sx={{ justifyContent: 'space-between' }}>
          <Typography variant="h6" fontWeight={700} letterSpacing={1}>
            PortalPoint
          </Typography>
          <Tooltip title="Logout">
            <IconButton color="inherit" onClick={handleLogout}>
              <LogoutIcon />
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        <Toolbar />
        <List disablePadding>
          {NAV_ITEMS.map(({ label, icon, to }) => (
            <ListItem key={to} disablePadding>
              <ListItemButton
                component={NavLink}
                to={to}
                sx={{
                  '&.active': {
                    bgcolor: 'primary.light',
                    color: 'primary.contrastText',
                    '& .MuiListItemIcon-root': { color: 'primary.contrastText' },
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 40 }}>{icon}</ListItemIcon>
                <ListItemText primary={label} />
              </ListItemButton>
            </ListItem>
          ))}
        </List>
      </Drawer>

      <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
        <Toolbar />
        <Outlet />
      </Box>
    </Box>
  );
}
