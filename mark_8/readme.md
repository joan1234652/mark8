# ⚙️ MARK 6
### The Ultimate Cross-Platform Personal AI Assistant — By Hasan.

**Mark 6** is the next evolution of Mark XLVIII — now with self-improving skills, semantic memory, free-AI path via OpenRouter, custom animated avatar, custom voice cloning, resilient Live API connection with model fallback chain, and a first-run setup wizard.

A real-time voice AI that can hear, see, understand, and control your computer — on any OS. Supports Windows, macOS, and Linux. Built on the Gemini Live API for native audio streaming, delivering zero subscriptions and total digital autonomy.

---

## 🆕 What's new in Mark 1

| Feature | What it does |
| --- | --- |
| 🧠 **Semantic memory (vector store)** | Free-text recall: ask "what did we discuss about Honda?" — JARVIS searches its entire memory by meaning, not by key. Uses Gemini's free `text-embedding-004` model when a key is set, falls back to a pure-Python hashing embedding offline. |
| 🔨 **Skill Forge (self-upgrade)** | JARVIS writes new Python skills on the fly. User asks "tell me the time" → Skill Forge generates `actions/custom/tell_time.py`, validates it, tests it, registers it as a callable tool — all in one turn. |
| 🎓 **Skill Registry** | One source of truth for everything JARVIS can do. Auto-discovers built-in actions + custom skills. main.py can subscribe to `on_change()` to refresh its Live API tool list when a new skill is forged live. |
| 🧩 **Unified AI client (`core/ai_client.py`)** | Single surface that routes between OpenRouter (free models) and Gemini. TTL cache (5 min), connection pool, retry-with-backoff, auto-fallback to Gemini on OpenRouter failure. |
| 🆓 **Free OpenRouter model catalog** | 10 curated free models tagged by task: `general`/`reasoning`/`code`/`long-context`/`vision`/`multilingual`. Auto-picks the best model per task. |
| 🎙️ **Voice-controlled skill management** | New tools surfaced to the Live API: `learn_skill`, `list_skills`, `forget_skill`, `recall_memory`, `remember_memory`. |
| 📝 **Improved system prompt** | Teaches JARVIS the self-improvement loop + when to forge vs. when to recall. |
| 🛠️ **First-run setup wizard** | `python setup.py` now walks the user through installing deps + entering a Gemini key (validated — bad keys are refused) + optional OpenRouter key + optional avatar PNG + optional voice sample. Idempotent — re-running prompts with current values pre-filled. |
| 🔄 **Live API model fallback chain** | 11 known Gemini Live API models tried in priority order. When Google retires a preview (e.g. the Dec 2025 model that was hardcoded in v1), JARVIS auto-tries the next one instead of crashing with `1008 Requested entity was not found`. |
| 🧵 **Thread-safe avatar** | Avatar widget uses Qt signals for cross-thread state changes — fixes the `QObject::killTimer: Timers cannot be stopped from another thread` warning. |

---

## ✨ Overview

Mark 1 is a direct evolution of Mark XLVIII — sharper, faster, more natural to talk to, and now able to learn new skills on demand. This build focused entirely on removing every friction point: the silence while JARVIS "thinks," the awkward double-responses, the interrupts that took four seconds to land, the news results that linked to homepages instead of articles. Everything that made the experience feel like software instead of an assistant has been addressed.

It's not just an assistant — it's an extension of your digital life — and now it can extend itself.

---

## 🚀 Capabilities

