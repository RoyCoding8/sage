import { useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Alert,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
} from '@mui/material'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { type ExecutionMode, runBenchmark } from '../api/client'

interface BenchmarkProps {
  mode: ExecutionMode
}

interface ScenarioResult {
  scenario?: string
  passed?: boolean
  outcome?: string
  expected?: string
  actual?: string
  duration_ms?: number
}

export default function Benchmark({ mode }: BenchmarkProps) {
  const [results, setResults] = useState<ScenarioResult[] | null>(null)
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleRun = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await runBenchmark(mode)
      setResults(response.results || [])
      setSummary(response.summary || {})
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to run benchmark'
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Benchmark
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Run a fixed suite of scenarios to detect regressions and measure improvement
      </Typography>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Button
            variant="contained"
            size="large"
            startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <PlayArrowIcon />}
            onClick={handleRun}
            disabled={loading}
          >
            {loading ? 'Running Benchmark...' : 'Run Benchmark'}
          </Button>
        </CardContent>
      </Card>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {summary && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Summary
            </Typography>
            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              {Object.entries(summary).map(([key, value]) => (
                <Chip
                  key={key}
                  label={`${key}: ${typeof value === 'number' ? value : String(value)}`}
                  variant="outlined"
                />
              ))}
            </Box>
          </CardContent>
        </Card>
      )}

      {results && results.length > 0 && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Scenario</TableCell>
                <TableCell>Result</TableCell>
                <TableCell>Expected</TableCell>
                <TableCell>Actual</TableCell>
                <TableCell>Duration</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {results.map((r, idx) => (
                <TableRow key={idx}>
                  <TableCell>{r.scenario || `Scenario ${idx + 1}`}</TableCell>
                  <TableCell>
                    <Chip
                      label={r.passed ? 'Passed' : 'Failed'}
                      color={r.passed ? 'success' : 'error'}
                      size="small"
                    />
                  </TableCell>
                  <TableCell>{r.expected || '-'}</TableCell>
                  <TableCell>{r.actual || r.outcome || '-'}</TableCell>
                  <TableCell>
                    {r.duration_ms ? `${(r.duration_ms / 1000).toFixed(2)}s` : '-'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}
