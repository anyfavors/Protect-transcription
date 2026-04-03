# Protect Transcribe

Speech transcription for UniFi Protect security cameras.

When Protect detects speech on a camera, this service fetches the audio clip, transcribes it with [speaches](https://github.com/speaches-ai/speaches) (faster-whisper), and presents a searchable web UI. It also generates AI summaries of what was said, using Ollama.

---

## How it works

```
UniFi Protect  →  webhook  →  protect-transcribe  →  speaches (Whisper)
                                     ↓
                              SQLite database
                                     ↓
                            Web UI  +  Ollama summaries
```

1. **Alarm Manager** in UniFi Protect fires a webhook on speech detection.
2. The service looks up the camera, fetches the video clip, and extracts 16 kHz mono WAV audio via ffmpeg.
3. The WAV is sent to a [speaches](https://github.com/speaches-ai/speaches) server running faster-whisper.
4. The transcription is stored in SQLite with FTS5 full-text search.
5. A FastAPI/Alpine.js web UI lets you search, play audio, retry errors, and request AI summaries via Ollama.

---

## Features

- **Real-time** — webhook-driven, processes speech events as they happen
- **Searchable** — full-text search across all transcriptions (SQLite FTS5)
- **Audio playback** — original clip saved and served alongside the transcript
- **Segment timestamps** — click a word to jump to that moment in the audio
- **Danish-optimised** — ships with model presets for [Hviske V3](https://huggingface.co/syvai/faster-hviske-v3-conversation) and [Røst V3](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b) (Alexandra Instituttet)
- **Anti-hallucination** — configurable no-speech threshold, compression ratio, and n-gram loop detector
- **AI summaries** — daily/weekly/monthly summaries via Ollama
- **Dark mode** — default dark theme, toggleable per-session
- **Retry / re-transcribe** — retry individual errors or re-process everything with a new model

---

## Requirements

| Dependency | Purpose |
|---|---|
| [UniFi Protect](https://ui.com/camera-security) | NVR with speech detection enabled |
| [speaches](https://github.com/speaches-ai/speaches) | Whisper inference server (faster-whisper) |
| [Ollama](https://ollama.com) | AI summaries (optional) |
| Kubernetes + NFS storage | Deployment target |

---

## Quick start (local)

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Set environment variables
export PROTECT_HOST=argos.local
export PROTECT_USERNAME=admin
export PROTECT_PASSWORD=secret
export WHISPER_URL=http://localhost:8000

# Run
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open [http://localhost:8080](http://localhost:8080).

---

## Configuration

All settings can be changed at runtime in the Settings panel in the UI. They are persisted in the SQLite database.

| Environment variable | Default | Description |
|---|---|---|
| `PROTECT_HOST` | `argos.local` | Hostname or IP of the UniFi Protect NVR |
| `PROTECT_PORT` | `443` | HTTPS port |
| `PROTECT_USERNAME` | | Local Protect user (not SSO) |
| `PROTECT_PASSWORD` | | Password |
| `WHISPER_URL` | `http://whisper-server:8000` | speaches base URL |
| `OLLAMA_URL` | `http://ollama:11434` | Ollama base URL |
| `OLLAMA_MODEL` | `llama3.2` | Model for summaries |
| `DATABASE_PATH` | `/data/transcriptions.db` | SQLite database file |
| `AUDIO_PATH` | `/data/audio` | Where WAV clips are stored |
| `AUDIO_BUFFER_BEFORE` | `5` | Seconds to capture before the speech event |
| `AUDIO_BUFFER_AFTER` | `10` | Seconds to capture after (overridable in UI, up to 600 s) |
| `TZ` | `Europe/Copenhagen` | Timezone for display and summaries |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Whisper model selection

The service supports any [CTranslate2-format](https://github.com/OpenNMT/CTranslate2) model compatible with speaches. Recommended models for Danish:

| Model | Notes |
|---|---|
| `syvai/faster-hviske-v3-conversation` | Best Danish, drop-in CTranslate2 format |
| `CoRal-project/roest-v3-whisper-1.5b` | Alexandra Instituttet — 57% better CER on Danish |
| `Systran/faster-whisper-large-v3` | Strong multilingual baseline |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | ~6× faster, slightly lower accuracy |

Change model in **Settings → Transcription**, then use **Settings → Maintenance → Re-transcribe with current model** to re-process existing clips.

---

## UniFi Protect setup

1. Go to **Protect → Alarm Manager**
2. Create an alarm for **Speech** detection on the cameras you want
3. Add action: **Webhook → Custom Webhook**
   - URL: `https://your-domain/api/webhook`
   - Method: `POST`

The webhook endpoint is protected by an IP allowlist (configured in `protect-transcribe.yml`).

---

## Deployment (Kubernetes)

The Ansible playbook `protect-transcribe.yml` deploys the full stack to a k3s cluster:

```bash
# Prerequisites: whisper.yml deployed first (speaches in protect-transcribe namespace)

ansible-playbook protect-transcribe.yml \
  -e PROTECT_USERNAME=admin \
  -e PROTECT_PASSWORD=secret \
  -e CLOUDFLARE_TUNNEL_TARGET=tunnel-id.cfargotunnel.com
```

Resources created:
- **Namespace** `protect-transcribe`
- **Secret** with Protect credentials
- **PVC** (5 Gi NFS) for the database and audio files
- **Deployment** (single replica, prefers amd64)
- **Service** (ClusterIP)
- **IngressRoute** (Traefik, ``)
- **Middleware** IP allowlist for the webhook path

---

## Project structure

```
app/
├── __init__.py          # FastAPI app, lifespan, router wiring
├── config.py            # Environment variable constants
├── database.py          # SQLite schema, settings CRUD
├── models.py            # Pydantic request/response models
├── protect.py           # UniFi Protect API client singleton
├── transcription.py     # Audio fetching, ffmpeg extraction, Whisper call
├── worker.py            # Background queue and transcription worker loop
├── summaries.py         # Ollama AI summary generation
└── routes/
    ├── health.py        # GET /health
    ├── webhook.py       # POST /api/webhook
    ├── transcriptions.py # GET/DELETE/POST /api/transcriptions/*
    ├── settings.py      # GET/PUT /api/settings, connectivity tests
    ├── summaries.py     # GET/POST /api/summaries
    └── sync.py          # POST /api/sync
static/
└── app.js               # Alpine.js frontend logic
templates/
└── index.html           # UI shell (Tailwind CSS + Alpine.js)
tests/                   # pytest test suite
```

---

## Development

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Lint
ruff check .
ruff format --check .

# Type check
mypy app/

# Test
pytest
```

The CI pipeline runs lint → type check → tests → source security scan → Docker build → release → image security scan on every push to `main`.