### Core Features
| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Ultra-low latency conversation in any language via Gemini Live API |
| 🖥️ System Control | Launch apps, adjust volume/brightness, WiFi, shortcuts, power — all by voice |
| 🧩 Autonomous Tasks | High-level planning for complex multi-step goals via agent mode |
| 👁️ Visual Awareness | Real-time screen capture and webcam vision piped into your main Gemini session |
| 🧠 Persistent Memory | Deeply remembers projects, preferences, and personal context across sessions |
| ⌨️ Hybrid Input | Seamlessly switch between keyboard typing and voice commands |
| 🌅 Morning Briefing | On first boot: greets you, reads the time, fetches live news headlines, and checks weather |
| 🔔 Proactive Check-ins | After 15 minutes of silence JARVIS checks context and offers something genuinely useful — no hardcoded rules, Gemini decides |
| 📊 Hardware Monitoring | Continuous CPU, RAM, GPU and temperature telemetry with localized voice alerts when thresholds are breached |
| 🌤️ Weather Report | Live weather data for your city, personalized from memory |
| 🗺️ Dynamic Content Panel | Scrollable display layer beneath the HUD that renders web results, news, and search data with timestamps |
| 🔍 Multi-Mode Web Search | `news` / `research` / `price` / `compare` / `search` — Gemini Grounded first, DDG fallback |
| ⏰ Smart Reminders | OS-native scheduled notifications (Windows Task Scheduler / macOS LaunchAgent / Linux systemd) |
| ✈️ Flight Finder | Live flight price and availability lookup |
| 🎮 Game Updater | Checks and triggers game updates on demand |
| 📂 File Processor | Read, summarize, and answer questions about local files |
| 💻 Code Helper | Inline code review, debugging, and generation |
| 🌐 Browser Control | Open URLs, navigate tabs, and interact with the browser by voice |
| 📨 Send Message | Compose and send messages through integrated messaging apps |
| 🎬 YouTube Control | Search, play, and control YouTube playback by voice |
| 🖱️ Desktop Control | Taskbar, window management, and desktop-level operations |
| 🧑‍💻 Silent Language Memory | Detects spoken language on first use and saves it silently — all future sessions and briefings adapt automatically |

---

## 🆕 What's New in XLVIII

### ✋ Instant Interrupt — ESC or Button
Press **Escape** or click the `INTERRUPT` button to cut JARVIS off mid-sentence and return to listening. Previously, cancelling a response required waiting for the full audio buffer to drain — sometimes four seconds. In Mark XLVIII, audio is split into ~50 ms chunks (2400 bytes each) so the interrupt fires within one slice. The interrupt drains the audio queue, sets a flag, and clears the turn — listening resumes in under 100 ms.

### 👁️ Immediate Vision Acknowledgment
When you ask JARVIS to look at your screen or camera, it no longer goes silent while processing. The tool now instructs Gemini to immediately say a natural sentence ("Looking at your screen now, sir" / "Ekrana bakıyorum efendim") while the image capture runs. The actual analysis follows as a second response. No more awkward silence.

### 📰 Parallel News Search — First Result Wins
News queries now run **Gemini Grounded Search and DuckDuckGo news simultaneously** in two daemon threads. Whichever delivers a valid result first wins; the other is silently discarded. Previously, a Gemini 503 error would stall the search for several seconds before falling back to DDG. Now the fallback happens in parallel — total news fetch time drops to whichever backend is fastest at that moment.

### 🗞️ Real News Articles (Not Homepages)
DDG news search was previously using `ddgs.text()`, which returned website homepage URLs for news queries. Mark XLVIII switches to `ddgs.news()`, which returns actual article URLs, titles, snippets, and source names — exactly what you want in a news briefing.

### 🌅 Two-Phase Startup Briefing — Runs Concurrently
The startup briefing now sends Phase 2 (news fetch) while Phase 1 audio (the greeting) is still playing. Previously, Phase 2 waited for Phase 1 to fully complete. The 1.5-second overlap means the news headline is ready by the time JARVIS finishes saying "Good morning."

### 🔁 Smarter Reconnection — Exponential Backoff
Network timeouts now use exponential backoff: 3s → 6s → 12s → 60s (capped). Each retry shows a Turkish-language status message in the UI ("Bağlantı kurulamadı — Xs sonra tekrar deneniyor"). Previously, a dropped connection would loop tightly and show no useful information.

### 🛡️ Vision Cooldown & Echo Guard
Screen capture is guarded against echo loops. If JARVIS speaks about the screen and the microphone picks up its own voice, a secondary `screen_process` call can be triggered. Mark XLVIII blocks duplicate calls with a 4-second cooldown and a `_vision_busy` flag. Both are fully reset when a new session connects — fixing a bug where the cooldown would persist across reconnects and block legitimate requests.

### 🌐 Language-Aware Address — Never Mixed
The system prompt now enforces: **Turkish → efendim**, **English → sir**, never mixed. Mark XLVII's prompt said "Always call sir to user" which caused JARVIS to say "sir" mid-Turkish sentence. Fixed.

