import axios from 'axios'

const ADMIN_TOKEN_KEY = 'sage-admin-token'
const SESSION_ID_KEY = 'sage-session-id'

function getSessionId(): string {
  const existing = sessionStorage.getItem(SESSION_ID_KEY)
  if (existing) return existing
  const generated = crypto.randomUUID().replace(/[^A-Za-z0-9_-]/g, '')
  sessionStorage.setItem(SESSION_ID_KEY, generated)
  return generated
}

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem(ADMIN_TOKEN_KEY)
  config.headers.set('X-Sage-Session-ID', getSessionId())
  if (token) config.headers.set('X-Sage-Admin-Token', token)
  return config
})

api.interceptors.response.use(
  response => response,
  error => {
    const message = error?.response?.data?.detail?.message
    return Promise.reject(new Error(message || error?.message || 'Sage API request failed'))
  },
)

export const setAdminToken = (token: string) =>
  sessionStorage.setItem(ADMIN_TOKEN_KEY, token)

export const clearAdminToken = () =>
  sessionStorage.removeItem(ADMIN_TOKEN_KEY)

export const hasAdminToken = () =>
  Boolean(sessionStorage.getItem(ADMIN_TOKEN_KEY))

// Execution modes:
// "offline" - heuristic model, in-memory sandbox, nothing leaves machine
// "qwen"   - Qwen LLM for reasoning, in-memory sandbox (no real cloud)
// "cloud"  - Qwen LLM + real Alibaba Cloud MCP (actual deployments)
export type ExecutionMode = 'offline' | 'qwen' | 'cloud'

export interface StatusResponse {
  mode: ExecutionMode
  rules_learned: number
  total_tasks: number
  successes: number
  failures: number
  corrections: number
  corrected_failures: number
  success_rate: number
}

export interface TaskRequest {
  task: string
  mode?: ExecutionMode
}

export interface CorrectionRequest {
  task: string
  action_taken: string
  error: string
  fix: string
  mode?: ExecutionMode
}

export interface Rule {
  id: string
  text: string
  confidence: number
  utility: number
  times_applied: number
  source: string
  pinned: boolean
  created_at?: string
}

export interface Skill {
  id: string
  name: string
  steps: Array<{ step: string; tool: string; args: Record<string, unknown> }>
  times_used: number
  success_rate: number
}

export interface Episode {
  id: string
  task: string
  outcome: string
  timestamp: string
}

export interface Case {
  id: string
  task: string
  outcome: string
  duration_ms: number
  timestamp: string
}

export const getStatus = (mode: ExecutionMode = 'offline') =>
  api.get<StatusResponse>('/status', { params: { mode } }).then(r => r.data)

export interface TaskJob {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  result: any
  error: string | null
}

export const submitTaskJob = (task: string, mode: ExecutionMode = 'offline') =>
  api.post<TaskJob>(
    '/jobs/task',
    { task, mode },
    { headers: { 'Idempotency-Key': crypto.randomUUID() } },
  ).then(r => r.data)

export const getTaskJob = (jobId: string) =>
  api.get<TaskJob>(`/jobs/${jobId}`).then(r => r.data)

export const cancelTaskJob = (jobId: string) =>
  api.delete<TaskJob>(`/jobs/${jobId}`).then(r => r.data)

export const executeTaskImmediate = (task: string, mode: ExecutionMode = 'offline') =>
  api.post('/task', { task, mode }).then(r => r.data)

export const executeTask = async (
  task: string,
  mode: ExecutionMode = 'offline',
  onStarted?: (jobId: string) => void,
) => {
  const submitted = await submitTaskJob(task, mode)
  onStarted?.(submitted.job_id)
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    const job = await getTaskJob(submitted.job_id)
    if (job.status === 'succeeded') return job.result
    if (job.status === 'failed') throw new Error(job.error || 'Run failed')
    if (job.status === 'cancelled') throw new Error('Run cancelled')
    await new Promise(resolve => window.setTimeout(resolve, 750))
  }
  throw new Error('Run is still active; check its status before retrying')
}

export const handleCorrection = (data: CorrectionRequest) =>
  api.post('/correction', data).then(r => r.data)

export const rerunTaskImmediate = (task: string, mode: ExecutionMode = 'offline') =>
  api.post('/task/rerun', { task, mode }).then(r => r.data)

export const rerunTask = (task: string, mode: ExecutionMode = 'offline') =>
  executeTask(task, mode)

