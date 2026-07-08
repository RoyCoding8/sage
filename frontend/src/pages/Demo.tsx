import { useState } from 'react'
import {
  Box,
  Button,
  Card,
  CardContent,
  Typography,
  Stepper,
  Step,
  StepLabel,
  StepContent,
  Alert,
  CircularProgress,
  Chip,
} from '@mui/material'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { type ExecutionMode, runDemo } from '../api/client'

interface DemoProps {
  mode: ExecutionMode
}

interface DemoResult {
  task?: string
  outcome?: string
  duration_ms?: number
  rule_extracted?: boolean
  steps?: Array<{ step: string; result: string }>
}

export default function Demo({ mode }: DemoProps) {
  const [results, setResults] = useState<DemoResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleRunDemo = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await runDemo(mode)
      setResults(response.results || [])
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to run demo'
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Demo
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Watch Sage learn from a sequence of deployment tasks
      </Typography>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Run Demo
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            This will execute a series of deployment tasks to demonstrate Sage's learning process.
            {mode ? ' Using Qwen Cloud API.' : ' Using offline mode.'}
          </Typography>
          <Button
            variant="contained"
            startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <PlayArrowIcon />}
            onClick={handleRunDemo}
            disabled={loading}
            size="large"
          >
            {loading ? 'Running Demo...' : 'Run Demo'}
          </Button>
        </CardContent>
      </Card>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {results && results.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Demo Results
            </Typography>
            <Stepper orientation="vertical">
              {results.map((result, index) => (
                <Step key={index} active={true} completed={true}>
                  <StepLabel
                    error={result.outcome === 'failed'}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Typography variant="body1" fontWeight={500}>
                        {result.task || `Task ${index + 1}`}
                      </Typography>
                      <Chip
                        label={result.outcome || 'unknown'}
                        size="small"
                        color={
                          result.outcome === 'success'
                            ? 'success'
                            : result.outcome === 'failed'
                            ? 'error'
                            : 'default'
                        }
                      />
                    </Box>
                  </StepLabel>
                  <StepContent>
                    {result.duration_ms && (
                      <Typography variant="body2" color="text.secondary">
                        Duration: {(result.duration_ms / 1000).toFixed(2)}s
                      </Typography>
                    )}
                    {result.rule_extracted && (
                      <Chip
                        label="Rule Extracted"
                        size="small"
                        color="primary"
                        variant="outlined"
                        sx={{ mt: 1 }}
                      />
                    )}
                    {result.steps && result.steps.length > 0 && (
                      <Box sx={{ mt: 1 }}>
                        {result.steps.map((step, stepIdx) => (
                          <Typography key={stepIdx} variant="body2" sx={{ ml: 2 }}>
                            {step.step}: {step.result}
                          </Typography>
                        ))}
                      </Box>
                    )}
                  </StepContent>
                </Step>
              ))}
            </Stepper>
          </CardContent>
        </Card>
      )}
    </Box>
  )
}
