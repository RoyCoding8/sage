import { createTheme } from '@mui/material/styles'

export const createAppTheme = (mode: 'light' | 'dark') => createTheme({
  palette: {
    mode,
    primary: {
      main: '#059669',
      light: '#10b981',
      dark: '#047857',
      contrastText: '#ffffff',
    },
    secondary: {
      main: '#475569',
      light: '#64748b',
      dark: '#334155',
      contrastText: '#ffffff',
    },
    error: {
      main: '#dc2626',
      light: '#ef4444',
      dark: '#b91c1c',
    },
    warning: {
      main: '#d97706',
      light: '#f59e0b',
      dark: '#b45309',
    },
    success: {
      main: '#059669',
      light: '#10b981',
      dark: '#047857',
    },
    info: {
      main: '#0284c7',
      light: '#0ea5e9',
      dark: '#0369a1',
    },
    background: {
      default: mode === 'light' ? '#f8fafc' : '#0f172a',
      paper: mode === 'light' ? '#ffffff' : '#1e293b',
    },
    text: {
      primary: mode === 'light' ? '#0f172a' : '#f1f5f9',
      secondary: mode === 'light' ? '#64748b' : '#94a3b8',
    },
    divider: mode === 'light' ? '#e2e8f0' : '#334155',
  },
  typography: {
    fontFamily: '"Outfit", "system-ui", "-apple-system", "Segoe UI", "Roboto", sans-serif',
    h1: {
      fontSize: '2.25rem',
      fontWeight: 600,
      letterSpacing: '-0.025em',
      lineHeight: 1.2,
    },
    h2: {
      fontSize: '1.875rem',
      fontWeight: 600,
      letterSpacing: '-0.02em',
      lineHeight: 1.3,
    },
    h3: {
      fontSize: '1.5rem',
      fontWeight: 600,
      letterSpacing: '-0.015em',
      lineHeight: 1.35,
    },
    h4: {
      fontSize: '1.25rem',
      fontWeight: 600,
      letterSpacing: '-0.01em',
      lineHeight: 1.4,
    },
    h5: {
      fontSize: '1.125rem',
      fontWeight: 600,
      lineHeight: 1.45,
    },
    h6: {
      fontSize: '1rem',
      fontWeight: 600,
      lineHeight: 1.5,
    },
    body1: {
      fontSize: '0.875rem',
      lineHeight: 1.6,
    },
    body2: {
      fontSize: '0.8125rem',
      lineHeight: 1.5,
    },
    button: {
      textTransform: 'none' as const,
      fontWeight: 500,
      fontSize: '0.875rem',
    },
    subtitle1: {
      fontSize: '0.9375rem',
      fontWeight: 500,
      lineHeight: 1.5,
      color: mode === 'light' ? '#64748b' : '#94a3b8',
    },
    subtitle2: {
      fontSize: '0.8125rem',
      fontWeight: 500,
      lineHeight: 1.5,
      color: mode === 'light' ? '#64748b' : '#94a3b8',
    },
  },
  shape: {
    borderRadius: 8,
  },
  shadows: [
    'none',
    mode === 'light' ? '0 1px 2px 0 rgba(15, 23, 42, 0.05)' : '0 1px 2px 0 rgba(0, 0, 0, 0.3)',
    mode === 'light' ? '0 1px 3px 0 rgba(15, 23, 42, 0.08), 0 1px 2px -1px rgba(15, 23, 42, 0.05)' : '0 1px 3px 0 rgba(0, 0, 0, 0.4), 0 1px 2px -1px rgba(0, 0, 0, 0.3)',
    mode === 'light' ? '0 4px 6px -1px rgba(15, 23, 42, 0.08), 0 2px 4px -2px rgba(15, 23, 42, 0.05)' : '0 4px 6px -1px rgba(0, 0, 0, 0.4), 0 2px 4px -2px rgba(0, 0, 0, 0.3)',
    mode === 'light' ? '0 10px 15px -3px rgba(15, 23, 42, 0.08), 0 4px 6px -4px rgba(15, 23, 42, 0.05)' : '0 10px 15px -3px rgba(0, 0, 0, 0.4), 0 4px 6px -4px rgba(0, 0, 0, 0.3)',
    mode === 'light' ? '0 20px 25px -5px rgba(15, 23, 42, 0.1), 0 8px 10px -6px rgba(15, 23, 42, 0.05)' : '0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.4)',
    mode === 'light' ? '0 25px 50px -12px rgba(15, 23, 42, 0.2)' : '0 25px 50px -12px rgba(0, 0, 0, 0.6)',
    'none', 'none', 'none', 'none', 'none', 'none', 'none', 'none',
    'none', 'none', 'none', 'none', 'none', 'none', 'none', 'none',
    'none', 'none',
  ],
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          padding: '8px 20px',
          fontWeight: 500,
          fontSize: '0.875rem',
          textTransform: 'none' as const,
          transition: 'all 0.15s ease',
        },
        contained: {
          boxShadow: mode === 'light' ? '0 1px 2px 0 rgba(15, 23, 42, 0.05)' : '0 1px 2px 0 rgba(0, 0, 0, 0.3)',
          '&:hover': {
            boxShadow: mode === 'light' ? '0 1px 3px 0 rgba(15, 23, 42, 0.1)' : '0 1px 3px 0 rgba(0, 0, 0, 0.4)',
          },
        },
        outlined: {
          borderWidth: 1.5,
          '&:hover': {
            borderWidth: 1.5,
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          border: `1px solid ${mode === 'light' ? '#e2e8f0' : '#334155'}`,
          boxShadow: mode === 'light' ? '0 1px 3px 0 rgba(15, 23, 42, 0.05)' : '0 1px 3px 0 rgba(0, 0, 0, 0.3)',
          transition: 'box-shadow 0.15s ease',
          '&:hover': {
            boxShadow: mode === 'light' ? '0 4px 6px -1px rgba(15, 23, 42, 0.08)' : '0 4px 6px -1px rgba(0, 0, 0, 0.4)',
          },
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          borderRadius: 12,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 6,
          fontWeight: 500,
          fontSize: '0.75rem',
        },
      },
    },
    MuiTextField: {
      defaultProps: {
        variant: 'outlined',
        size: 'small',
      },
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 8,
            transition: 'border-color 0.15s ease',
          },
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          borderRight: `1px solid ${mode === 'light' ? '#e2e8f0' : '#334155'}`,
          backgroundColor: mode === 'light' ? '#ffffff' : '#1e293b',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          boxShadow: mode === 'light' ? '0 1px 3px 0 rgba(15, 23, 42, 0.05)' : '0 1px 3px 0 rgba(0, 0, 0, 0.3)',
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderBottom: `1px solid ${mode === 'light' ? '#e2e8f0' : '#334155'}`,
          padding: '12px 16px',
        },
        head: {
          fontWeight: 600,
          fontSize: '0.75rem',
          textTransform: 'uppercase' as const,
          letterSpacing: '0.05em',
          color: mode === 'light' ? '#64748b' : '#94a3b8',
          backgroundColor: mode === 'light' ? '#f8fafc' : '#0f172a',
        },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          transition: 'background-color 0.1s ease',
          '&:hover': {
            backgroundColor: mode === 'light' ? '#f8fafc' : '#334155',
          },
        },
      },
    },
    MuiAccordion: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          border: `1px solid ${mode === 'light' ? '#e2e8f0' : '#334155'}`,
          boxShadow: 'none',
          '&:before': {
            display: 'none',
          },
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: {
          borderRadius: 4,
          height: 6,
          backgroundColor: mode === 'light' ? '#e2e8f0' : '#334155',
        },
        bar: {
          borderRadius: 4,
        },
      },
    },
    MuiSwitch: {
      styleOverrides: {
        root: {
          padding: 8,
        },
        switchBase: {
          padding: 1,
        },
        thumb: {
          width: 18,
          height: 18,
        },
        track: {
          borderRadius: 10,
          opacity: 1,
          backgroundColor: mode === 'light' ? '#cbd5e1' : '#475569',
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          textTransform: 'none' as const,
          fontWeight: 500,
          fontSize: '0.875rem',
          minHeight: 44,
        },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: {
          height: 2,
          borderRadius: 1,
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          '&.Mui-selected': {
            backgroundColor: mode === 'light' ? '#ecfdf5' : '#064e3b',
            color: mode === 'light' ? '#059669' : '#10b981',
            '&:hover': {
              backgroundColor: mode === 'light' ? '#d1fae5' : '#065f46',
            },
            '& .MuiListItemIcon-root': {
              color: mode === 'light' ? '#059669' : '#10b981',
            },
          },
          '&:hover': {
            backgroundColor: mode === 'light' ? '#f1f5f9' : '#334155',
          },
        },
      },
    },
  },
})

export default createAppTheme('light')
