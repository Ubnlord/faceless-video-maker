# 🎬 Faceless Video Maker

**A real, working AI faceless video generator.** Give it a topic and it writes a
script, records a neural voiceover, generates cinematic visuals, times
word-synced captions and renders an actual **MP4** you can upload to TikTok,
YouTube Shorts or Instagram Reels.

No face. No camera. No editing timeline. No mockups.

---

## What this is (and what it replaced)

The original repository was a **single static `index.html`** that:

| Original behaviour | Problem |
| --- | --- |
| Called a Google Apps Script for the script | External dependency, no fallback, no control |
| Used the browser's `speechSynthesis` for "voice" | Never captured into the file — the video had **no audio** |
| Recorded a `<canvas>` with `MediaRecorder` | Produced a **silent WebM**, not a shareable MP4 |
| Ran entirely in the browser | Nothing to deploy, nothing to scale, no real output |

This project keeps the same product idea and the same single-page feel, but
moves the work to a **real backend** that produces a **real video file**:

```
topic ──► script ──► voiceover (MP3 + word timings) ──► captions (ASS/SRT)
              └──► procedural backgrounds ──► animated clips ──► concat
                                                                    │
                        music bed ──► mix ──► burn captions ──► MP4 ◄┘
```

Every stage is genuine: the voiceover is a real MP3 with real word-level
timings, the visuals are real generated images animated by ffmpeg, and the
output is a real H.264/AAC MP4 with the captions burned into the pixels.

---

## Quick start

### 1. Requirements

* **Python 3.10+**
* **ffmpeg** (with `libx264`, `aac` and `libass`) — the only system dependency

```bash
# Debian / Ubuntu
sudo apt-get install -y ffmpeg fonts-dejavu-core

# macOS
brew install ffmpeg
```

### 2. Run it

```bash
git clone https://github.com/Ubnlord/faceless-video-maker.git
cd faceless-video-maker
./run.sh
```

`run.sh` creates a virtualenv, installs the Python dependencies and starts the
server. Then open **<http://localhost:8000>**.

<details>
<summary>Manual setup instead of <code>run.sh</code></summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
</details>

### 3. Docker

```bash
docker compose up --build      # http://localhost:8000
```

---

## Using it

1. Open the app and click **Create a video**.
2. Type a topic — e.g. *"5 habits that can completely change your life"*.
3. Pick a niche, length, style, voice, caption style and background.
4. **Generate script** to preview the script, or **Create video** to render.
5. Watch the live progress, then play and download the MP4, the voiceover and
   the `.srt` captions.

Renders take roughly **1–3 minutes** for a 60-second video on one CPU core.

---

## Configuration

Everything is optional — with no configuration at all the app runs fully
offline using the built-in scriptwriter. Copy `.env.example` to `.env` to
change anything.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(empty)* | Enables LLM scriptwriting. Empty ⇒ offline writer |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for scripts |
| `FVM_DEFAULT_VOICE` | `en-US-AndrewNeural` | Default neural voice |
| `FVM_TTS_RATE` | `+8%` | Speaking rate |
| `FVM_WIDTH` / `FVM_HEIGHT` | `1080` / `1920` | Output resolution |
| `FVM_FPS` | `30` | Frame rate |
| `FVM_CRF` / `FVM_PRESET` | `20` / `veryfast` | x264 quality / speed |
| `FVM_MAX_WORKERS` | `1` | Concurrent renders |
| `FVM_DATA_DIR` / `FVM_OUTPUT_DIR` | `./data` / `./output` | Storage |

### Script engines

* **LLM engine** — used automatically when `OPENAI_API_KEY` is set. Works with
  OpenAI, Groq, Together, OpenRouter, Ollama, LM Studio, vLLM or anything else
  that speaks the `/chat/completions` shape.
* **Offline engine** — a deterministic, dependency-free writer that always
  works. It is the default, and it is what keeps the test suite hermetic.

If the LLM call fails for any reason the app logs a warning and silently falls
back to the offline writer, so a render never dies because of a flaky API.

---

## Architecture

```
faceless-video-maker/
├── app/
│   ├── main.py        FastAPI app: REST API + serves the UI
│   ├── config.py      Environment-driven settings
│   ├── models.py      Pydantic request/response schemas
│   ├── scriptgen.py   Script generation (LLM + offline fallback)
│   ├── tts.py         Neural TTS with word-level timings
│   ├── visuals.py     Procedural background generation (Pillow)
│   ├── captions.py    Karaoke ASS + SRT caption builders
│   ├── render.py      ffmpeg: scene clips, concat, music, final encode
│   ├── pipeline.py    Orchestrates the whole generation
│   └── jobs.py        Job store + background worker
├── index.html         The single-page UI
├── scripts/
│   ├── smoke_test.py  Renders a real video and verifies it with ffprobe
│   └── api_e2e.py     Drives the live HTTP API end to end
├── tests/             pytest suite (unit, API, render)
├── Dockerfile
└── docker-compose.yml
```

