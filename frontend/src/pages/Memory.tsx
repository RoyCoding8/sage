import { useEffect, useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Tabs,
  Tab,
  Grid,
  Chip,
  IconButton,
  Button,
  TextField,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Alert,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  LinearProgress,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import PushPinIcon from '@mui/icons-material/PushPin'
import PushPinOutlinedIcon from '@mui/icons-material/PushPinOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import RefreshIcon from '@mui/icons-material/Refresh'
import BuildIcon from '@mui/icons-material/Build'
import {
  getRules,
  getSkills,
  getEpisodes,
  getCases,
  getProvenance,
  getLifecycle,
  pinRule,
  retireRule,
  runMaintenance,
  refreshIndex,
  type ExecutionMode,
} from '../api/client'

interface MemoryProps {
  mode: ExecutionMode
}

interface TabPanelProps {
  children?: React.ReactNode
  index: number
  value: number
}

function TabPanel({ children, value, index }: TabPanelProps) {
  return (
    <div role="tabpanel" hidden={value !== index}>
      {value === index && <Box sx={{ pt: 3 }}>{children}</Box>}
    </div>
  )
}

export default function Memory({ mode }: MemoryProps) {
  const [tab, setTab] = useState(0)
  const [rules, setRules] = useState<unknown[]>([])
  const [skills, setSkills] = useState<unknown[]>([])
  const [episodes, setEpisodes] = useState<unknown[]>([])
  const [cases, setCases] = useState<unknown[]>([])
  const [provenance, setProvenance] = useState<Record<string, unknown>>({})
  const [lifecycle, setLifecycle] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rulesFilter, setRulesFilter] = useState('All')
  const [rulesSort, setRulesSort] = useState('Confidence')
  const [rulesSearch, setRulesSearch] = useState('')

  useEffect(() => {
    setLoading(true)
    Promise.all([
      getRules(mode),
      getSkills(mode),
      getEpisodes(mode),
      getCases(mode),
      getProvenance(mode),
      getLifecycle(mode),
    ])
      .then(([r, s, e, c, p, l]) => {
        setRules(r.rules || [])
        setSkills(s.skills || [])
        setEpisodes(e.episodes || [])
        setCases(c.cases || [])
        setProvenance(p.provenance || {})
        setLifecycle(l.lifecycle || {})
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [mode])

  const handlePin = async (ruleId: string) => {
    await pinRule(ruleId, mode)
    const updated = await getRules(mode)
    setRules(updated.rules || [])
  }

  const handleRetire = async (ruleId: string) => {
    await retireRule(ruleId, mode)
    const updated = await getRules(mode)
    setRules(updated.rules || [])
  }

  const handleMaintenance = async () => {
    await runMaintenance(mode)
    const updated = await getLifecycle(mode)
    setLifecycle(updated.lifecycle || {})
  }

  const handleRefreshIndex = async () => {
    await refreshIndex(mode)
  }

  const filteredRules = (rules as Array<Record<string, unknown>>)
    .filter((r) => {
      if (rulesFilter === 'Active') return r.status !== 'retired'
      if (rulesFilter === 'Pinned') return r.pinned
      if (rulesFilter === 'Retired') return r.status === 'retired'
      return true
    })
    .filter((r) => {
      if (!rulesSearch) return true
      return String(r.text || '').toLowerCase().includes(rulesSearch.toLowerCase())
    })
    .sort((a, b) => {
      if (rulesSort === 'Confidence') return (b.confidence as number) - (a.confidence as number)
      if (rulesSort === 'Utility') return (b.utility as number) - (a.utility as number)
      if (rulesSort === 'Times Applied') return (b.times_applied as number) - (a.times_applied as number)
      return 0
    })

  if (loading) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>Memory</Typography>
        <Skeleton variant="rounded" height={400} />
      </Box>
    )
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 500 }}>
            Memory
          </Typography>
          <Typography variant="body1" color="text.secondary">
            Inspect and manage Sage's learned knowledge
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            startIcon={<RefreshIcon />}
            variant="outlined"
            onClick={handleRefreshIndex}
            size="small"
          >
            Refresh Index
          </Button>
          <Button
            startIcon={<BuildIcon />}
            variant="outlined"
            onClick={handleMaintenance}
            size="small"
          >
            Maintenance
          </Button>
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center', py: 2 }}>
              <Typography variant="h4" color="primary.main">{rules.length}</Typography>
              <Typography variant="body2" color="text.secondary">Rules</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center', py: 2 }}>
              <Typography variant="h4">{cases.length}</Typography>
              <Typography variant="body2" color="text.secondary">Cases</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center', py: 2 }}>
              <Typography variant="h4">{episodes.length}</Typography>
              <Typography variant="body2" color="text.secondary">Episodes</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center', py: 2 }}>
              <Typography variant="h4" color="secondary.main">{skills.length}</Typography>
              <Typography variant="body2" color="text.secondary">Skills</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Card>
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ borderBottom: 1, borderColor: 'divider', px: 2 }}
        >
          <Tab label="Rules" />
          <Tab label="Cases" />
          <Tab label="Episodes" />
          <Tab label="Skills" />
          <Tab label="Provenance" />
          <Tab label="Lifecycle" />
        </Tabs>

        <CardContent>
          <TabPanel value={tab} index={0}>
            <Box sx={{ display: 'flex', gap: 2, mb: 3, flexWrap: 'wrap' }}>
              <TextField
                placeholder="Search rules..."
                value={rulesSearch}
                onChange={(e) => setRulesSearch(e.target.value)}
                size="small"
                sx={{ minWidth: 200 }}
              />
              <FormControl size="small" sx={{ minWidth: 120 }}>
                <InputLabel>Filter</InputLabel>
                <Select
                  value={rulesFilter}
                  label="Filter"
                  onChange={(e) => setRulesFilter(e.target.value)}
                >
                  <MenuItem value="All">All</MenuItem>
                  <MenuItem value="Active">Active</MenuItem>
                  <MenuItem value="Pinned">Pinned</MenuItem>
                  <MenuItem value="Retired">Retired</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>Sort by</InputLabel>
                <Select
                  value={rulesSort}
                  label="Sort by"
                  onChange={(e) => setRulesSort(e.target.value)}
                >
                  <MenuItem value="Confidence">Confidence</MenuItem>
                  <MenuItem value="Utility">Utility</MenuItem>
                  <MenuItem value="Times Applied">Times Applied</MenuItem>
                </Select>
              </FormControl>
            </Box>

            {filteredRules.length === 0 ? (
              <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                No rules found
              </Typography>
            ) : (
              filteredRules.map((rule, idx) => (
                <Card key={String(rule.id || idx)} variant="outlined" sx={{ mb: 2 }}>
                  <CardContent>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Chip
                          label={String(rule.id || `rule_${idx}`)}
                          size="small"
                          color="primary"
                          variant="outlined"
                          sx={{ fontFamily: 'monospace' }}
                        />
                        {rule.pinned ? <Chip label="Pinned" size="small" color="warning" /> : null}
                        {rule.status === 'retired' ? <Chip label="Retired" size="small" color="error" /> : null}
                      </Box>
                      <Typography variant="body2" fontWeight={600}>
                        {(rule.confidence as number)?.toFixed(2)}
                      </Typography>
                    </Box>
                    <Typography variant="body1" sx={{ mb: 1.5 }}>
                      {String(rule.text || '')}
                    </Typography>
                    <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
                      <Chip label={`Applied ${rule.times_applied || 0}x`} size="small" variant="outlined" />
                      <Chip label={`Utility: ${rule.utility ? (rule.utility as number).toFixed(2) : '0.00'}`} size="small" variant="outlined" />
                      <Chip label={`Source: ${rule.source || 'unknown'}`} size="small" variant="outlined" />
                      <Box sx={{ flexGrow: 1 }} />
                      <IconButton
                        size="small"
                        onClick={() => handlePin(String(rule.id))}
                        color={rule.pinned ? 'warning' : 'default'}
                      >
                        {rule.pinned ? <PushPinIcon /> : <PushPinOutlinedIcon />}
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={() => handleRetire(String(rule.id))}
                        color="error"
                      >
                        <DeleteOutlineIcon />
                      </IconButton>
                    </Box>
                  </CardContent>
                </Card>
              ))
            )}
          </TabPanel>

          <TabPanel value={tab} index={1}>
            {cases.length === 0 ? (
              <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                No cases recorded
              </Typography>
            ) : (
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Task</TableCell>
                      <TableCell>Outcome</TableCell>
                      <TableCell>Duration</TableCell>
                      <TableCell>Timestamp</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(cases as Array<Record<string, unknown>>).map((c, idx) => (
                      <TableRow key={idx}>
                        <TableCell>{String(c.task || '')}</TableCell>
                        <TableCell>
                          <Chip
                            label={String(c.outcome || '')}
                            size="small"
                            color={c.outcome === 'success' ? 'success' : 'error'}
                          />
                        </TableCell>
                        <TableCell>{c.duration_ms ? `${((c.duration_ms as number) / 1000).toFixed(2)}s` : '-'}</TableCell>
                        <TableCell>{String(c.timestamp || '').slice(0, 16)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </TabPanel>

          <TabPanel value={tab} index={2}>
            {episodes.length === 0 ? (
              <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                No episodes recorded
              </Typography>
            ) : (
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Task</TableCell>
                      <TableCell>Outcome</TableCell>
                      <TableCell>Timestamp</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(episodes as Array<Record<string, unknown>>).map((e, idx) => (
                      <TableRow key={idx}>
                        <TableCell>{String(e.task || '')}</TableCell>
                        <TableCell>
                          <Chip
                            label={String(e.outcome || '')}
                            size="small"
                            color={e.outcome === 'success' ? 'success' : 'error'}
                          />
                        </TableCell>
                        <TableCell>{String(e.timestamp || '').slice(0, 16)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </TabPanel>

          <TabPanel value={tab} index={3}>
            {skills.length === 0 ? (
              <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                No skills promoted yet
              </Typography>
            ) : (
              (skills as Array<Record<string, unknown>>).map((skill, idx) => (
                <Card key={idx} variant="outlined" sx={{ mb: 2 }}>
                  <CardContent>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                      <Typography variant="h6">{String(skill.name || 'Unnamed')}</Typography>
                      <Chip label={`Used ${skill.times_used || 0}x`} size="small" />
                    </Box>
                    {Array.isArray(skill.steps) && (skill.steps as Array<Record<string, unknown>>).length > 0 && (
                      <Accordion>
                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                          <Typography variant="body2">View steps ({(skill.steps as unknown[]).length})</Typography>
                        </AccordionSummary>
                        <AccordionDetails>
                          {(skill.steps as Array<Record<string, unknown>>).map((step, sIdx) => (
                            <Typography key={sIdx} variant="body2" sx={{ mb: 0.5 }}>
                              {sIdx + 1}. <strong>{String(step.step || '')}</strong>: {String(step.tool || '')}
                            </Typography>
                          ))}
                        </AccordionDetails>
                      </Accordion>
                    )}
                  </CardContent>
                </Card>
              ))
            )}
          </TabPanel>

          <TabPanel value={tab} index={4}>
            {Object.keys(provenance).length === 0 ? (
              <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                No provenance data
              </Typography>
            ) : (
              <pre style={{ fontSize: '0.75rem', overflow: 'auto', maxHeight: 500 }}>
                {JSON.stringify(provenance, null, 2)}
              </pre>
            )}
          </TabPanel>

          <TabPanel value={tab} index={5}>
            {Object.keys(lifecycle).length === 0 ? (
              <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                No lifecycle data
              </Typography>
            ) : (
              <Box>
                {Object.entries(lifecycle).map(([key, value]) => (
                  <Box key={key} sx={{ mb: 2 }}>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      {key}
                    </Typography>
                    {typeof value === 'number' ? (
                      <LinearProgress
                        variant="determinate"
                        value={Math.min(value as number, 100)}
                        sx={{ height: 8, borderRadius: 4 }}
                      />
                    ) : (
                      <Typography variant="body1">
                        {JSON.stringify(value)}
                      </Typography>
                    )}
                  </Box>
                ))}
              </Box>
            )}
          </TabPanel>
        </CardContent>
      </Card>
    </Box>
  )
}
