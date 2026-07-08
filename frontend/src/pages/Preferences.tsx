import { useEffect, useState } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Grid,
  TextField,
  Button,
  Alert,
  Skeleton,
  Divider,
  Chip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import AddIcon from '@mui/icons-material/Add'
import { type ExecutionMode, getPreferences, setPreference } from '../api/client'

interface PreferencesProps {
  mode: ExecutionMode
}

export default function Preferences({ mode }: PreferencesProps) {
  const [preferences, setPreferences] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const [category, setCategory] = useState('deployment')
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')

  useEffect(() => {
    setLoading(true)
    getPreferences(mode)
      .then((res) => setPreferences(res.preferences || {}))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [mode])

  const handleSetPreference = async () => {
    if (!category || !key || !value) return
    try {
      await setPreference(category, key, value, mode)
      setSuccess(`Preference set: ${category}.${key} = ${value}`)
      const res = await getPreferences(mode)
      setPreferences(res.preferences || {})
      setKey('')
      setValue('')
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed'
      setError(message)
    }
  }

  if (loading) {
    return (
      <Box>
        <Typography variant="h4" gutterBottom>Preferences</Typography>
        <Skeleton variant="rounded" height={400} />
      </Box>
    )
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom sx={{ fontWeight: 500 }}>
        Preferences
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
        Configure durable settings that influence Sage's behavior
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

      <Grid container spacing={3}>
        {Object.entries(preferences).length > 0 &&
          Object.entries(preferences).map(([cat, prefs]) => (
            <Grid item xs={12} md={6} key={cat}>
              <Card>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    {cat}
                  </Typography>
                  {typeof prefs === 'object' && prefs !== null ? (
                    Object.entries(prefs as Record<string, unknown>).map(([k, v]) => (
                      <Box key={k} sx={{ display: 'flex', justifyContent: 'space-between', py: 1, borderBottom: '1px solid #f0f0f0' }}>
                        <Typography variant="body2" color="text.secondary">{k}</Typography>
                        <Chip label={String(v)} size="small" variant="outlined" />
                      </Box>
                    ))
                  ) : (
                    <Typography variant="body2">{String(prefs)}</Typography>
                  )}
                </CardContent>
              </Card>
            </Grid>
          ))}

        {Object.keys(preferences).length === 0 && (
          <Grid item xs={12}>
            <Card>
              <CardContent sx={{ textAlign: 'center', py: 4 }}>
                <Typography color="text.secondary">No preferences set yet</Typography>
              </CardContent>
            </Card>
          </Grid>
        )}
      </Grid>

      <Divider sx={{ my: 4 }} />

      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Set Preference
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Category</InputLabel>
              <Select
                value={category}
                label="Category"
                onChange={(e) => setCategory(e.target.value)}
              >
                <MenuItem value="deployment">Deployment</MenuItem>
                <MenuItem value="infrastructure">Infrastructure</MenuItem>
                <MenuItem value="security">Security</MenuItem>
                <MenuItem value="performance">Performance</MenuItem>
              </Select>
            </FormControl>
            <TextField
              label="Key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              size="small"
              sx={{ minWidth: 160 }}
            />
            <TextField
              label="Value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              size="small"
              sx={{ minWidth: 200 }}
            />
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={handleSetPreference}
              disabled={!category || !key || !value}
            >
              Set
            </Button>
          </Box>
        </CardContent>
      </Card>

      {Object.keys(preferences).length > 0 && (
        <>
          <Divider sx={{ my: 4 }} />
          <Accordion variant="outlined">
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>View Raw Preferences</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <pre style={{ fontSize: '0.75rem', overflow: 'auto' }}>
                {JSON.stringify(preferences, null, 2)}
              </pre>
            </AccordionDetails>
          </Accordion>
        </>
      )}
    </Box>
  )
}