### How each stage actually works

**Script** — `scriptgen.py` produces a hook, N narration beats and a call to
action. The offline engine composes them from curated beat templates; the LLM
engine asks for strict JSON and validates it.

**Voice** — `tts.py` uses Microsoft Edge's neural voices via `edge-tts`. It
requests `WordBoundary` events explicitly (the service defaults to *sentence*
boundaries, which are far too coarse for karaoke captions) and converts the
100-nanosecond tick offsets into seconds. If the service returns no boundaries,
timings are estimated by distributing the measured duration across the words,
weighted by word length. Transient empty streams are retried with backoff.

**Visuals** — `visuals.py` builds each background with Pillow: a vertical
gradient, two or three soft radial light sources, sine-wave light streaks,
particles and a vignette. Eight palettes rotate across scenes so consecutive
scenes look different. Generation is seeded, so the same job always produces
the same images.

**Motion** — `render.py` animates each still with ffmpeg's `zoompan` filter,
cycling through `zoom_in`, `pan_up`, `zoom_out` and `pan_down`, with a short
fade in and out on every scene.

**Captions** — `captions.py` groups the word timings into short chunks (max 4
words / 26 characters, split on long pauses) and emits one ASS dialogue line
per word so the currently spoken word can be highlighted and scaled up. The
same timings produce a plain `.srt` sidecar.

**Audio** — each beat is synthesised separately, which makes every scene's
duration exact. The beats are concatenated with a short pause between them, and
a soft ambient music bed is synthesised from detuned sine tones with ffmpeg's
own generators — no external audio assets. The voice and the bed are mixed with
`amix`, with the voice kept dominant.

**Final encode** — the scene clips are concatenated, the audio is muxed in, the
ASS captions are burned in by libass, and the result is encoded as H.264 High
profile + AAC with `+faststart` for web playback.

---

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | The single-page UI |
| `GET` | `/api/health` | Status, ffmpeg availability, engine, resolution |
| `GET` | `/api/options` | Niches, lengths, styles, caption styles, aspects |
| `GET` | `/api/voices` | Available neural voices |
| `GET` | `/api/backgrounds` | Available background palettes |
| `POST` | `/api/script` | Preview a script without rendering |
| `POST` | `/api/videos` | Queue a render (returns `202` + the job) |
| `GET` | `/api/videos` | List jobs |
| `GET` | `/api/videos/{id}` | Job status, progress, script and scenes |
| `DELETE` | `/api/videos/{id}` | Delete a job and its files |
| `GET` | `/api/videos/{id}/video` | Download the MP4 |
| `GET` | `/api/videos/{id}/voice` | Download the voiceover MP3 |
| `GET` | `/api/videos/{id}/captions` | Download the SRT |
| `GET` | `/api/videos/{id}/thumbnail` | Download the thumbnail |

Interactive docs are at **`/docs`**.

### Example

```bash
# Queue a render
curl -s -X POST http://localhost:8000/api/videos \
  -H 'Content-Type: application/json' \
  -d '{"topic":"5 habits that change your life","length":"60 seconds","music":true}'

# Poll it
curl -s http://localhost:8000/api/videos/<job_id>

# Download the result
curl -o video.mp4 http://localhost:8000/api/videos/<job_id>/video
```

---

## Tests

```bash
pip install pytest
python -m pytest              # 41 tests: units, API, render
python scripts/smoke_test.py  # renders a real video, verifies it with ffprobe
python scripts/api_e2e.py     # drives the live HTTP API end to end
```

The suite covers script generation and determinism, word-timing estimation,
caption chunking and ASS/SRT output, background generation, every ffmpeg motion
path, audio assembly, the music bed, and the full REST surface. Render tests
skip automatically when ffmpeg is not installed.

---

## Notes and limits

* Renders are CPU-bound. One 60-second 1080×1920 video takes roughly 1–3
  minutes on a single core; raise `FVM_MAX_WORKERS` on a bigger machine.
* Jobs are stored in memory and mirrored to `data/jobs.json`. For a
  multi-instance deployment, swap `JobStore` for a real queue and database.
* The music bed is synthesised, not licensed music. Point `build_music_bed` at
  your own tracks if you want something richer.
* Voice synthesis uses the public Edge TTS endpoint. It is free and needs no
  key, but it is a third-party service — if it is unreachable, the affected
  beat falls back to silence so the render still completes.

---

## License

MIT.
