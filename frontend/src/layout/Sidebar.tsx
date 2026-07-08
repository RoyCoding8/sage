import {
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  Divider,
} from '@mui/material'
import DashboardIcon from '@mui/icons-material/Dashboard'
import InfoIcon from '@mui/icons-material/Info'
import PlayCircleIcon from '@mui/icons-material/PlayCircle'
import TerminalIcon from '@mui/icons-material/Terminal'
import PsychologyIcon from '@mui/icons-material/Psychology'
import AnalyticsIcon from '@mui/icons-material/Analytics'
import SpeedIcon from '@mui/icons-material/Speed'
import SettingsIcon from '@mui/icons-material/Settings'
import HistoryIcon from '@mui/icons-material/History'

interface SidebarProps {
  currentPath: string
  onNavigate: (path: string) => void
}

const navItems = [
  { path: '/', label: 'Dashboard', icon: <DashboardIcon /> },
  { path: '/status', label: 'Status', icon: <InfoIcon /> },
  { path: '/demo', label: 'Demo', icon: <PlayCircleIcon /> },
  { path: '/interactive', label: 'Interactive', icon: <TerminalIcon /> },
  { path: '/memory', label: 'Memory', icon: <PsychologyIcon /> },
  { path: '/metrics', label: 'Metrics', icon: <AnalyticsIcon /> },
  { path: '/benchmark', label: 'Benchmark', icon: <SpeedIcon /> },
  { path: '/preferences', label: 'Preferences', icon: <SettingsIcon /> },
  { path: '/sessions', label: 'Sessions', icon: <HistoryIcon /> },
]

export default function Sidebar({ currentPath, onNavigate }: SidebarProps) {
  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ p: 3, pb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 0.5 }}>
          <Box
            sx={{
              width: 36,
              height: 36,
              borderRadius: 2,
              bgcolor: 'primary.main',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontWeight: 700,
              fontSize: '1.125rem',
            }}
          >
            S
          </Box>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 600, lineHeight: 1.2, color: 'text.primary' }}>
              Sage
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.6875rem' }}>
              Deployment Autopilot
            </Typography>
          </Box>
        </Box>
      </Box>

      <Divider sx={{ mx: 2 }} />

      <List sx={{ flex: 1, px: 1.5, py: 2 }}>
        {navItems.map((item) => {
          const isActive = currentPath === item.path
          return (
            <ListItem key={item.path} disablePadding sx={{ mb: 0.25 }}>
              <ListItemButton
                onClick={() => onNavigate(item.path)}
                selected={isActive}
                sx={{
                  borderRadius: 2,
                  py: 1,
                  px: 2,
                }}
              >
                <ListItemIcon
                  sx={{
                    minWidth: 36,
                    color: isActive ? 'primary.main' : 'text.secondary',
                    '& .MuiSvgIcon-root': {
                      fontSize: '1.25rem',
                    },
                  }}
                >
                  {item.icon}
                </ListItemIcon>
                <ListItemText
                  primary={item.label}
                  primaryTypographyProps={{
                    fontSize: '0.875rem',
                    fontWeight: isActive ? 600 : 400,
                  }}
                />
              </ListItemButton>
            </ListItem>
          )
        })}
      </List>
    </Box>
  )
}
