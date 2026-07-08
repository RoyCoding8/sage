import { useEffect, useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Grid,
  Typography,
  Chip,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  ListItemButton,
  Skeleton,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  IconButton,
  Divider,
} from '@mui/material'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import InfoIcon from '@mui/icons-material/Info'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import CloseIcon from '@mui/icons-material/Close'
import { type ExecutionMode, getDashboard } from '../api/client'

interface DashboardProps {
  mode: ExecutionMode
}

interface ActivityEntry {
  task?: string
  outcome?: string
  timestamp?: string
  [key: string]: unknown
}

interface DashboardData {
  status: {
    rules_learned: number
    total_tasks: number
    successes: number
    failures: number
    corrections: number
  }
  memory_summary: Record<string, unknown>
  recent_activity: ActivityEntry[]
}

export default function Dashboard({ mode }: DashboardProps) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAllActivity, setShowAllActivity] = useState(false)
  const [showAllMemory, setShowAllMemory] = useState(false)
  const [selectedActivity, setSelectedActivity] = useState<ActivityEntry | null>(null)

  const formatMemoryValue = (value: unknown): string => {
    if (value === null || value === undefined) return 'N/A'
    if (typeof value === 'number') return value.toLocaleString()
    if (typeof value === 'boolean') return value ? 'Yes' : 'No'
    if (typeof value === 'string') return value
    if (Array.isArray(value)) return `${value.length} items`
    if (typeof value === 'object') {
      const keys = Object.keys(value as Record<string, unknown>)
      if (keys.length === 0) return 'N/A'
      return `${keys.length} properties`
    }
    return String(value)
  }

  const isExpandableValue = (value: unknown): boolean => {
    if (typeof value === 'object' && value !== null) {
      if (Array.isArray(value)) return value.length > 0
      return Object.keys(value as Record<string, unknown>).length > 0
    }
    return false
  }

  useEffect(() => {
    setLoading(true)
    getDashboard(mode)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [mode])

  if (loading) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>
          Dashboard
        </Typography>
        <Grid container spacing={3}>
          {[1, 2, 3, 4].map((i) => (
            <Grid item xs={12} sm={6} md={3} key={i}>
              <Skeleton variant="rounded" height={120} />
            </Grid>
          ))}
        </Grid>
      </Box>
    )
  }

  if (error) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>
          Dashboard
        </Typography>
        <Typography color="error">Error: {error}</Typography>
      </Box>
    )
  }

  const status = data?.status

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Dashboard
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Overview of Sage's learning progress and recent activity
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Rules Learned
              </Typography>
              <Typography variant="h3" color="primary.main">
                {status?.rules_learned ?? 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Total Tasks
              </Typography>
              <Typography variant="h3">
                {status?.total_tasks ?? 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Successes
              </Typography>
              <Typography variant="h3" color="success.main">
                {status?.successes ?? 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Corrections
              </Typography>
              <Typography variant="h3" color="warning.main">
                {status?.corrections ?? 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Recent Activity: clickable items open detail dialog */}
        <Grid item xs={12} md={8}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Typography variant="h6">
                  Recent Activity
                </Typography>
                {data?.recent_activity && data.recent_activity.length > 5 && (
                  <Chip
                    label={showAllActivity ? 'Show less' : `Show all ${data.recent_activity.length}`}
                    size="small"
                    onClick={() => setShowAllActivity(!showAllActivity)}
                    variant="outlined"
                  />
                )}
              </Box>
              {data?.recent_activity && data.recent_activity.length > 0 ? (
                <List sx={{ py: 0 }}>
                  {(showAllActivity ? data.recent_activity : data.recent_activity.slice(0, 5)).map((activity, idx) => (
                    <ListItem key={idx} sx={{ px: 0, py: 0 }} disablePadding divider={idx < (showAllActivity ? data.recent_activity.length : Math.min(5, data.recent_activity.length)) - 1}>
                      <ListItemButton
                        onClick={() => setSelectedActivity(activity)}
                        sx={{ px: 1, py: 1, borderRadius: 1 }}
                      >
                        <ListItemIcon sx={{ minWidth: 36 }}>
                          {activity.outcome === 'success' ? (
                            <CheckCircleIcon color="success" fontSize="small" />
                          ) : activity.outcome === 'failed' ? (
                            <ErrorIcon color="error" fontSize="small" />
                          ) : (
                            <InfoIcon color="info" fontSize="small" />
                          )}
                        </ListItemIcon>
                        <ListItemText
                          primary={activity.task || 'Unknown task'}
                          secondary={activity.timestamp}
                          primaryTypographyProps={{ fontSize: '0.875rem', fontWeight: 500 }}
                          secondaryTypographyProps={{ fontSize: '0.75rem' }}
                        />
                        <Chip
                          label={activity.outcome || 'unknown'}
                          size="small"
                          color={
                            activity.outcome === 'success'
                              ? 'success'
                              : activity.outcome === 'failed'
                              ? 'error'
                              : 'default'
                          }
                          sx={{ fontSize: '0.6875rem' }}
                        />
                      </ListItemButton>
                    </ListItem>
                  ))}
                </List>
              ) : (
                <Typography color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
                  No recent activity
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Memory Summary: collapsed by default, show all button */}
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Memory Summary
              </Typography>
              {data?.memory_summary && Object.keys(data.memory_summary).length > 0 ? (
                <Box>
                  {Object.entries(data.memory_summary).slice(0, showAllMemory ? undefined : 4).map(([key, value]) => (
                    isExpandableValue(value) ? (
                      <Accordion
                        key={key}
                        disableGutters
                        elevation={0}
                        sx={{
                          '&:before': { display: 'none' },
                          border: '1px solid',
                          borderColor: 'divider',
                          borderRadius: '8px !important',
                          mb: 1,
                          overflow: 'hidden',
                        }}
                      >
                        <AccordionSummary
                          expandIcon={<ExpandMoreIcon fontSize="small" />}
                          sx={{ minHeight: 44, px: 2, '& .MuiAccordionSummary-content': { my: 1 } }}
                        >
                          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', pr: 1 }}>
                            <Typography variant="body2" fontWeight={500} sx={{ textTransform: 'capitalize' }}>
                              {key.replace(/_/g, ' ')}
                            </Typography>
                            <Typography variant="body2" color="text.secondary">
                              {formatMemoryValue(value)}
                            </Typography>
                          </Box>
                        </AccordionSummary>
                        <AccordionDetails sx={{ px: 2, pt: 0, pb: 2 }}>
                          {typeof value === 'object' && value !== null && !Array.isArray(value) ? (
                            Object.entries(value as Record<string, unknown>).map(([subKey, subValue]) => (
                              <Box key={subKey} sx={{ display: 'flex', justifyContent: 'space-between', py: 0.5 }}>
                                <Typography variant="body2" color="text.secondary" sx={{ textTransform: 'capitalize' }}>
                                  {subKey.replace(/_/g, ' ')}
                                </Typography>
                                <Typography variant="body2" fontWeight={500}>
                                  {formatMemoryValue(subValue)}
                                </Typography>
                              </Box>
                            ))
                          ) : Array.isArray(value) ? (
                            (value as unknown[]).slice(0, 10).map((item, i) => (
                              <Typography key={i} variant="body2" sx={{ py: 0.25 }}>
                                {formatMemoryValue(item)}
                              </Typography>
                            ))
                          ) : (
                            <Typography variant="body2">{formatMemoryValue(value)}</Typography>
                          )}
                        </AccordionDetails>
                      </Accordion>
                    ) : (
                      <Box key={key} sx={{ display: 'flex', justifyContent: 'space-between', py: 1, borderBottom: '1px solid', borderColor: 'divider' }}>
                        <Typography variant="body2" color="text.secondary" sx={{ textTransform: 'capitalize' }}>
                          {key.replace(/_/g, ' ')}
                        </Typography>
                        <Typography variant="body2" fontWeight={500}>
                          {formatMemoryValue(value)}
                        </Typography>
                      </Box>
                    )
                  ))}
                  {Object.keys(data.memory_summary).length > 4 && (
                    <Box sx={{ mt: 1, textAlign: 'center' }}>
                      <Chip
                        label={showAllMemory ? 'Show less' : `Show all ${Object.keys(data.memory_summary).length}`}
                        size="small"
                        onClick={() => setShowAllMemory(!showAllMemory)}
                        variant="outlined"
                      />
                    </Box>
                  )}
                </Box>
              ) : (
                <Typography color="text.secondary">No memory data</Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Activity Detail Dialog */}
      <Dialog
        open={selectedActivity !== null}
        onClose={() => setSelectedActivity(null)}
        maxWidth="sm"
        fullWidth
      >
        {selectedActivity && (
          <>
            <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                {selectedActivity.outcome === 'success' ? (
                  <CheckCircleIcon color="success" />
                ) : selectedActivity.outcome === 'failed' ? (
                  <ErrorIcon color="error" />
                ) : (
                  <InfoIcon color="info" />
                )}
                <Typography variant="h6" component="span">
                  Activity Details
                </Typography>
              </Box>
              <IconButton onClick={() => setSelectedActivity(null)} size="small">
                <CloseIcon />
              </IconButton>
            </DialogTitle>
            <DialogContent dividers>
              <Box sx={{ mb: 2 }}>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  Task
                </Typography>
                <Typography variant="body1" fontWeight={500}>
                  {selectedActivity.task || 'Unknown task'}
                </Typography>
              </Box>
              <Divider sx={{ my: 2 }} />
              <Grid container spacing={2}>
                <Grid item xs={6}>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Outcome
                  </Typography>
                  <Chip
                    label={selectedActivity.outcome || 'unknown'}
                    size="small"
                    color={
                      selectedActivity.outcome === 'success'
                        ? 'success'
                        : selectedActivity.outcome === 'failed'
                        ? 'error'
                        : 'default'
                    }
                  />
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Timestamp
                  </Typography>
                  <Typography variant="body1">
                    {selectedActivity.timestamp || 'N/A'}
                  </Typography>
                </Grid>
              </Grid>
              {/* Render all additional fields */}
              {Object.entries(selectedActivity)
                .filter(([key]) => !['task', 'outcome', 'timestamp'].includes(key))
                .length > 0 && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Additional Details
                  </Typography>
                  {Object.entries(selectedActivity)
                    .filter(([key]) => !['task', 'outcome', 'timestamp'].includes(key))
                    .map(([key, value]) => (
                      <Box key={key} sx={{ display: 'flex', justifyContent: 'space-between', py: 1, borderBottom: '1px solid', borderColor: 'divider' }}>
                        <Typography variant="body2" color="text.secondary" sx={{ textTransform: 'capitalize' }}>
                          {key.replace(/_/g, ' ')}
                        </Typography>
                        <Typography variant="body2" fontWeight={500} sx={{ textAlign: 'right', maxWidth: '60%', wordBreak: 'break-word' }}>
                          {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? 'N/A')}
                        </Typography>
                      </Box>
                    ))}
                </>
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setSelectedActivity(null)}>Close</Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </Box>
  )
}
