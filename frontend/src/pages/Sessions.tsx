import { useEffect, useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Grid,
  Alert,
  Skeleton,
  Divider,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
} from '@mui/material'
import { type ExecutionMode, getSessions } from '../api/client'

interface SessionsProps {
  mode: ExecutionMode
}

interface SessionData {
  sessions: unknown[]
  cumulative: Record<string, unknown>
  current: Record<string, unknown>
}

export default function Sessions({ mode }: SessionsProps) {
  const [data, setData] = useState<SessionData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    getSessions(mode)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [mode])

  if (loading) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>Sessions</Typography>
        <Skeleton variant="rounded" height={400} />
      </Box>
    )
  }

  if (error) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>Sessions</Typography>
        <Alert severity="error">{error}</Alert>
      </Box>
    )
  }

  const current = data?.current || {}
  const cumulative = data?.cumulative || {}
  const sessions = data?.sessions || []

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Sessions
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Track session history and cumulative statistics
      </Typography>

      <Typography variant="h6" gutterBottom sx={{ mt: 2 }}>
        Current Session
      </Typography>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">Tasks Completed</Typography>
              <Typography variant="h4">{String(current.tasks_completed ?? 0)}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">Success Rate</Typography>
              <Typography variant="h4">{String(current.success_rate ?? '0/0')}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">Corrections</Typography>
              <Typography variant="h4">{String(current.corrections ?? 0)}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">Start Time</Typography>
              <Typography variant="body1">{String(current.start_time || 'N/A').slice(0, 16)}</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Divider sx={{ my: 3 }} />

      <Typography variant="h6" gutterBottom>
        Cumulative Stats
      </Typography>
      {Object.keys(cumulative).length > 0 ? (
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {Object.entries(cumulative).map(([key, value]) => (
            <Grid item xs={6} sm={3} key={key}>
              <Card>
                <CardContent sx={{ textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">
                    {key.replace(/_/g, ' ')}
                  </Typography>
                  <Typography variant="h5">
                    {typeof value === 'number' ? value : String(value)}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      ) : (
        <Typography color="text.secondary" sx={{ mb: 3 }}>No cumulative stats yet</Typography>
      )}

      <Divider sx={{ my: 3 }} />

      <Typography variant="h6" gutterBottom>
        Session History
      </Typography>
      {sessions.length === 0 ? (
        <Card>
          <CardContent sx={{ textAlign: 'center', py: 4 }}>
            <Typography color="text.secondary">
              No session history. Complete a session to see it here.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Session ID</TableCell>
                <TableCell>Tasks</TableCell>
                <TableCell>Success</TableCell>
                <TableCell>Rules</TableCell>
                <TableCell>Started</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(sessions as Array<Record<string, unknown>>).slice().reverse().slice(0, 10).map((s, idx) => (
                <TableRow key={idx}>
                  <TableCell>
                    <Chip
                      label={String(s.session_id || '?')}
                      size="small"
                      variant="outlined"
                      sx={{ fontFamily: 'monospace' }}
                    />
                  </TableCell>
                  <TableCell>{String(s.tasks_completed ?? 0)}</TableCell>
                  <TableCell>{String(s.success_rate || '0/0')}</TableCell>
                  <TableCell>
                    {Array.isArray(s.rules_learned) ? (s.rules_learned as unknown[]).length : 0}
                  </TableCell>
                  <TableCell>{String(s.start_time || '').slice(0, 16)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}
