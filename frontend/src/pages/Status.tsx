import { useEffect, useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Grid,
  Typography,
  Chip,
  LinearProgress,
  Skeleton,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Button,
  Snackbar,
  Alert,
} from '@mui/material'
import { type ExecutionMode, getStatus, type StatusResponse, getModelConfig, setModelConfig, type ModelConfig } from '../api/client'

interface StatusProps {
  mode: ExecutionMode
}

const TASK_DESCRIPTIONS: Record<string, string> = {
  execution: 'Tool selection, step planning, port decisions — called most often',
  reflection: 'Rule extraction, learning from corrections — needs deep reasoning',
  planning: 'Task decomposition — used less frequently',
}

export default function Status({ mode }: StatusProps) {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Model config state
  const [modelConfig, setModelConfigState] = useState<ModelConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(true)
  const [configSaving, setConfigSaving] = useState(false)
  const [configError, setConfigError] = useState<string | null>(null)
  const [configSuccess, setConfigSuccess] = useState(false)

  useEffect(() => {
    setLoading(true)
    getStatus(mode)
      .then(setStatus)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [mode])

  useEffect(() => {
    setConfigLoading(true)
    getModelConfig()
      .then(setModelConfigState)
      .catch((err) => setConfigError(err.message))
      .finally(() => setConfigLoading(false))
  }, [])

  const handleModelChange = (taskType: string, value: string) => {
    if (!modelConfig) return
    setModelConfigState({
      ...modelConfig,
      config: { ...modelConfig.config, [taskType]: value },
    })
  }

  const handleSaveConfig = async () => {
    if (!modelConfig) return
    setConfigSaving(true)
    setConfigError(null)
    try {
      const result = await setModelConfig(modelConfig.config)
      setModelConfigState(result)
      setConfigSuccess(true)
    } catch (err: unknown) {
      setConfigError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setConfigSaving(false)
    }
  }

  if (loading) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>
          Status
        </Typography>
        <Skeleton variant="rounded" height={400} />
      </Box>
    )
  }

  if (error) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>
          Status
        </Typography>
        <Typography color="error">Error: {error}</Typography>
      </Box>
    )
  }

  const successRate = status ? status.success_rate * 100 : 0

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        System Status
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Current state of the Sage deployment agent
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Connection Status
              </Typography>
              <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                <Chip
                  label={mode === 'cloud' ? 'Cloud (Alibaba)' : mode === 'qwen' ? 'Qwen LLM' : 'Offline Mode'}
                  color={mode === 'cloud' ? 'success' : mode === 'qwen' ? 'primary' : 'default'}
                  variant="outlined"
                />
                <Chip
                  label={mode === 'cloud' ? 'Environment: Alibaba Cloud' : 'Environment: Simulated Sandbox'}
                  color={mode === 'cloud' ? 'success' : 'warning'}
                  variant="outlined"
                />
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Success Rate
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <Box sx={{ flexGrow: 1 }}>
                  <LinearProgress
                    variant="determinate"
                    value={successRate}
                    sx={{ height: 12, borderRadius: 6 }}
                    color={successRate >= 70 ? 'success' : successRate >= 40 ? 'warning' : 'error'}
                  />
                </Box>
                <Typography variant="h5" fontWeight={500}>
                  {successRate.toFixed(1)}%
                </Typography>
              </Box>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {status?.successes} of {status?.total_tasks} tasks successful
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                Rules Learned
              </Typography>
              <Typography variant="h3" color="primary.main" sx={{ my: 1 }}>
                {status?.rules_learned}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                Total Tasks
              </Typography>
              <Typography variant="h3" sx={{ my: 1 }}>
                {status?.total_tasks}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                Failures
              </Typography>
              <Typography variant="h3" color="error.main" sx={{ my: 1 }}>
                {status?.failures}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                Corrections
              </Typography>
              <Typography variant="h3" color="warning.main" sx={{ my: 1 }}>
                {status?.corrections}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Model Configuration */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Model Configuration
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Select which Qwen model to use for each task type. Changes take effect on the next task execution.
              </Typography>

              {configLoading ? (
                <Skeleton variant="rounded" height={200} />
              ) : modelConfig ? (
                <Grid container spacing={3}>
                  {Object.entries(modelConfig.config).map(([taskType, currentModel]) => (
                    <Grid item xs={12} sm={4} key={taskType}>
                      <FormControl fullWidth size="small">
                        <InputLabel>{taskType.charAt(0).toUpperCase() + taskType.slice(1)}</InputLabel>
                        <Select
                          value={currentModel}
                          label={taskType.charAt(0).toUpperCase() + taskType.slice(1)}
                          onChange={(e) => handleModelChange(taskType, e.target.value)}
                        >
                          {modelConfig.available_models.map((model) => (
                            <MenuItem key={model} value={model}>
                              {model}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                        {TASK_DESCRIPTIONS[taskType] || ''}
                      </Typography>
                    </Grid>
                  ))}
                  <Grid item xs={12}>
                    <Button
                      variant="contained"
                      onClick={handleSaveConfig}
                      disabled={configSaving}
                      sx={{ mt: 1 }}
                    >
                      {configSaving ? 'Saving...' : 'Save Configuration'}
                    </Button>
                  </Grid>
                </Grid>
              ) : null}

              {configError && (
                <Alert severity="error" sx={{ mt: 2 }}>
                  {configError}
                </Alert>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Snackbar
        open={configSuccess}
        autoHideDuration={3000}
        onClose={() => setConfigSuccess(false)}
      >
        <Alert onClose={() => setConfigSuccess(false)} severity="success" variant="filled">
          Model configuration saved
        </Alert>
      </Snackbar>
    </Box>
  )
}