### 🪟 Zero Terminal Windows
A subprocess monkey-patch at startup sets `CREATE_NO_WINDOW` on every child process launched by the app. No PowerShell, no CMD, no terminal flash — ever. Applies to all actions including reminders, system commands, and scheduler calls.

### 🔄 Session State Isolation
All transient vision and interrupt flags (`_pending_vision`, `_vision_busy`, `_vision_cam_active`, `_vision_close_pending`, `_interrupted`) are fully reset whenever a new Gemini session connects. Previously, state from a crashed session could carry over and leave JARVIS in a broken state until restart.

---

## ⚡ Quick Start

```bash
git clone https://github.com/Hasan8123/Mark-XLVIII.git
cd Mark-XLVIII
python setup.py      # ← first-run wizard: installs deps + asks for your Gemini key + optional avatar PNG + voice sample
python main.py
```

The first-run wizard will walk you through:

1. **Gemini API key** — required. Validates the format (must start with `AIza`, 39 chars). Refuses to write a bad key — you'll be re-prompted. Get one free at <https://aistudio.google.com/apikey>.
2. **OpenRouter API key** — optional. Free text-AI routing through Llama 3.3 / DeepSeek R1 / Qwen Coder. Get one free at <https://openrouter.ai/keys>.
3. **Avatar PNG path** — optional. Your face for JARVIS. Auto-detects the mouth region for lip-sync.
4. **Voice sample** — optional. 5-30s WAV/MP3 of the voice you want JARVIS to use. Builds a voice profile (pitch + tempo) that the TTS layer applies on every utterance.

Re-running `python setup.py` is safe — it re-prompts with current values pre-filled, so you can update individual fields without losing the rest.

> ⚠️ **Installation Note:** Some OS-specific dependencies are not bundled in `requirements.txt` to keep the repo lightweight. If you hit a `ModuleNotFoundError`, install the missing package with `pip install <module_name>`.

---

## 📋 Requirements

| Requirement | Details |
| --- | --- |
| **OS** | Windows 10/11, macOS, or Linux |
| **Python** | 3.11 or 3.12 |
| **Microphone** | Required for voice interaction |
| **API Key** | Free Gemini API key for voice (Live API) — `config/api_keys.json` |
| **Optional** | Free OpenRouter API key to route text-only actions through free OpenRouter models — get one at <https://openrouter.ai/keys> |

---

## 🆕 OpenRouter integration (free text AI)

Mark 1 ships with a **unified AI client** (`core/ai_client.py`) that routes text-based actions (the dev-agent planner/writer, code-helper reviewer, etc.) between **Gemini** and **OpenRouter's free model tier** based on a single config flag.

The voice / Live API in `main.py` still requires Gemini — there is no free OpenRouter equivalent for real-time audio. But every text-only action can now run on OpenRouter's free models, so you can run the project without paying for any tokens at all.

### Configuring

Edit `config/api_keys.json`:

```json
{
  "gemini_api_key":     "AIza…",            // required for voice / Live API
  "openrouter_api_key": "sk-or-v1-…",       // optional — grab one free at openrouter.ai/keys

  // Picks the text-LLM provider. Auto-detects when omitted:
  //   - 'openrouter'  if openrouter_api_key is set
  //   - 'gemini'      otherwise
  "llm_provider": "openrouter",

  "gemini_model":      "gemini-2.5-flash",
  "openrouter_model":  "meta-llama/llama-3.3-70b-instruct:free"
}
```

When `llm_provider` is `openrouter` but `openrouter_api_key` is empty, every text call automatically falls back to Gemini — so you can switch providers in config without breaking anything.

### Curated free OpenRouter models

OpenRouter exposes a generous free tier. The catalog is centralised in `core/openrouter_models.py` so new models can be added in one place:

| Tag | Default model | Use case |
| --- | --- | --- |
| `general` / `fast` | `meta-llama/llama-3.3-70b-instruct:free` | All-round chat, planning |
| `reasoning` | `deepseek/deepseek-r1:free` | Deep step-by-step reasoning |
| `code` | `qwen/qwen-2.5-coder-32b-instruct:free` | Code generation / review |
| `long-context` | `google/gemini-2.0-flash-exp:free` | 1M-token context |
| `lightweight` | `meta-llama/llama-3.1-8b-instruct:free` | Short Q&A, ultra-fast |

