import { useEffect, useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Grid,
  Button,
  Alert,
  Skeleton,
  Divider,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  TextField,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import {
  getMetrics,
  getMetricsHistory,
  runCounterfactual,
  getCounterfactualHistory,
  type ExecutionMode,
} from '../api/client'

interface MetricsProps {
  mode: ExecutionMode
}

export default function Metrics({ mode }: MetricsProps) {
  const [metrics, setMetrics] = useState<Record<string, unknown>>({})
  const [history, setHistory] = useState<unknown[]>([])
  const [cfHistory, setCfHistory] = useState<unknown[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cfTask, setCfTask] = useState('')
  const [cfResult, setCfResult] = useState<Record<string, unknown> | null>(null)
  const [cfLoading, setCfLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      getMetrics(mode),
      getMetricsHistory(mode),
      getCounterfactualHistory(mode),
    ])
      .then(([m, h, cf]) => {
        setMetrics(m.metrics || {})
        setHistory(h.history || [])
        setCfHistory(cf.entries || [])
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [mode])

  const handleCounterfactual = async () => {
    if (!cfTask.trim()) return
    setCfLoading(true)
    try {
      const result = await runCounterfactual(cfTask, mode)
      setCfResult(result)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed'
      setError(message)
    } finally {
      setCfLoading(false)
    }
  }

  if (loading) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>Metrics</Typography>
        <Skeleton variant="rounded" height={400} />
      </Box>
    )
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Metrics
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Performance metrics and counterfactual analysis
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      <Grid container spacing={3} sx={{ mb: 3 }}>
        {Object.entries(metrics).slice(0, 5).map(([key, value]) => (
          <Grid item xs={6} sm={4} md={2.4} key={key}>
            <Card>
              <CardContent sx={{ textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  {key.replace(/_/g, ' ')}
                </Typography>
                <Typography variant="h4" sx={{ mt: 1 }}>
                  {typeof value === 'number' ? value : String(value)}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Divider sx={{ my: 3 }} />

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Counterfactual Analysis
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Compare task execution with and without memory to measure learning impact.
          </Typography>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField
              fullWidth
              placeholder="Enter a task to analyze..."
              value={cfTask}
              onChange={(e) => setCfTask(e.target.value)}
              size="small"
            />
            <Button
              variant="contained"
              onClick={handleCounterfactual}
              disabled={cfLoading || !cfTask.trim()}
              startIcon={<PlayArrowIcon />}
            >
              {cfLoading ? 'Running...' : 'Run'}
            </Button>
          </Box>

          {cfResult && (
            <Box sx={{ mt: 2 }}>
              <Box
                component="pre"
                sx={{
                  fontSize: '0.75rem',
                  overflow: 'auto',
                  maxHeight: 300,
                  bgcolor: 'action.hover',
                  p: 2,
                  borderRadius: 2,
                }}
              >
                {JSON.stringify(cfResult, null, 2)}
              </Box>
            </Box>
          )}
        </CardContent>
      </Card>

      <Divider sx={{ my: 3 }} />

      <Typography variant="h6" gutterBottom>
        Evaluation History
      </Typography>
      {history.length === 0 ? (
        <Typography color="text.secondary">No evaluation history</Typography>
      ) : (
        history.slice(-10).reverse().map((entry, idx) => (
          <Accordion key={idx} variant="outlined" sx={{ mb: 1 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography variant="body2">
                {String((entry as Record<string, unknown>).task || 'Unknown')} | {String((entry as Record<string, unknown>).timestamp || '').slice(0, 16)}
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <pre style={{ fontSize: '0.75rem', overflow: 'auto' }}>
                {JSON.stringify(entry, null, 2)}
              </pre>
            </AccordionDetails>
          </Accordion>
        ))
      )}

      {cfHistory.length > 0 && (
        <>
          <Divider sx={{ my: 3 }} />
          <Typography variant="h6" gutterBottom>
            Counterfactual History
          </Typography>
          {cfHistory.slice(-10).reverse().map((entry, idx) => (
            <Accordion key={idx} variant="outlined" sx={{ mb: 1 }}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="body2">
                  {String((entry as Record<string, unknown>).task || 'Unknown')} | {String((entry as Record<string, unknown>).timestamp || '').slice(0, 16)}
                </Typography>
              </AccordionSummary>
              <AccordionDetails>
                <pre style={{ fontSize: '0.75rem', overflow: 'auto' }}>
                  {JSON.stringify(entry, null, 2)}
                </pre>
              </AccordionDetails>
            </Accordion>
          ))}
        </>
      )}
    </Box>
  )
}
