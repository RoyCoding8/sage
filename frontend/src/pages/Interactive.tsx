import { useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  TextField,
  Button,
  Typography,
  Alert,
  CircularProgress,
  Divider,
  Chip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material'
import SendIcon from '@mui/icons-material/Send'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import {
  type ExecutionMode,
  cancelTaskJob,
  executeTask,
  handleCorrection as submitCorrection,
  rerunTask,
} from '../api/client'

interface InteractiveProps {
  mode: ExecutionMode
}

interface TaskResult {
  success?: boolean
  outcome?: string
  task?: string
  response?: string
  observation?: string
  duration_ms?: number
  rules_applied?: string[]
  error?: string
  steps?: Array<{ step: string; result: string; tool?: string; thought?: string }>
  correction_needed?: boolean
}

interface CorrectionResult {
  rule?: string
  rule_id?: string
  confidence?: number
}

export default function Interactive({ mode }: InteractiveProps) {
  const [task, setTask] = useState('')
  const [taskResult, setTaskResult] = useState<TaskResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [correctionAction, setCorrectionAction] = useState('')
  const [correctionError, setCorrectionError] = useState('')
  const [correctionFix, setCorrectionFix] = useState('')
  const [correctionResult, setCorrectionResult] = useState<CorrectionResult | null>(null)
  const [correcting, setCorrecting] = useState(false)

  const [rerunResult, setRerunResult] = useState<TaskResult | null>(null)
  const [rerunning, setRerunning] = useState(false)

  const handleExecuteTask = async () => {
    if (!task.trim()) return
    setLoading(true)
    setError(null)
    setTaskResult(null)
    setCorrectionResult(null)
    setRerunResult(null)
    try {
      const result = await executeTask(task, mode, setActiveJobId)
      setTaskResult(result)
      if (result.rules_applied) {
        setCorrectionAction(result.task || task)
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to execute task'
      setError(message)
    } finally {
      setLoading(false)
      setActiveJobId(null)
    }
  }

  const handleCancelTask = async () => {
    if (!activeJobId) return
    await cancelTaskJob(activeJobId)
  }

  const handleCorrectionSubmit = async () => {
    if (!task || !correctionAction || !correctionError || !correctionFix) return
    setCorrecting(true)
    setError(null)
    try {
      const result = await submitCorrection({
        task,
        action_taken: correctionAction,
        error: correctionError,
        fix: correctionFix,
        mode,
      })
      setCorrectionResult(result)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to submit correction'
      setError(message)
    } finally {
      setCorrecting(false)
    }
  }

  const handleRerun = async () => {
    if (!task) return
    setRerunning(true)
    setError(null)
    try {
      const result = await rerunTask(task, mode)
      setRerunResult(result)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to rerun task'
      setError(message)
    } finally {
      setRerunning(false)
    }
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Interactive Mode
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Execute deployment tasks and teach Sage through corrections
      </Typography>

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Execute Task
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
            <TextField
              fullWidth
              multiline
              rows={2}
              placeholder="Describe a deployment task (e.g., 'Deploy a Node.js app on port 3000')"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              disabled={loading}
            />
            <Button
              variant="contained"
              onClick={handleExecuteTask}
              disabled={loading || !task.trim()}
              startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <SendIcon />}
              sx={{ height: 56 }}
            >
              Run
            </Button>
            {loading && activeJobId && (
              <Button color="error" variant="outlined" onClick={handleCancelTask} sx={{ height: 56 }}>
                Cancel
              </Button>
            )}
          </Box>
        </CardContent>
      </Card>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {taskResult && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Task Result
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
              <Chip
                label={taskResult.outcome === 'success' || taskResult.success ? 'Success' : 'Failed'}
                color={taskResult.outcome === 'success' || taskResult.success ? 'success' : 'error'}
              />
              {taskResult.duration_ms && (
                <Chip
                  label={`${(taskResult.duration_ms / 1000).toFixed(2)}s`}
                  variant="outlined"
                />
              )}
            </Box>
            {taskResult.response && (
              <Typography variant="body2" sx={{ mb: 2 }}>
                <strong>Response:</strong> {taskResult.response}
              </Typography>
            )}
            {taskResult.observation && (
              <Typography variant="body2" sx={{ mb: 2 }}>
                <strong>Observation:</strong> {taskResult.observation}
              </Typography>
            )}
            {taskResult.error && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {taskResult.error}
              </Alert>
            )}
            {taskResult.rules_applied && taskResult.rules_applied.length > 0 && (
              <Box>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  Rules Applied:
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  {taskResult.rules_applied.map((rule, idx) => (
                    <Chip key={idx} label={rule} size="small" variant="outlined" />
                  ))}
                </Box>
              </Box>
            )}

            {taskResult.steps && taskResult.steps.length > 0 && (
              <Accordion sx={{ mt: 2 }}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography>Steps ({taskResult.steps.length})</Typography>
                </AccordionSummary>
                <AccordionDetails>
                  {taskResult.steps.map((step, idx) => (
                    <Box key={idx} sx={{ mb: 1, pb: 1, borderBottom: idx < taskResult.steps!.length - 1 ? '1px solid' : 'none', borderColor: 'divider' }}>
                      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                        <Chip label={step.result} size="small" color={step.result === 'success' ? 'success' : 'error'} />
                        <Typography variant="body2" fontWeight={500}>{step.step}</Typography>
                        {step.tool && <Chip label={step.tool} size="small" variant="outlined" />}
                      </Box>
                      {step.thought && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                          {step.thought}
                        </Typography>
                      )}
                    </Box>
                  ))}
                </AccordionDetails>
              </Accordion>
            )}

            <Accordion sx={{ mt: 2 }}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography>View Raw Result</Typography>
              </AccordionSummary>
              <AccordionDetails>
                <pre style={{ fontSize: '0.75rem', overflow: 'auto' }}>
                  {JSON.stringify(taskResult, null, 2)}
                </pre>
              </AccordionDetails>
            </Accordion>
          </CardContent>
        </Card>
      )}

      {taskResult && taskResult.outcome !== 'success' && !taskResult.success && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Submit Correction
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Tell Sage what went wrong and how to fix it. This will create a new rule.
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="What action was taken?"
                value={correctionAction}
                onChange={(e) => setCorrectionAction(e.target.value)}
                fullWidth
              />
              <TextField
                label="What was the error?"
                value={correctionError}
                onChange={(e) => setCorrectionError(e.target.value)}
                fullWidth
              />
              <TextField
                label="How should it be fixed?"
                value={correctionFix}
                onChange={(e) => setCorrectionFix(e.target.value)}
                fullWidth
                multiline
                rows={2}
              />
              <Button
                variant="contained"
                color="secondary"
                onClick={handleCorrectionSubmit}
                disabled={correcting || !correctionAction || !correctionError || !correctionFix}
              >
                {correcting ? 'Submitting...' : 'Submit Correction'}
              </Button>
            </Box>

            {correctionResult && (
              <Alert severity="success" sx={{ mt: 2 }}>
                <Typography variant="body2">
                  <strong>Rule Created:</strong> {correctionResult.rule}
                </Typography>
                {correctionResult.confidence && (
                  <Typography variant="body2">
                    Confidence: {(correctionResult.confidence * 100).toFixed(0)}%
                  </Typography>
                )}
              </Alert>
            )}
          </CardContent>
        </Card>
      )}

      {correctionResult && (
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Rerun Corrected Task
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Run the task again to see if the new rule helps.
            </Typography>
            <Button
              variant="outlined"
              onClick={handleRerun}
              disabled={rerunning}
            >
              {rerunning ? 'Rerunning...' : 'Rerun Task'}
            </Button>

            {rerunResult && (
              <Box sx={{ mt: 2 }}>
                <Divider sx={{ my: 2 }} />
                <Typography variant="h6" gutterBottom>
                  Rerun Result
                </Typography>
                <Chip
                  label={rerunResult.outcome === 'success' || rerunResult.success ? 'Success' : 'Failed'}
                  color={rerunResult.outcome === 'success' || rerunResult.success ? 'success' : 'error'}
                />
                {rerunResult.observation && (
                  <Typography variant="body2" sx={{ mt: 1 }}>
                    {rerunResult.observation}
                  </Typography>
                )}
              </Box>
            )}
          </CardContent>
        </Card>
      )}
    </Box>
  )
}