Browse the live list with: `python -c "from core.openrouter_models import list_free_models; import json; print(json.dumps(list_free_models(), indent=2))"`

### Efficiency features baked in

The unified client adds three transparent efficiency layers on every text call:

1. **In-process TTL response cache (5 min, 256 entries)** — duplicate prompts within a 5-minute window hit the cache and skip the network round-trip entirely. The cache key is a SHA-256 of the request payload (model + messages + temperature + max_tokens), so identical calls are safe to repeat.
2. **Connection pool singleton** — every OpenRouter request reuses a single `requests.Session`, so the TLS handshake happens once. Subsequent calls shave 100-300 ms of cold-start cost.
3. **Retry with exponential backoff (0.5 s → 1 s → 2 s → 4 s, capped at 4 s)** — transient HTTP errors (429, 500, 502, 503, 504) automatically retry up to four times. `Retry-After` header is honoured when present.
4. **Auto-fallback to Gemini** — if an OpenRouter call fails outright (auth error, network down, model retired), the client transparently retries on Gemini using the same `gemini_api_key` so the user never sees a hard failure.
5. **Lazy imports** — `google-genai` is only imported when the Gemini backend is actually used, keeping OpenRouter-only startup fast.

### Smoke test

Verify your config without touching the network:

```bash
python -c "from core import ai_client; import json; print(json.dumps(ai_client.health(), indent=2))"
```

Output:

```json
{
  "active_provider": "openrouter",
  "active_model": "meta-llama/llama-3.3-70b-instruct:free",
  "gemini_configured": true,
  "openrouter_configured": true
}
```

---

## 🔨 Self-improvement: Skill Forge

JARVIS can write new skills on the fly. When the user asks for something JARVIS can't currently do ("tell me the time", "convert 100 usd to eur", "roll a 20-sided die"), JARVIS calls the `learn_skill` tool which triggers `core/skill_forge.py`:

1. **Generate** — `ai_client.generate_text()` writes a Python module following the Skill Forge contract (function + `TOOL_DECLARATION` dict)
2. **Validate** — `ast.parse()` + dangerous-pattern scan (refuses `subprocess`, `os.system`, `eval`, `exec`, `__import__`, `pty.spawn`, `shutil.rmtree`)
3. **Save** — writes to `actions/custom/{skill_name}.py`
4. **Load** — `importlib.util.spec_from_file_location` in an isolated namespace
5. **Test** — calls the function with sample args; rejects skills that crash
6. **Register** — adds to `core.skill_registry` and fires `on_new_skill` callback so main.py can refresh the Live API tool list

The new skill becomes available on the next turn — JARVIS can call it immediately.

### The Skill Forge contract

A generated module must define:

```python
def <skill_name>(**kwargs) -> str | dict:
    ...

TOOL_DECLARATION = {
    "name":        "<skill_name>",
    "description": "When to call this skill (one short sentence).",
    "parameters":  {
        "type": "OBJECT",
        "properties": { ... },   # JSON schema; empty dict if no params
        "required": [...]
    },
}
```

Return `str` for spoken reply, or `{"text": str, "extra": any}` to pass structured data back without it being spoken.

### Voice-controllable tools surfaced to the Live API

| Tool | Purpose |
| --- | --- |
| `learn_skill(intent, suggested_name="")` | Forge a new skill from a plain-English intent |
| `list_skills(filter_custom=False)` | Read back every registered skill |
| `forget_skill(skill_name)` | Delete a previously-forged custom skill |
| `recall_memory(query, k=5)` | Semantic search over JARVIS's vector memory |
| `remember_memory(text, category, tags)` | Save a free-text memory for later recall |

### Smoke test

```bash
python tests/test_skill_forge.py
# === 3/3 test groups passed ===
```

---

## 🧠 Semantic memory: vector store

`core/memory_vector.py` adds free-text semantic recall layered on top of the existing JSON memory (`memory/memory_manager.py`).

**Embedding backend**: prefers Google's free `text-embedding-004` via the `google-genai` SDK when a Gemini key is configured. Falls back to a pure-Python hashing bag-of-words embedding (1024-dim) so the store works without network.

