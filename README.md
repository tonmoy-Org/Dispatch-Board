# 🚀 Sterling Septic & Plumbing - FieldEdge Automation Suite

Complete production-grade automation platform for FieldEdge Dispatch Board display, asset assignment synchronization, and automated Quo (OpenPhone) Job Check-in SMS workflows.

---

## 📂 Project Architecture

```text
Dispatch_Board/
├── dispatch/
│   ├── job_checkin_automation.py               # FieldEdge & Quo Job Check-in Automation
│   ├── dispatch_board_display_automation_scraper.py # Visual grid & work order automation
│   ├── asset_assignment_scraper.py             # Mobile asset assignment synchronizer
│   ├── base_scraper.py                         # Playwright browser lifecycle & FieldEdge auth
│   ├── run_standalone.py                       # FastAPI real-time Atlassian Statuspage dashboard
│   ├── config/
│   │   ├── tech_mapping.json                   # 36 mapped technicians & sub-rows
│   │   ├── priority_data.json                  # Priority colors & check-in intervals
│   │   ├── dispatch_board_template.json        # Board layout & visual schedule templates
│   │   ├── scraper_rules.json                  # FieldEdge web selectors & XPaths
│   │   └── job_checkin_rules.json              # Modal and appointment selectors
│   ├── assets/                                 # UI brand assets (logo, favicon)
│   ├── job_checkin_state.json                  # Persistent job state tracking
│   ├── requirements.txt                        # Python dependencies
│   ├── .env.example                            # Template configuration
│   └── .env                                    # Environment credentials (git-ignored)
├── Dockerfile                                  # Containerized deployment
├── docker-compose.yml                          # Multi-service production compose
├── entrypoint.sh                               # Xvfb virtual framebuffer & container entry
├── run.py                                      # Universal cross-platform CLI runner
├── run.bat                                     # 1-click Windows runner
└── README.md                                   # Platform documentation
```

---

## ⏱️ Service 1: FieldEdge & Quo Job Check-in Automation

Automatically monitors active technicians on the FieldEdge Dispatch Board, sends automated SMS check-ins via Quo (OpenPhone) from the **Tech Line `(253) 880-1535`**, listens for technician replies, and adds/removes **`Complete @ [Time]`** notes directly on the board.

### Trigger & Interval Rules:
1. **Trigger:** Technician begins working on a customer work order (status `"Working"`).
2. **Priority Check-in Intervals:**
   - **Standard (30min Group):** 1st check-in @ **30 min**, then every **30 min**.
   - **Yellow Group (Corrections & Upgrade):** 1st check-in @ **2 hours**, then every **1 hour**.
   - **Brown Group (Excavator):** 1st check-in @ **4 hours**, then every **1 hour**.
   - **Internal Tasks:** Excluded completely (no SMS).
3. **Outbound SMS:**
   ```text
   ⏱️ 𝗝𝗢𝗕 𝗖𝗛𝗘𝗖𝗞-𝗜𝗡
   You have been on site for 30 minutes:
   Do you expect to be finished within 30 minutes?
   A — Need 1–2 more hours
   B — Need 2+ more hours
   C — Unsure; I will call within 5min
   ```
4. **Technician Response & Note Creation:**
   - Tech replies **A** -> Adds +1.5h -> `Complete @ [Time]`
   - Tech replies **B** -> Adds +2.5h -> `Complete @ [Time]`
   - Tech replies custom time (e.g. `11:30 AM`) -> `Complete @ 11:30 AM`
   - Creates an appointment block on the technician's sub-row (e.g. Mackie -> `c`, Russell -> `e`, Skyler -> `f1`).
5. **Auto-Cleanup:** When the work order status turns to `"Completed"`, the note is automatically removed from the board.

### CLI Usage:
```bash
# 1. Simulate check without sending SMS (Dry Run)
python run.py --checkin --dry-run

# 2. Run single check cycle for today
python run.py --checkin

# 3. Run single check cycle for a specific date
python run.py --checkin --date "11/30/2026"

# 4. Continuous 24/7 background monitoring (Daemon mode every 60s)
python run.py --checkin --daemon --poll-interval 60

# 5. Send test SMS to technician via Quo
python run.py --checkin --test-sms russell

# 6. Test creating note on the dispatch board
python run.py --checkin --test-note russell --time "10:30 AM"

# 7. Test deleting note from the dispatch board
python run.py --checkin --test-delete-note russell --time "10:30 AM"
```

---

## 📊 Service 2: Dispatch Board Display Automation & Real-Time Dashboard

Runs a FastAPI web dashboard (port `3000`) inspired by Atlassian Statuspage showing system health, 90-day uptime bars, and automated rolling schedule maintenance.

```bash
# Run web dashboard and 31-day rolling automation loop
python run.py --live --days 31 --loop --port 3000

# Open in browser:
http://localhost:3000
```

---

## 🐳 Docker & Production Deployment

The project is fully containerized using Google Chrome, Xvfb virtual framebuffer, and Playwright.

### 1. Run All Services with Docker Compose
```bash
# Run Dispatch Board Automation with Web Dashboard (Default)
docker compose up -d

# Run Job Check-in Service in Background
docker compose up -d job-checkin-automation

# Or run ALL services simultaneously
docker compose --profile all up -d
```

### 2. View Logs
```bash
# View dashboard logs
docker compose logs -f dispatch-automation

# View job check-in logs
docker compose logs -f job-checkin-automation
```

---

## ⚙️ Environment Configuration (`dispatch/.env`)

| Variable | Description |
|---|---|
| `DASH_EMAIL` | FieldEdge login email |
| `DASH_PASSWORD` | FieldEdge login password |
| `FIELDEDGE_LOGIN_URL` | FieldEdge login endpoint |
| `FIELDEDGE_BOARD_URL` | FieldEdge dispatch board URL |
| `HEADLESS` | `true` for headless, `false` for visible browser |
| `TZ` | Timezone (`America/Los_Angeles`) |
| `QUO_API_KEY` | OpenPhone / Quo REST API key |
| `QUO_API_BASE_URL` | Quo API base URL (`https://api.openphone.com`) |
| `TECH_PHONE_NUMBER_ID` | Quo Phone number ID (`PNAtQoMH5W`) |
| `TECH_PHONE_NUMBER` | Quo sender phone number (`+12538801535`) |
| `POLL_INTERVAL_SECONDS` | Job check-in daemon poll interval (default `60`) |
