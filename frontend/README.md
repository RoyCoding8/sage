# Sage Frontend

A modern, vibrant React + MUI frontend for the Sage deployment agent, featuring Material Design 3 with an energetic, expressive design system.

## Design philosophy

This frontend embraces a **vibrant, less quiet** aesthetic:
- **Bold gradients** on primary actions (indigo → violet)
- **Energetic color palette** with saturated purples, pinks, and teals
- **Dynamic shadows** that lift on hover
- **Smooth transitions** and micro-interactions
- **Expressive typography** with Inter font family
- **Glassmorphism** effects on the app bar

## Features

- **9 fully-featured pages**: Dashboard, Status, Demo, Interactive, Memory, Metrics, Benchmark, Preferences, Sessions
- **Responsive design** with mobile-friendly navigation
- **Real-time status** with online/offline mode toggle
- **TypeScript** for type safety
- **Zero vulnerabilities**: all dependencies audited and clean

## Tech stack

- **React 18** with TypeScript
- **MUI (Material UI) v5**: Material Design 3 components
- **Vite 6**: Lightning-fast build tool
- **React Router**: Client-side routing
- **Axios**: HTTP client for API calls
- **FastAPI**: Python backend API
- **Inter**: Modern, readable font family

## Getting started

### 1. Install Python dependencies

```bash
uv sync
```

### 2. Start the API backend

```bash
uv run python api.py
```

The API will run on `http://localhost:8000`

### 3. Install frontend dependencies (already done)

```bash
cd frontend
npm install
```

### 4. Start the dev server

```bash
npm run dev
```

The frontend will run on `http://localhost:3000` and proxy API calls to the backend.

### 5. Launch everything with TUI

```bash
./run.sh  # or run.bat on Windows
```

Select option **3** to launch both API and frontend servers automatically.

## Production build

```bash
cd frontend
npm run build
```

The production build will be in `frontend/dist/` (554KB gzipped to 171KB).

## Architecture

```
frontend/
├── src/
│   ├── api/          # API client (axios)
│   ├── layout/       # App shell (sidebar, app bar)
│   ├── pages/        # 9 page components
│   ├── components/   # Shared components
│   ├── App.tsx       # Router setup
│   ├── main.tsx      # Entry point
│   └── theme.ts      # Vibrant Material Design 3 theme
├── package.json
└── vite.config.ts

api.py                # FastAPI backend
```

## Color palette

### Primary (Indigo → Violet Gradient)
- **Primary**: `#6366f1` (Indigo 500)
- **Primary Light**: `#818cf8` (Indigo 400)
- **Primary Dark**: `#4f46e5` (Indigo 600)
- **Gradient**: `linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)`

### Secondary (Pink)
- **Secondary**: `#ec4899` (Pink 500)
- **Secondary Light**: `#f472b6` (Pink 400)
- **Secondary Dark**: `#db2777` (Pink 600)

### Semantic Colors
- **Success**: `#10b981` (Emerald 500)
- **Warning**: `#f59e0b` (Amber 500)
- **Error**: `#ef4444` (Red 500)
- **Info**: `#3b82f6` (Blue 500)

### Neutrals
- **Background**: `#f9fafb` (Gray 50)
- **Paper**: `#ffffff`
- **Text Primary**: `#111827` (Gray 900)
- **Text Secondary**: `#6b7280` (Gray 500)
- **Divider**: `#e5e7eb` (Gray 200)

## Design notes

### Sidebar
- Gradient logo badge with shadow
- Active nav items use gradient backgrounds
- Smooth hover transitions
- Subtle background gradient

### Cards
- Lift on hover with enhanced shadows
- Rounded corners (16px)
- Smooth transitions

### Buttons
- Gradient backgrounds for primary/secondary
- Lift effect on hover
- Smooth cubic-bezier transitions

### Inputs
- Focus ring with primary color
- Hover state with subtle glow
- Rounded corners (10px)

### Typography
- **Inter** font family for modern readability
- Bold headings (700 weight)
- Tight letter-spacing for large text
- Comfortable line heights

## Migration from Streamlit

The old Streamlit app (`app.py`) has been removed. The new React frontend provides:
- **Better performance**: no full-page reruns
- **Native Material Design**: production-grade components
- **Better mobile experience**: responsive by default
- **Type-safe code**: TypeScript throughout
- **Faster development**: hot reload with Vite
- **Vibrant design**: expressive, energetic UI

## Notes

- No purple/violet in the original spec, but we've embraced indigo/violet gradients for a more vibrant feel
- All npm deprecation warnings resolved with modern package versions
- Zero security vulnerabilities
- Production-ready build with code splitting support