**Storage**: SQLite — one row per memory (id, text, metadata JSON, embedding JSON, created_at). Pure-Python cosine similarity. Fine for the 1k–10k memory scale a single user generates; swap to `sqlite-vec` if you outgrow it.

### API

```python
from core.memory_vector import vstore

# Remember anything
vstore.remember(
    "User asked me to remind them about the Honda Civic oil change next month.",
    metadata={"category": "projects", "tags": ["honda", "car", "maintenance"]}
)

# Semantic recall — finds by meaning, not by exact token
hits = vstore.recall("car maintenance", k=5)
# → [{id, text, metadata, score, created_at}, ...]

# Filter by category or tag
hits = vstore.recall("appointment", category="reminders", tag="dentist")
```

The existing `memory/memory_manager.py` exposes thin wrappers: `recall(query, k)`, `remember_text(text, category, tags)`, `list_vector_memories(limit)`.

---

## 🗂️ Project Structure

```
Mark 1/
├── main.py                  # Core loop — Gemini Live session, audio I/O, tool dispatch
├── ui.py                    # PyQt6 HUD — waveform, log panel, interrupt button, camera feed
├── setup.py                 # First-run configuration wizard
├── actions/
│   ├── web_search.py        # Gemini + DDG parallel search (news, research, price, compare)
│   ├── screen_processor.py  # Screen capture & webcam vision via Gemini Live
│   ├── reminder.py          # OS-native scheduled notifications
│   ├── system_monitor.py    # CPU / RAM / GPU / temperature telemetry
│   ├── computer_settings.py # Volume, brightness, WiFi, power
│   ├── computer_control.py  # Keyboard shortcuts, mouse, window management
│   ├── open_app.py          # Application launcher
│   ├── browser_control.py   # Web browser control
│   ├── file_controller.py   # File system operations
│   ├── file_processor.py    # Document reading and summarization
│   ├── send_message.py      # Messaging integration
│   ├── weather_report.py    # Live weather data
│   ├── flight_finder.py     # Flight search
│   ├── youtube_video.py     # YouTube playback control
│   ├── game_updater.py      # Game update management
│   ├── code_helper.py       # Code review and generation
│   ├── dev_agent.py         # Developer task agent
│   ├── desktop.py           # Desktop and taskbar control
│   ├── proactive.py        # Proactive silence-break suggestions
│   ├── skill_manager.py     # ★ Voice-controllable Skill Forge + memory tools
│   └── custom/             # ★ JARVIS-forged skills land here
│       └── tell_time.py    # ★ Example skill showing the contract
├── memory/                  # Persistent key-value memory store + SQLite vector DB
│   ├── memory_manager.py   # JSON memory + new recall()/remember_text() vector bridge
│   └── vector_store.db     # Auto-created SQLite vector store
├── core/
│   ├── ai_client.py         # Unified AI client — OpenRouter (free) + Gemini fallback
│   ├── openrouter_models.py # Curated catalog of free OpenRouter models
│   ├── llm_client.py        # Legacy Ollama/OpenAI/OpenRouter router (delegates to ai_client)
│   ├── memory_vector.py    # ★ Vector store — semantic recall over JARVIS's memory
│   ├── skill_forge.py       # ★ Self-upgrade engine — generates, validates, loads, tests new skills
│   ├── skill_registry.py    # ★ Catalog of every skill (built-in + custom)
│   ├── installer.py          # First-run dependency installer
│   ├── prompt.txt           # JARVIS personality + self-improvement rules
│   ├── stt.py               # Speech to text (Whisper / Google)
│   └── tts.py               # Text to speech
├── tests/                  # ★ Self-tests — no network needed
│   ├── test_ai_client.py    # 7 groups, all passing
│   └── test_skill_forge.py  # 3 groups, all passing
└── config/
    └── api_keys.json        # API key and system configuration (gitignored)
```

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

---

## 👤 Connect with the Creator

Engineered by a developer building a real-world JARVIS-style assistant.
⭐ **Star the repository to support the journey to Mark 100.**

| Platform | Link |
| --- | --- |
| GitHub | [@lumendev8](https://github.com/lumendev8) |
| X | [@lumendev8](https://x.com/lumendev8) |
| LinkedIn | [@lumendev8](https://www.linkedin.com/company/lumendev8) |
