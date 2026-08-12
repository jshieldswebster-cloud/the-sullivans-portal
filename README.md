# VV LUXE Studio

Local desktop application for ingesting venue photography, auto-categorizing by event type, and producing production-ready **9:16 reels**, **4:5 carousels**, and **localized luxury captions** — entirely on Apple Silicon with zero paid API costs.

## Architecture

```
vv-luxe-studio/
├── electron/              # Electron main process
├── frontend/              # React + Tailwind + Vite UI
├── backend/
│   ├── main.py            # FastAPI entry + model init
│   ├── config.py          # Categories, MPS device, prompts
│   ├── database.py        # SQLite persistence
│   ├── models/
│   │   ├── classifier.py  # CLIP / Florence-2 zero-shot tagging
│   │   ├── depth_engine.py# Depth Anything V2 + 2.5D parallax
│   │   ├── caption_engine.py # Ollama luxury captions
│   │   └── carousel_compositor.py # 4:5 branded slides
│   ├── scripts/
│   │   ├── classify_images.py
│   │   └── render_reel.py
│   ├── routers/           # REST API
│   └── services/          # Orchestration
├── assets/
│   ├── fonts/           # Brand .ttf / .otf
│   ├── logos/           # PNG logo overlay
│   └── audio/           # Ambient tracks for reels
├── uploads/             # Ingested photos
└── output/
    ├── videos/
    ├── carousels/
    └── captions/
```

## Prerequisites (macOS / Apple Silicon)

```bash
# Xcode CLI tools (for git, build tools)
xcode-select --install

# Homebrew stack
brew install python@3.11 node ffmpeg ollama

# Pull local LLM for captions
ollama serve   # run in a separate terminal
ollama pull llama3:8b
```

## Setup

```bash
cd ~/Projects/vv-luxe-studio

# Python backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Node / Electron / React
npm install
```

Drop brand assets into:
- `assets/fonts/` — title card typography
- `assets/logos/` — watermark overlay
- `assets/audio/` — reel background music (optional)

## Run (development)

```bash
# Terminal 1 — Ollama
ollama serve

# Terminal 2 — Full stack
source .venv/bin/activate
npm run dev
```

This starts:
1. FastAPI backend on `http://127.0.0.1:8765`
2. Vite dev server on `http://127.0.0.1:5173`
3. Electron shell

## CLI Tools

**Test enriched caption pipeline:**
```bash
python backend/test_captioning.py ./test-photos/venue_01.jpg
python backend/test_captioning.py ./uploads/photo.jpg --vision florence2 --save
```

**Test classifier confidence matrix:**
```bash
python backend/test_classifier.py ./uploads
python backend/test_classifier.py ./test-photos --verbose --threshold 0.35
```

**Render a 2.5D parallax reel:**
```bash
**Assemble multi-photo montage reel (recommended):**
```bash
python backend/scripts/test_montage.py uploads/*.png --duration 4 --transition 0.8
python backend/scripts/test_montage.py photo1.jpg photo2.jpg photo3.jpg --audio assets/audio/track.mp3
```

**Legacy: single-image 2.5D depth parallax (deprecated — may show warping):**
```bash
python backend/scripts/test_depth_render.py ./uploads/photo.jpg --motion push_in --duration 5
```

**Render reel (production CLI):**
```bash
python backend/scripts/render_reel.py ./uploads/venue.jpg --category Weddings --motion push_in
```

**Export depth map only:**
```bash
python backend/scripts/render_reel.py ./uploads/venue.jpg --depth-only
```

## Event Categories

All uploads are scored against these five venue types:

1. Baby Shower
2. Birthday
3. Corporate
4. Weddings
5. Legacy Receptions

Multi-label assignment is supported (CLIP zero-shot with category-specific prompt templates).

## Apple Silicon Optimizations

| Component | Optimization |
|-----------|-------------|
| CLIP / Depth Anything V2 | PyTorch `mps` device via `get_device()` |
| Depth inference | `Depth-Anything-V2-Small-hf` (configurable) |
| Video encode | FFmpeg `hevc_videotoolbox` / `h264_videotoolbox` fallback |
| Captions | Ollama local LLM (Llama 3 8B default) |

Set environment overrides:
```bash
export OLLAMA_MODEL=mistral:7b
export CAPTION_VISION_MODEL=moondream2   # or florence2
export DEPTH_MODEL_ID=depth-anything/Depth-Anything-V2-Small-hf
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Device, MPS, Ollama status |
| POST | `/api/models/load` | Eager-load ML models |
| POST | `/api/upload/batch` | Batch upload + classify |
| GET | `/api/upload/images` | List categorized images |
| POST | `/api/render/montage` | Multi-photo Ken Burns + xfade reel (1080×1920) |
| POST | `/api/render/reel` | Legacy single-image 2.5D depth reel |
| POST | `/api/render/carousel` | 4:5 slide deck per category |
| POST | `/api/render/bundle` | Reel + carousel + caption |
| POST | `/api/captions/extract` | Moondream2/Florence-2 visual extraction |
| POST | `/api/captions/generate-enriched` | Full classify + extract + caption |
| POST | `/api/captions/generate` | Caption (optionally from filepath) |

## Studio Web UI

Launch the backend and open the luxury dashboard in your browser:

```bash
source .venv/bin/activate
python backend/main.py
# → http://127.0.0.1:8765/login
```

Default credentials (override via env): `vvluxe` / `vvluxe`

```bash
export STUDIO_USERNAME=your_user
export STUDIO_PASSWORD=your_pass
export STUDIO_SECRET_KEY=long-random-secret
```

Features: drag-and-drop multi-photo upload, Ken Burns montage settings, async FFmpeg progress, in-browser reel preview + download.

## Multi-Image Montage Engine (Primary)

| Module | Role |
|--------|------|
| `backend/services/montage_service.py` | Ken Burns per photo + FFmpeg xfade assembly |
| `backend/scripts/test_montage.py` | CLI for batch photo → vertical reel |

- **No depth warping** — crisp pre-edited photos with subtle zoom/pan only
- Per-clip Ken Burns (1.0→1.08×) with alternating pan directions
- Seamless **cross-fade** transitions via FFmpeg `xfade`
- Output: **1080×1920** @ 18Mbps HEVC VideoToolbox + BT.709
- Optional background audio trimmed to total montage duration

## Legacy 2.5D Depth Engine

| Module | Role |
|--------|------|
| `backend/models/depth_engine.py` | Depth Anything V2 on MPS, 16-bit depth, fg/bg segmentation |
| `backend/services/motion_service.py` | Procedural parallax paths (push_in, pan_left_right, tilt_up) |
| `backend/services/ffmpeg_renderer.py` | VideoToolbox HEVC/H.264 hardware encode + audio mux |
| `backend/scripts/test_depth_render.py` | End-to-end verification CLI |

- **No generative warping** — architecture stays rigid (affine transforms only)
- Motion presets: `push_in` (1.0→1.15×), `pan_left_right` (20px), `tilt_up` (vertical sweep)
- Output: 1080×1920 (9:16), 30/60fps, VideoToolbox accelerated

## License

Private — VV LUXE internal use.