export const getMemory = (mode: ExecutionMode = 'offline') =>
  api.get('/memory', { params: { mode } }).then(r => r.data)

export const getRules = (mode: ExecutionMode = 'offline') =>
  api.get('/memory/rules', { params: { mode } }).then(r => r.data)

export const pinRule = (ruleId: string, mode: ExecutionMode = 'offline') =>
  api.post('/memory/rules/pin', { rule_id: ruleId, mode }).then(r => r.data)

export const retireRule = (ruleId: string, mode: ExecutionMode = 'offline') =>
  api.post('/memory/rules/retire', { rule_id: ruleId, mode }).then(r => r.data)

export const editRule = (
  ruleId: string,
  updates: { text?: string; confidence?: number },
  mode: ExecutionMode = 'offline',
) => api.put(`/memory/rules/${ruleId}`, { rule_id: ruleId, ...updates, mode }).then(r => r.data)

export const getSkills = (mode: ExecutionMode = 'offline') =>
  api.get('/memory/skills', { params: { mode } }).then(r => r.data)

export const getEpisodes = (mode: ExecutionMode = 'offline') =>
  api.get('/memory/episodes', { params: { mode } }).then(r => r.data)

export const getCases = (mode: ExecutionMode = 'offline') =>
  api.get('/memory/cases', { params: { mode } }).then(r => r.data)

export const getProvenance = (mode: ExecutionMode = 'offline') =>
  api.get('/memory/provenance', { params: { mode } }).then(r => r.data)

export const getLifecycle = (mode: ExecutionMode = 'offline') =>
  api.get('/memory/lifecycle', { params: { mode } }).then(r => r.data)

export const runMaintenance = (mode: ExecutionMode = 'offline') =>
  api.post('/memory/maintenance', null, { params: { mode } }).then(r => r.data)

export const refreshIndex = (mode: ExecutionMode = 'offline') =>
  api.post('/memory/refresh-index', null, { params: { mode } }).then(r => r.data)

export const getMetrics = (mode: ExecutionMode = 'offline') =>
  api.get('/metrics', { params: { mode } }).then(r => r.data)

export const getMetricsHistory = (mode: ExecutionMode = 'offline') =>
  api.get('/metrics/history', { params: { mode } }).then(r => r.data)

export const runCounterfactual = (task: string, mode: ExecutionMode = 'offline') =>
  api.post('/counterfactual', { task, mode }).then(r => r.data)

export const getCounterfactualHistory = (mode: ExecutionMode = 'offline') =>
  api.get('/counterfactual/history', { params: { mode } }).then(r => r.data)

export const runBenchmark = (mode: ExecutionMode = 'offline') =>
  api.post('/benchmark', null, { params: { mode } }).then(r => r.data)

export const runDemo = (mode: ExecutionMode = 'offline') =>
  api.post('/demo', null, { params: { mode } }).then(r => r.data)

export const getPreferences = (mode: ExecutionMode = 'offline') =>
  api.get('/preferences', { params: { mode } }).then(r => r.data)

export const setPreference = (category: string, key: string, value: string, mode: ExecutionMode = 'offline') =>
  api.post('/preferences', { category, key, value, mode }).then(r => r.data)

export const getSessions = (mode: ExecutionMode = 'offline') =>
  api.get('/sessions', { params: { mode } }).then(r => r.data)

export const getDashboard = (mode: ExecutionMode = 'offline') =>
  api.get('/dashboard', { params: { mode } }).then(r => r.data)

export interface CredentialStatus {
  live_enabled: boolean
  cloud_mutations_enabled: boolean
  qwen_key_configured: boolean
  has_credentials: boolean
  region: string
}

export const setCredentials = (access_key_id: string, access_key_secret: string, region?: string) =>
  api.post('/credentials', { access_key_id, access_key_secret, region }).then(r => r.data)

export const getCredentialsStatus = () =>
  api.get<CredentialStatus>('/credentials/status').then(r => r.data)

export const clearCredentials = () =>
  api.delete('/credentials').then(r => r.data)

export interface ModelConfig {
  config: {
    execution: string
    reflection: string
    planning: string
  }
  available_models: string[]
}

export const getModelConfig = () =>
  api.get<ModelConfig>('/models').then(r => r.data)

export const setModelConfig = (updates: Partial<{ execution: string; reflection: string; planning: string }>) =>
  api.put<ModelConfig>('/models', updates).then(r => r.data)

export default api
