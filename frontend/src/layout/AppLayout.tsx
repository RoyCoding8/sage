import { useState, useEffect, useCallback } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Box,
  Drawer,
  AppBar,
  Toolbar,
  Typography,
  IconButton,
  useMediaQuery,
  useTheme,
  Switch,
  FormControlLabel,
  Tooltip,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Alert,
} from '@mui/material'
import MenuIcon from '@mui/icons-material/Menu'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import LightModeIcon from '@mui/icons-material/LightMode'
import CloudIcon from '@mui/icons-material/Cloud'
import CloudOffIcon from '@mui/icons-material/CloudOff'
import SmartToyIcon from '@mui/icons-material/SmartToy'
import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined'
import KeyIcon from '@mui/icons-material/Key'
import Sidebar from './Sidebar'
import {
  getCredentialsStatus,
  setCredentials,
  clearCredentials,
  hasAdminToken,
  setAdminToken,
} from '../api/client'
import type { ExecutionMode } from '../api/client'

const DRAWER_WIDTH = 280

interface AppLayoutProps {
  mode: ExecutionMode
  onModeChange: (mode: ExecutionMode) => void
  darkMode: boolean
  onDarkModeChange: (darkMode: boolean) => void
}

export default function AppLayout({ mode, onModeChange, darkMode, onDarkModeChange }: AppLayoutProps) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const theme = useTheme()
  const isMobile = useMediaQuery(theme.breakpoints.down('md'))
  const location = useLocation()
  const navigate = useNavigate()

  const [hasCredentials, setHasCredentials] = useState(false)
  const [liveEnabled, setLiveEnabled] = useState(false)
  const [cloudMutationsEnabled, setCloudMutationsEnabled] = useState(false)
  const [qwenKeyConfigured, setQwenKeyConfigured] = useState(false)
  const [hasAdminAccess, setHasAdminAccess] = useState(hasAdminToken)
  const [credDialogOpen, setCredDialogOpen] = useState(false)
  const [credKeyId, setCredKeyId] = useState('')
  const [credKeySecret, setCredKeySecret] = useState('')
  const [credRegion, setCredRegion] = useState('cn-hangzhou')
  const [adminToken, setAdminTokenInput] = useState('')
  const [credSaving, setCredSaving] = useState(false)
  const [credError, setCredError] = useState('')

  const checkCredentials = useCallback(async () => {
    if (!hasAdminToken()) {
      setHasAdminAccess(false)
      setHasCredentials(false)
      setLiveEnabled(false)
      setCloudMutationsEnabled(false)
      setQwenKeyConfigured(false)
      if (mode !== 'offline') onModeChange('offline')
      return
    }
    try {
      const status = await getCredentialsStatus()
      setHasAdminAccess(true)
      setHasCredentials(status.has_credentials)
      setLiveEnabled(status.live_enabled)
      setCloudMutationsEnabled(status.cloud_mutations_enabled)
      setQwenKeyConfigured(status.qwen_key_configured)
      if (!status.live_enabled || !status.qwen_key_configured) {
        if (mode !== 'offline') onModeChange('offline')
      } else if (
        mode === 'cloud'
        && (!status.cloud_mutations_enabled || !status.has_credentials)
      ) {
        onModeChange('qwen')
      }
    } catch {
      setHasAdminAccess(false)
      setHasCredentials(false)
      setLiveEnabled(false)
      setCloudMutationsEnabled(false)
      setQwenKeyConfigured(false)
      if (mode !== 'offline') onModeChange('offline')
    }
  }, [mode, onModeChange])

  useEffect(() => {
    checkCredentials()
  }, [checkCredentials])

  const qwenAvailable = liveEnabled && qwenKeyConfigured
  const qwenEnabled = qwenAvailable && (mode === 'qwen' || mode === 'cloud')
  const cloudEnabled = qwenEnabled
    && cloudMutationsEnabled
    && hasCredentials
    && mode === 'cloud'

  const handleQwenToggle = (checked: boolean) => {
    if (checked) {
      onModeChange('qwen')
    } else {
      onModeChange('offline')
    }
  }

  const handleCloudToggle = async (checked: boolean) => {
    if (checked) {
      if (!hasCredentials) {
        setCredDialogOpen(true)
        return
      }
      onModeChange('cloud')
    } else {
      onModeChange('qwen')
    }
  }

  const handleCredSave = async () => {
    if (!adminToken.trim() && !hasAdminAccess) {
      setCredError('The Sage administration token is required')
      return
    }
    if (Boolean(credKeyId.trim()) !== Boolean(credKeySecret.trim())) {
      setCredError('Provide both Alibaba Cloud key fields or leave both blank')
      return
    }
    setCredSaving(true)
    setCredError('')
    try {
      if (adminToken.trim()) setAdminToken(adminToken.trim())
      const status = credKeyId.trim()
        ? await setCredentials(credKeyId.trim(), credKeySecret.trim(), credRegion.trim())
            .then(() => getCredentialsStatus())
        : await getCredentialsStatus()
      setHasAdminAccess(true)
      setHasCredentials(status.has_credentials)
      setLiveEnabled(status.live_enabled)
      setCloudMutationsEnabled(status.cloud_mutations_enabled)
      setQwenKeyConfigured(status.qwen_key_configured)
      setCredDialogOpen(false)
      setAdminTokenInput('')
      setCredKeyId('')
      setCredKeySecret('')
      if (status.has_credentials && status.cloud_mutations_enabled) onModeChange('cloud')
    } catch (e: unknown) {
      setCredError(e instanceof Error ? e.message : 'Failed to save credentials')
    } finally {
      setCredSaving(false)
    }
  }

  const handleCredClear = async () => {
    try {
      await clearCredentials()
      setHasCredentials(false)
      if (mode === 'cloud') onModeChange('qwen')
    } catch { /* ignore */ }
    setCredDialogOpen(false)
  }

  const handleDrawerToggle = () => {
    setMobileOpen(!mobileOpen)
  }

  const handleNavigation = (path: string) => {
    navigate(path)
    if (isMobile) {
      setMobileOpen(false)
    }
  }

  const modeLabel = mode === 'cloud'
    ? 'LIVE CLOUD'
    : mode === 'qwen'
      ? 'SIMULATED / Qwen'
      : 'SIMULATED / Offline'
  const modeColor = mode === 'cloud' ? 'success' : mode === 'qwen' ? 'primary' : 'default'

  const drawer = (
    <Sidebar
      currentPath={location.pathname}
      onNavigate={handleNavigation}
    />
  )

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        sx={{
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          ml: { md: `${DRAWER_WIDTH}px` },
          bgcolor: 'background.paper',
          color: 'text.primary',
          borderBottom: '1px solid',
          borderColor: 'divider',
        }}
      >
        <Toolbar sx={{ minHeight: '64px !important' }}>
          <IconButton
            color="inherit"
            aria-label="open drawer"
            edge="start"
            onClick={handleDrawerToggle}
            sx={{ mr: 2, display: { md: 'none' } }}
          >
            <MenuIcon />
          </IconButton>

          <Chip
            label={modeLabel}
            color={modeColor as 'success' | 'primary' | 'default'}
            size="small"
            variant="outlined"
            sx={{ mr: 2 }}
          />

          <Box sx={{ flexGrow: 1 }} />

          <Tooltip title={hasAdminAccess ? 'API access connected (click to manage)' : 'Connect to the Sage API'}>
            <IconButton
              aria-label="connect to Sage API"
              onClick={() => setCredDialogOpen(true)}
              color="inherit"
              sx={{
                mr: 1,
                color: hasAdminAccess ? 'success.main' : 'text.secondary',
              }}
            >
              <KeyIcon fontSize="small" />
            </IconButton>
          </Tooltip>

          <Tooltip title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}>
            <IconButton
              onClick={() => onDarkModeChange(!darkMode)}
              color="inherit"
              sx={{ mr: 1 }}
            >
              {darkMode ? <LightModeIcon /> : <DarkModeIcon />}
            </IconButton>
          </Tooltip>

          <Tooltip title={
            !liveEnabled
              ? 'Live Qwen mode is disabled by the server safety switch'
              : qwenEnabled
              ? 'Disable Qwen LLM (switch to offline heuristics)'
              : 'Enable Qwen LLM for reasoning'
          }>
            <FormControlLabel
              control={
                <Switch
                  checked={qwenEnabled}
                  onChange={(e) => handleQwenToggle(e.target.checked)}
                  color="primary"
                  size="small"
                  disabled={!liveEnabled}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  {qwenEnabled ? <SmartToyIcon fontSize="small" /> : <SmartToyOutlinedIcon fontSize="small" />}
                  <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.8rem' }}>
                    Qwen
                  </Typography>
                </Box>
              }
              sx={{ mr: 1 }}
            />
          </Tooltip>

          <Tooltip title={
            !qwenEnabled
              ? 'Enable Qwen LLM first to use Cloud'
              : !cloudMutationsEnabled
              ? 'Cloud mutations are disabled by the server safety switch'
              : cloudEnabled
              ? 'Disable real Alibaba Cloud (use sandbox)'
              : 'Enable real Alibaba Cloud deployments'
          }>
            <span>
              <FormControlLabel
                control={
                  <Switch
                    checked={cloudEnabled}
                    onChange={(e) => handleCloudToggle(e.target.checked)}
                    color="success"
                    size="small"
                    disabled={!qwenEnabled || !cloudMutationsEnabled}
                  />
                }
                label={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, opacity: qwenEnabled ? 1 : 0.4 }}>
                    {cloudEnabled ? <CloudIcon fontSize="small" color="success" /> : <CloudOffIcon fontSize="small" />}
                    <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.8rem' }}>
                      Cloud
                    </Typography>
                  </Box>
                }
                sx={{ mr: 0 }}
                disabled={!qwenEnabled || !cloudMutationsEnabled}
              />
            </span>
          </Tooltip>
        </Toolbar>
      </AppBar>

      <Box
        component="nav"
        sx={{ width: { md: DRAWER_WIDTH }, flexShrink: { md: 0 } }}
      >
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={handleDrawerToggle}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', md: 'none' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH },
          }}
        >
          {drawer}
        </Drawer>
        <Drawer
          variant="permanent"
          sx={{
            display: { xs: 'none', md: 'block' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: DRAWER_WIDTH },
          }}
          open
        >
          {drawer}
        </Drawer>
      </Box>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 4,
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          mt: '64px',
          bgcolor: 'background.default',
          minHeight: 'calc(100vh - 64px)',
        }}
      >
        <Outlet />
      </Box>

      <Dialog open={credDialogOpen} onClose={() => setCredDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Sage API &amp; Alibaba Cloud Access</DialogTitle>
        <DialogContent>
          <Alert severity="info" sx={{ mb: 2, mt: 1 }}>
            The administration token stays in this browser tab. Alibaba credentials are kept in server memory only and cleared when the server restarts.
          </Alert>
          {credError && <Alert severity="error" sx={{ mb: 2 }}>{credError}</Alert>}
          <TextField
              label="Sage Administration Token (not the Qwen key)"
            fullWidth
            margin="dense"
            type="password"
            value={adminToken}
            onChange={(e) => setAdminTokenInput(e.target.value)}
              placeholder={hasAdminAccess ? 'Already connected; leave blank to keep' : 'Matches SAGE_ADMIN_TOKEN'}
              sx={{ mb: 2 }}
            />
          {hasAdminAccess && liveEnabled && !qwenKeyConfigured && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Qwen is disabled because SAGE_QWEN_API_KEY is not configured on the server. Add it to .env and restart the API.
            </Alert>
          )}
          <TextField
            label="Access Key ID"
            fullWidth
            margin="dense"
            value={credKeyId}
            onChange={(e) => setCredKeyId(e.target.value)}
            placeholder="Optional unless enabling real Cloud mode"
            sx={{ mb: 2 }}
          />
          <TextField
            label="Access Key Secret"
            fullWidth
            margin="dense"
            type="password"
            value={credKeySecret}
            onChange={(e) => setCredKeySecret(e.target.value)}
            placeholder="Your secret key"
            sx={{ mb: 2 }}
          />
          <TextField
            label="Region"
            fullWidth
            margin="dense"
            value={credRegion}
            onChange={(e) => setCredRegion(e.target.value)}
            placeholder="cn-hangzhou"
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          {hasCredentials && (
            <Button onClick={handleCredClear} color="error" sx={{ mr: 'auto' }}>
              Clear
            </Button>
          )}
          <Button onClick={() => setCredDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleCredSave} variant="contained" disabled={credSaving}>
            {credSaving ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
