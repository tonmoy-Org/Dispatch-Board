#!/usr/bin/env python3
import os
import sys
import re
import json
import time
import asyncio
import argparse
import unicodedata
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dotenv import load_dotenv

_app_dir = os.path.dirname(os.path.abspath(__file__))
for env_candidate in [
    os.path.join(_app_dir, ".env"),
    os.path.join(_app_dir, "..", "dispatch", ".env"),
    os.path.join(_app_dir, "..", ".env"),
]:
    if os.path.exists(env_candidate):
        load_dotenv(env_candidate)
        break

DASH_EMAIL = os.getenv("DASH_EMAIL", "")
DASH_PASSWORD = os.getenv("DASH_PASSWORD", "")
FIELDEDGE_LOGIN_URL = os.getenv("FIELDEDGE_LOGIN_URL", "")
FIELDEDGE_BOARD_URL = os.getenv("FIELDEDGE_BOARD_URL", "")
HEADLESS = os.getenv("HEADLESS", "true").strip().lower() in ("true", "1", "yes")

QUO_API_BASE_URL = os.getenv("QUO_API_BASE_URL", "").rstrip("/")
QUO_API_KEY = os.getenv("QUO_API_KEY", "")
TECH_PHONE_NUMBER_ID = os.getenv("TECH_PHONE_NUMBER_ID", "")
TECH_PHONE_NUMBER = os.getenv("TECH_PHONE_NUMBER", "")

STATE_FILE = os.getenv("JOB_CHECKIN_STATE_FILE", os.path.join(_app_dir, "job_checkin_state.json"))
CONFIG_DIR = os.path.join(_app_dir, "config")
if not os.path.isdir(CONFIG_DIR) and os.path.isdir(os.path.join(_app_dir, "dispatch", "config")):
    CONFIG_DIR = os.path.join(_app_dir, "dispatch", "config")


def normalize_str(s: Optional[str]) -> str:
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(s))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c) and ord(c) < 128)
    clean = re.sub(r"[^\w\s-]", " ", ascii_only).lower()
    return re.sub(r"\s+", " ", clean).strip()


def get_pst_now() -> datetime:
    import zoneinfo
    try:
        return datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    except Exception:
        return datetime.utcnow() - timedelta(hours=8)


def is_within_operating_hours(now_dt: Optional[datetime] = None) -> Tuple[bool, str]:
    now = now_dt or get_pst_now()
    try:
        start_hour = int(os.getenv("OPERATING_START_HOUR_PST", "7"))
        end_hour = int(os.getenv("OPERATING_END_HOUR_PST", "20"))
    except ValueError:
        start_hour, end_hour = 7, 20

    start_time = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)

    is_active = (start_time <= now <= end_time)
    window_desc = f"{start_hour:02d}:00 AM to {end_hour % 12 or 12}:00 PM PST"
    return is_active, window_desc


TECH_MAPPING: Dict[str, Any] = {}
TECH_PHONE_MAPPING: Dict[str, str] = {}
SUB_ROW_MAPPING: Dict[str, str] = {}

_tech_path = os.path.join(CONFIG_DIR, "tech_mapping.json")
if os.path.exists(_tech_path):
    try:
        with open(_tech_path, "r", encoding="utf-8") as f:
            TECH_MAPPING = json.load(f)
            for k, v in TECH_MAPPING.items():
                if isinstance(v, dict):
                    if v.get("phone"):
                        TECH_PHONE_MAPPING[k.lower()] = v["phone"]
                    if v.get("sub_row"):
                        SUB_ROW_MAPPING[k.lower()] = v["sub_row"]
    except Exception as e:
        print(f"⚠️ Error loading tech_mapping.json: {e}")

_env_tech_phones = os.getenv("TECH_PHONE_MAPPING_JSON", "").strip()
if _env_tech_phones:
    try:
        TECH_PHONE_MAPPING.update(json.loads(_env_tech_phones))
    except Exception as e:
        print(f"⚠️ Error parsing TECH_PHONE_MAPPING_JSON from .env: {e}")

PRIORITY_LOOKUP: Dict[int, Dict[str, str]] = {}
_p_path = os.path.join(CONFIG_DIR, "priority_data.json")
if os.path.exists(_p_path):
    try:
        with open(_p_path, "r", encoding="utf-8") as f:
            for it in json.load(f):
                name = it.get("name", "")
                c_str = it.get("color", "")
                if c_str.startswith("rgb("):
                    parts = c_str.replace("rgb(", "").replace(")", "").split(",")
                    if len(parts) == 3:
                        r, g, b = int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())
                        bgr_int = (b << 16) | (g << 8) | r
                        PRIORITY_LOOKUP[bgr_int] = {
                            "name": name,
                            "hex": f"#{r:02X}{g:02X}{b:02X}"
                        }
    except Exception as e:
        print(f"⚠️ Error loading priority_data.json: {e}")

PRIORITY_CHECKIN_RULES = {
    "30min": {"initial_delay_mins": 30, "interval_mins": 30},
    "2hr": {"initial_delay_mins": 120, "interval_mins": 60},
    "4hr": {"initial_delay_mins": 240, "interval_mins": 60},
}


class QuoClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or QUO_API_KEY
        self.base_url = (base_url or QUO_API_BASE_URL).rstrip("/")
        self.headers = {
            "Authorization": self.api_key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Content-Type": "application/json"
        }

    def send_sms(self, to_number: str, message: str, from_number: Optional[str] = None) -> Dict[str, Any]:
        sender = from_number or TECH_PHONE_NUMBER
        url = f"{self.base_url}/v1/messages"
        payload = {
            "content": message,
            "from": sender,
            "to": [to_number]
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "data": res}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            print(f"❌ Quo API Error [{e.code}]: {err_body}")
            return {"success": False, "error": err_body, "status": e.code}
        except Exception as e:
            print(f"❌ Quo Request Error: {e}")
            return {"success": False, "error": str(e)}

    def get_incoming_messages(
        self,
        participant_number: str,
        phone_number_id: Optional[str] = None,
        since_iso: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        pid = phone_number_id or TECH_PHONE_NUMBER_ID
        encoded_part = urllib.parse.quote(participant_number)
        url = f"{self.base_url}/v1/messages?phoneNumberId={pid}&participants[]={encoded_part}"
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                messages = res.get("data", [])
                incoming = [m for m in messages if m.get("direction") == "incoming"]
                if since_iso:
                    try:
                        since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                        incoming = [
                            m for m in incoming
                            if m.get("createdAt") and datetime.fromisoformat(m["createdAt"].replace("Z", "+00:00")) > since_dt
                        ]
                    except Exception:
                        incoming = [m for m in incoming if (m.get("createdAt") or "") > since_iso]
                return incoming
        except Exception as e:
            print(f"⚠️ Error fetching messages for {participant_number}: {e}")
            return []


class FieldEdgeBoardManager:
    def __init__(self, headless: bool = HEADLESS):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self.captured_board_data = None

    async def initialize(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080"
            ]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/Los_Angeles",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
        await self.context.route("**/*pendo*", lambda route: route.abort())
        self.page = await self.context.new_page()

    async def login(self):
        if not DASH_EMAIL or not DASH_PASSWORD or not FIELDEDGE_LOGIN_URL:
            print("❌ Error: DASH_EMAIL, DASH_PASSWORD, or FIELDEDGE_LOGIN_URL is missing in environment (.env).")
            return
        print("🔐 Authenticating to FieldEdge...")
        await self.page.goto(FIELDEDGE_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await self.page.wait_for_selector("//input[@name='UserName']", timeout=15000)
            await self.page.fill("//input[@name='UserName']", DASH_EMAIL)
            await self.page.fill("//input[@name='Password']", DASH_PASSWORD)
            await self.page.press("//input[@name='Password']", "Enter")
            await self.page.wait_for_url(lambda u: "Account/Login" not in u, timeout=35000)
            print("✅ FieldEdge login successful.")
        except Exception as e:
            print(f"⚠️ FieldEdge login step: {e}")

    async def navigate_to_board_date(self, date_str: str) -> bool:
        clean_date = date_str.replace("/", "-")
        target_url = f"{FIELDEDGE_BOARD_URL}{clean_date}"
        print(f"🗓️ Navigating to Dispatch Board: {target_url}")
        try:
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            print("⏳ Waiting for Dispatch Board grid to render...")
            try:
                await self.page.wait_for_selector(
                    ".dispatch-drop-area, #fixed-left-column, #dispatch-schedule",
                    state="attached",
                    timeout=25000
                )
            except Exception:
                pass

            try:
                spinners = self.page.locator(".loading, .spinner, .v-spinner, div[class*='loading']:visible")
                if await spinners.count() > 0:
                    await spinners.first.wait_for(state="hidden", timeout=12000)
            except Exception:
                pass

            await self.page.wait_for_timeout(2000)
            return True
        except Exception as e:
            print(f"⚠️ Error navigating to board date {target_url}: {e}")
            return False

    async def resolve_drop_area_for_tech(self, tech_query: str):
        if not tech_query:
            return None, None

        q = tech_query.strip()
        q_norm = normalize_str(q)

        try:
            await self.page.wait_for_selector(".dispatch-drop-area", state="attached", timeout=15000)
        except Exception:
            pass

        matched_entry = TECH_MAPPING.get(q) or TECH_MAPPING.get(q_norm)
        if not matched_entry:
            for k, entry in TECH_MAPPING.items():
                if k.lower() == q_norm or normalize_str(entry.get("name", "")) == q_norm:
                    matched_entry = entry
                    break

        if matched_entry:
            tech_id = matched_entry["id"]
            loc = self.page.locator(f".dispatch-drop-area[data-tech-id='{tech_id}']").first
            try:
                await loc.wait_for(state="attached", timeout=6000)
                return loc, matched_entry.get("name", q)
            except Exception:
                row_idx = matched_entry.get("row")
                if row_idx is not None:
                    row_loc = self.page.locator(".dispatch-drop-area").nth(row_idx)
                    try:
                        await row_loc.wait_for(state="attached", timeout=3000)
                        return row_loc, matched_entry.get("name", q)
                    except Exception:
                        pass

        try:
            tech_rows = self.page.locator("#fixed-left-column .tech-draggable")
            c = await tech_rows.count()
            for i in range(c):
                text = await tech_rows.nth(i).inner_text()
                if q_norm and (q_norm == normalize_str(text) or q_norm in normalize_str(text)):
                    drop = self.page.locator(".dispatch-drop-area").nth(i)
                    if await drop.count() > 0:
                        return drop, text.strip()
        except Exception:
            pass

        return None, None

    async def fetch_board_data(self, date_str: str) -> Dict[str, Any]:
        self.captured_board_data = None

        async def on_response(response):
            if "GetDispatchBoardDataAsync" in response.url and response.status == 200:
                try:
                    self.captured_board_data = await response.json()
                except Exception:
                    pass

        self.page.on("response", on_response)
        await self.navigate_to_board_date(date_str)

        for _ in range(40):
            if self.captured_board_data:
                break
            await asyncio.sleep(0.5)

        return self.captured_board_data or {}

    async def create_note(self, sub_row_key: str, start_time_str: str, note_text: str) -> bool:
        drop_area, tech_display = await self.resolve_drop_area_for_tech(sub_row_key)
        if not drop_area:
            print(f"❌ Could not find drop area for sub-row '{sub_row_key}' on the board.")
            return False

        try:
            await drop_area.scroll_into_view_if_needed(timeout=5000)
            try:
                await self.page.evaluate("""(dropEl) => {
                    if (dropEl) dropEl.scrollIntoView({ behavior: 'instant', block: 'center' });
                }""", await drop_area.element_handle())
                await self.page.wait_for_timeout(200)
            except Exception:
                pass

            dt_match = re.search(r'(\d+):(\d+)\s*(AM|PM)', start_time_str, re.I)
            if dt_match:
                hr = int(dt_match.group(1))
                mn = int(dt_match.group(2))
                ampm = dt_match.group(3).upper()
                if ampm == "PM" and hr != 12:
                    hr += 12
                elif ampm == "AM" and hr == 12:
                    hr = 0
                total_min = hr * 60 + mn
                x_offset = max(10, ((total_min - 300) / 60.0) * 105.0)
            else:
                x_offset = 100

            try:
                await self.page.evaluate("""(xOff) => {
                    const grid = document.querySelector('#grid-container');
                    if (grid) {
                        grid.scrollLeft = Math.max(0, xOff - (grid.clientWidth / 2));
                    }
                }""", x_offset)
                await self.page.wait_for_timeout(300)
            except Exception:
                pass

            box = await drop_area.bounding_box()
            if not box:
                print(f"⚠️ Could not compute bounding box for sub-row '{sub_row_key}'.")
                return False

            start_x = box["x"] + x_offset
            start_y = box["y"] + (box["height"] / 2.0)

            print(f"🖱️ Double-clicking sub-row '{sub_row_key}' at '{start_time_str}' (pos x={start_x:.1f}, y={start_y:.1f})...")

            await self.page.mouse.dblclick(start_x + 15.0, start_y)
            await self.page.wait_for_timeout(800)

            modal_input = self.page.locator(".modal_gRQBi input[name='Description'], input[name='Description']").first

            if await modal_input.count() == 0 or not await modal_input.is_visible():
                await self.page.mouse.move(start_x, start_y)
                await self.page.mouse.down()
                await self.page.wait_for_timeout(100)
                await self.page.mouse.move(start_x + 60.0, start_y, steps=5)
                await self.page.mouse.up()
                await self.page.wait_for_timeout(800)

            await modal_input.wait_for(state="visible", timeout=8000)
            await modal_input.click()
            await modal_input.fill(note_text)

            await self.page.evaluate("""(el) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""", await modal_input.element_handle())

            saved = False
            for sel in [
                ".modal_gRQBi:visible button.confirm_hsagT",
                ".modal_gRQBi:visible button:has-text('Save')",
                "div[class*='modal']:visible button:has-text('Save')",
                "button.confirm_hsagT:visible"
            ]:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    try:
                        await loc.click(timeout=3000)
                        saved = True
                        break
                    except Exception:
                        pass

            if not saved:
                saved = await self.page.evaluate("""() => {
                    const modals = Array.from(document.querySelectorAll('.modal_gRQBi, div[class*="modal"]')).filter(m => m.offsetParent !== null);
                    for (const m of modals) {
                        const btn = Array.from(m.querySelectorAll('button')).find(
                            x => x.innerText && (x.innerText.trim().toLowerCase().includes('save') || x.classList.contains('confirm_hsagT'))
                        );
                        if (btn) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")

            if not saved:
                print("   ⚠️ Could not click Save button in appointment modal.")
                return False

            await self.page.wait_for_timeout(2000)
            print(f"   ✅ Successfully created note '{note_text}' on sub-row '{sub_row_key}'!")
            return True
        except Exception as e:
            print(f"   ❌ Failed to create note on board: {e}")
            return False

    async def delete_note(self, sub_row_key: str, note_text: str) -> bool:
        drop_area, tech_display = await self.resolve_drop_area_for_tech(sub_row_key)
        if not drop_area:
            print(f"❌ Drop area for sub-row '{sub_row_key}' not found on board.")
            return False

        try:
            await drop_area.scroll_into_view_if_needed(timeout=5000)

            appts = drop_area.locator(".appointment-element")
            total_appts = await appts.count()
            if total_appts == 0:
                print(f"ℹ️ No appointment notes found on sub-row '{sub_row_key}' to delete.")
                return True

            target_note = None
            clean_text = note_text.strip().lower()
            for idx in range(total_appts):
                elem = appts.nth(idx)
                txt = (await elem.inner_text()).strip().lower()
                if clean_text in txt or "complete @" in txt:
                    target_note = elem
                    break

            if not target_note:
                target_note = appts.first

            box = await target_note.bounding_box()
            if not box:
                await target_note.scroll_into_view_if_needed()
                box = await target_note.bounding_box()

            if box:
                cx = box['x'] + box['width'] / 2.0
                cy = box['y'] + box['height'] / 2.0
                print(f"🖱️ Clicking appointment note at ({cx:.1f}, {cy:.1f})...")
                await self.page.mouse.click(cx, cy)
            else:
                await target_note.click(force=True)

            await self.page.wait_for_timeout(1000)

            del_clicked = False
            for sel in [
                ".modal_gRQBi:visible button:has-text('Delete')",
                "div[class*='modal']:visible button:has-text('Delete')",
                "button:has-text('Delete'):visible"
            ]:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    try:
                        await loc.click(timeout=3000)
                        del_clicked = True
                        break
                    except Exception:
                        pass

            if not del_clicked:
                del_clicked = await self.page.evaluate("""() => {
                    const modals = Array.from(document.querySelectorAll('.modal_gRQBi, div[class*="modal"]'));
                    for (const m of modals) {
                        if (m.innerText && m.innerText.includes('Scheduled From')) {
                            const btn = Array.from(m.querySelectorAll('button')).find(
                                x => (x.innerText || '').trim().toLowerCase() === 'delete'
                            );
                            if (btn) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""")

            if not del_clicked:
                print(f"❌ Delete button not found in appointment modal.")
                return False

            print("🗑️ Delete button clicked. Confirming deletion...")
            await self.page.wait_for_timeout(1000)

            confirmed = await self.page.evaluate("""() => {
                const popups = Array.from(document.querySelectorAll('.confirmation-popup-container_NiN5O, .modal_gRQBi, div[class*="popup"]'));
                for (const p of popups) {
                    if (p.innerText && (p.innerText.includes('Delete unavailable time') || p.innerText.includes('Are you sure you want to delete'))) {
                        const confirmBtn = Array.from(p.querySelectorAll('button')).find(
                            b => (b.innerText || '').trim().toLowerCase() === 'confirm' || b.classList.contains('confirm_hsagT')
                        );
                        if (confirmBtn) {
                            confirmBtn.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")

            if not confirmed:
                confirm_btn = self.page.locator(".confirmation-popup-container_NiN5O:visible button:has-text('Confirm'), button:has-text('Confirm'):visible").first
                if await confirm_btn.count() > 0 and await confirm_btn.is_visible():
                    await confirm_btn.click(force=True)
                    confirmed = True

            if not confirmed:
                print("❌ Confirmation button could not be clicked.")
                return False

            await self.page.wait_for_timeout(3000)
            remaining_appts = await drop_area.locator(".appointment-element").count()
            if remaining_appts < total_appts or remaining_appts == 0:
                print(f"   ✅ Successfully deleted note '{note_text}' from sub-row '{sub_row_key}'! (Confirmed: {remaining_appts} remaining)")
                return True
            else:
                print(f"⚠️ Note may still be present on sub-row '{sub_row_key}' (Count: {remaining_appts}).")
                return False
        except Exception as e:
            print(f"   ❌ Failed to delete note on board: {e}")
            return False

    async def cleanup(self):
        try:
            if self.page:
                await self.page.close()
            if self.browser:
                await self.browser.close()
            if hasattr(self, "playwright"):
                await self.playwright.stop()
        except Exception:
            pass


class JobCheckinAutomation:
    def __init__(self, dry_run: bool = False, headless: Optional[bool] = None):
        self.dry_run = dry_run
        self.headless = HEADLESS if headless is None else headless
        self.quo = QuoClient()
        self.state: Dict[str, Any] = self.load_state()

    def load_state(self) -> Dict[str, Any]:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"jobs": {}, "last_run": None}

    def save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save state file: {e}")

    @staticmethod
    def classify_priority(task_name: str, priority_name: Optional[str], color_int: Optional[int]) -> str:
        t_upper = (task_name or "").upper()
        p_upper = (priority_name or "").upper()

        if (
            "INTERNAL" in t_upper or
            "INTERNAL" in p_upper or
            "HR" in t_upper or
            "DUMP" in t_upper or
            "BLOCKED" in p_upper or
            "SHOP" in p_upper or
            "GALLONS" in p_upper
        ):
            return "internal"

        if "EXCAVATOR" in p_upper or (isinstance(color_int, int) and (color_int & 0xFF) == 128):
            return "4hr"

        if "CORRECTION" in p_upper or "UPGRADE" in p_upper or color_int == 6547189:
            return "2hr"

        return "30min"

    @staticmethod
    def format_elapsed(minutes: int) -> str:
        if minutes < 60:
            return f"{minutes} minutes" if minutes != 1 else "1 minute"
        hrs = minutes // 60
        mins = minutes % 60
        hr_str = f"{hrs} hour" if hrs == 1 else f"{hrs} hours"
        if not mins:
            return hr_str
        min_str = f"{mins} minute" if mins == 1 else f"{mins} minutes"
        return f"{hr_str} and {min_str}"

    def build_checkin_message(self, elapsed_minutes: int) -> str:
        elapsed_str = self.format_elapsed(elapsed_minutes)
        return (
            f"⏱️ 𝗝𝗢𝗕 𝗖𝗛𝗘𝗖𝗞-𝗜𝗡\n"
            f"You have been on site for {elapsed_str}:\n"
            f"Do you expect to be finished within 30 minutes?\n"
            f"A — Need 1–2 more hours\n"
            f"B — Need 2+ more hours\n"
            f"C — Unsure; I will call within 5min"
        )

    @staticmethod
    def parse_tech_response(text: str, current_dt: Optional[datetime] = None) -> Tuple[Optional[str], Optional[str]]:
        if not text:
            return None, None
        now = current_dt or get_pst_now()
        clean = text.strip().upper()

        if re.search(r'\b(A|OPTION\s*A)\b', clean) or "1-2" in clean or "1 TO 2" in clean or clean == "A":
            est_dt = now + timedelta(hours=1, minutes=30)
            return est_dt.strftime("%I:%M %p"), "Option A (Need 1–2 more hours)"

        if re.search(r'\b(B|OPTION\s*B)\b', clean) or "2+" in clean or "2 MORE" in clean or clean == "B":
            est_dt = now + timedelta(hours=2, minutes=30)
            return est_dt.strftime("%I:%M %p"), "Option B (Need 2+ more hours)"

        if re.search(r'\b(C|OPTION\s*C)\b', clean) or "UNSURE" in clean or "CALL" in clean:
            return None, "Option C (Unsure; will call within 5min)"

        time_match = re.search(r'\b(0?[1-9]|1[0-2])(?::([0-5]\d))?\s*(AM|PM)?\b', clean)
        if time_match:
            hr = int(time_match.group(1))
            mn = int(time_match.group(2) or 0)
            ampm = time_match.group(3)
            if not ampm:
                ampm = "PM" if (now.hour >= 12 or hr < 7) else "AM"
            try:
                parsed_t = datetime.strptime(f"{hr}:{mn:02d} {ampm}", "%I:%M %p")
                return parsed_t.strftime("%I:%M %p"), f"Custom Time Reported ({text.strip()})"
            except Exception:
                pass

        return None, None

    async def run_cycle(self, target_date_str: Optional[str] = None):
        now_pst = get_pst_now()
        date_str = target_date_str or now_pst.strftime("%m/%d/%Y")
        print(f"\n=======================================================")
        print(f"🤖 JOB CHECK-IN AUTOMATION CYCLE: {now_pst.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
        print(f"=======================================================")

        fe_mgr = FieldEdgeBoardManager(headless=self.headless)
        await fe_mgr.initialize()
        await fe_mgr.login()

        print(f"📡 Fetching Dispatch Board data for {date_str}...")
        raw_board = await fe_mgr.fetch_board_data(date_str)
        groups = raw_board.get("Board", {}).get("Groups", [])

        active_jobs_state = self.state.setdefault("jobs", {})
        stats = {"working": 0, "sms_sent": 0, "replies": 0, "notes_created": 0, "notes_deleted": 0}

        for g in groups:
            for row_idx, r in enumerate(g.get("Rows", [])):
                tech_name = r.get("Name") or ""
                tech_id = r.get("EntityID") or ""

                tech_key = None
                for k, v in TECH_MAPPING.items():
                    if v.get("id", "").lower() == tech_id.lower() or normalize_str(v.get("name", "")) == normalize_str(tech_name):
                        tech_key = k
                        break

                if not tech_key:
                    continue

                for s in r.get("ScheduleItems", []):
                    sched_id = s.get("DispatchID") or s.get("EntityID")
                    item_name = (s.get("Name") or "").strip()
                    task_name = (s.get("TaskName") or "").strip()
                    status = (s.get("Status") or s.get("AssignmentTechStatusName") or "").strip()
                    p_col = s.get("PriorityColor")
                    p_info = PRIORITY_LOOKUP.get(p_col) if isinstance(p_col, int) else None
                    p_name = p_info["name"] if p_info else None

                    category = self.classify_priority(task_name, p_name, p_col)
                    if category == "internal":
                        continue

                    tech_phone = TECH_PHONE_MAPPING.get(tech_key)
                    if not tech_phone:
                        continue

                    is_completed = (
                        status.lower() in ("completed", "complete", "closed", "canceled", "cancelled")
                        or s.get("Completed") is True
                        or s.get("AssignmentTechStatus") in (3, 4)
                    )

                    job_entry = active_jobs_state.get(sched_id)

                    if is_completed:
                        if job_entry and job_entry.get("note_created"):
                            sub_row_key = job_entry.get("note_sub_row") or SUB_ROW_MAPPING.get(tech_key)
                            note_desc = f"Complete @ {job_entry.get('note_time')}"
                            print(f"🧹 Job COMPLETED for {tech_name}! Deleting note '{note_desc}' on sub-row '{sub_row_key}'...")
                            if not self.dry_run:
                                del_ok = await fe_mgr.delete_note(sub_row_key, note_desc)
                                if del_ok:
                                    job_entry["note_created"] = False
                                    stats["notes_deleted"] += 1
                        active_jobs_state.pop(sched_id, None)
                        continue

                    is_working = (status.lower() == "working" or s.get("AssignmentTechStatus") == 2)

                    if is_working:
                        stats["working"] += 1
                        if not job_entry:
                            job_entry = {
                                "schedule_id": sched_id,
                                "dispatch_number": s.get("DispatchName"),
                                "work_order_name": item_name,
                                "tech_key": tech_key,
                                "tech_name": tech_name,
                                "tech_phone": tech_phone,
                                "category": category,
                                "status": "Working",
                                "working_started_at": now_pst.isoformat(),
                                "checkin_count": 0,
                                "last_checkin_at": None,
                                "reported_time": None,
                                "note_created": False,
                                "note_time": None,
                            }
                            active_jobs_state[sched_id] = job_entry
                            print(f"🚀 Detected NEW Working job: {item_name} | Tech: {tech_name} | Cat: {category}")

                        started_at = datetime.fromisoformat(job_entry["working_started_at"])
                        elapsed_mins = int((now_pst - started_at).total_seconds() / 60)
                        rule = PRIORITY_CHECKIN_RULES.get(category, PRIORITY_CHECKIN_RULES["30min"])

                        is_due = False
                        if not job_entry.get("reported_time"):
                            if job_entry["checkin_count"] == 0:
                                if elapsed_mins >= rule["initial_delay_mins"]:
                                     is_due = True
                            elif job_entry.get("last_checkin_at"):
                                last_checkin_dt = datetime.fromisoformat(job_entry["last_checkin_at"])
                                mins_since_last = int((now_pst - last_checkin_dt).total_seconds() / 60)
                                if mins_since_last >= rule["interval_mins"]:
                                    is_due = True

                        if is_due:
                            msg = self.build_checkin_message(elapsed_mins)
                            print(f"📱 SENDING JOB CHECK-IN SMS to {tech_name} ({tech_phone}):\n{msg}\n")
                            if not self.dry_run:
                                res = self.quo.send_sms(to_number=tech_phone, message=msg)
                                if res.get("success"):
                                    job_entry["checkin_count"] += 1
                                    job_entry["last_checkin_at"] = now_pst.isoformat()
                                    stats["sms_sent"] += 1
                                    print("   ✅ SMS sent via Quo 'Tech' line!")
                            else:
                                print("   [DRY-RUN] SMS send simulated.")

                        if job_entry.get("last_checkin_at") and not job_entry.get("reported_time"):
                            incoming = self.quo.get_incoming_messages(
                                participant_number=tech_phone,
                                since_iso=job_entry["last_checkin_at"]
                            )
                            for inc in incoming:
                                inc_text = inc.get("text") or inc.get("body") or ""
                                comp_time, reason = self.parse_tech_response(inc_text, now_pst)
                                if comp_time:
                                    print(f"📥 TECH REPLY from {tech_name}: '{inc_text}' -> Complete @ {comp_time} ({reason})")
                                    job_entry["reported_time"] = comp_time
                                    stats["replies"] += 1

                                    sub_row_key = SUB_ROW_MAPPING.get(tech_key)
                                    if sub_row_key:
                                        note_desc = f"Complete @ {comp_time}"
                                        print(f"📝 Creating note '{note_desc}' on sub-row '{sub_row_key}'...")
                                        if not self.dry_run:
                                            note_ok = await fe_mgr.create_note(sub_row_key, comp_time, note_desc)
                                            if note_ok:
                                                job_entry["note_created"] = True
                                                job_entry["note_time"] = comp_time
                                                job_entry["note_sub_row"] = sub_row_key
                                                stats["notes_created"] += 1
                                    break



        self.state["last_run"] = now_pst.isoformat()
        self.save_state()
        await fe_mgr.cleanup()

        print(f"📊 SUMMARY: Working: {stats['working']} | SMS Sent: {stats['sms_sent']} | Replies: {stats['replies']} | Notes Created: {stats['notes_created']} | Notes Deleted: {stats['notes_deleted']}")


def main():
    parser = argparse.ArgumentParser(description="Standalone FieldEdge & Quo Job Check-in Service")
    parser.add_argument("--date", type=str, help="Target date (e.g. '11/30/2026', default: today)")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background")
    parser.add_argument("--poll-interval", type=int, default=60, help="Poll interval in seconds (default: 60)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without sending SMS or modifying board")
    parser.add_argument("--ignore-hours", action="store_true", help="Bypass 7:00 AM - 8:00 PM PST operating hours check")
    parser.add_argument("--headless", action="store_true", default=None, help="Force headless browser execution")
    parser.add_argument("--no-headless", action="store_true", help="Force visible browser window")
    parser.add_argument("--test-sms", type=str, help="Send test SMS to technician (e.g. 'russell')")
    parser.add_argument("--test-note", type=str, help="Test creating 'Complete @ [Time]' note for tech (e.g. 'russell', 'mackie')")
    parser.add_argument("--test-delete-note", type=str, help="Test deleting 'Complete @ [Time]' note for tech (e.g. 'russell')")
    parser.add_argument("--time", type=str, default="10:30 AM", help="Time to use for test note (default: '10:30 AM')")
    args = parser.parse_args()

    headless = None
    if args.no_headless:
        headless = False
    elif args.headless:
        headless = True
    else:
        headless = HEADLESS

    automation = JobCheckinAutomation(dry_run=args.dry_run, headless=headless)

    if args.test_sms:
        tech_key = args.test_sms.lower()
        phone = TECH_PHONE_MAPPING.get(tech_key)
        if not phone:
            print(f"❌ Unknown technician '{tech_key}'. Available: {list(TECH_PHONE_MAPPING.keys())}")
            return
        msg = automation.build_checkin_message(30)
        print(f"🧪 Sending TEST SMS to '{tech_key}' at {phone} from Tech line {TECH_PHONE_NUMBER}...")
        res = automation.quo.send_sms(to_number=phone, message=msg)
        print("Result:", res)
        return

    if args.test_note:
        tech_key = args.test_note.lower()
        sub_row = SUB_ROW_MAPPING.get(tech_key, tech_key)
        note_text = f"Complete @ {args.time}"
        target_date = args.date or get_pst_now().strftime("%m/%d/%Y")
        print(f"🧪 Testing NOTE CREATION for '{tech_key}' on sub-row '{sub_row}': '{note_text}' for {target_date}...")
        
        async def _test_create():
            fe = FieldEdgeBoardManager(headless=headless)
            await fe.initialize()
            await fe.login()
            nav_ok = await fe.navigate_to_board_date(target_date)
            if not nav_ok:
                print("❌ Failed to load board grid.")
                await fe.cleanup()
                return
            ok = await fe.create_note(sub_row, args.time, note_text)
            print(f"Result: {'SUCCESS' if ok else 'FAILED'}")
            await asyncio.sleep(4)
            await fe.cleanup()

        asyncio.run(_test_create())
        return

    if args.test_delete_note:
        tech_key = args.test_delete_note.lower()
        sub_row = SUB_ROW_MAPPING.get(tech_key, tech_key)
        note_text = f"Complete @ {args.time}"
        target_date = args.date or get_pst_now().strftime("%m/%d/%Y")
        print(f"🧪 Testing NOTE DELETION for '{tech_key}' on sub-row '{sub_row}': '{note_text}' for {target_date}...")

        async def _test_delete():
            fe = FieldEdgeBoardManager(headless=headless)
            await fe.initialize()
            await fe.login()
            nav_ok = await fe.navigate_to_board_date(target_date)
            if not nav_ok:
                print("❌ Failed to load board grid.")
                await fe.cleanup()
                return
            ok = await fe.delete_note(sub_row, note_text)
            print(f"Result: {'SUCCESS' if ok else 'FAILED'}")
            await asyncio.sleep(3)
            await fe.cleanup()

        asyncio.run(_test_delete())
        return

    if args.daemon:
        start_h = int(os.getenv("OPERATING_START_HOUR_PST", "7"))
        end_h = int(os.getenv("OPERATING_END_HOUR_PST", "20"))
        print(f"🔄 Starting Job Check-In Automation DAEMON (Interval: {args.poll_interval}s | Operating Hours: {start_h}:00 AM - {end_h % 12 or 12}:00 PM PST)...")
        last_logged_outside = 0.0
        while True:
            now_pst = get_pst_now()
            is_active, window_desc = is_within_operating_hours(now_pst)

            if not is_active and not args.ignore_hours:
                if time.time() - last_logged_outside > 600:
                    print(f"⏸️ [{now_pst.strftime('%I:%M:%S %p PST')}] Outside operating hours ({window_desc}). Monitoring is paused until 7:00 AM PST.")
                    last_logged_outside = time.time()
                time.sleep(min(args.poll_interval, 60))
                continue

            last_logged_outside = 0.0
            try:
                asyncio.run(automation.run_cycle(target_date_str=args.date))
            except Exception as e:
                print(f"⚠️ Error during cycle: {e}")
            time.sleep(args.poll_interval)
    else:
        now_pst = get_pst_now()
        is_active, window_desc = is_within_operating_hours(now_pst)
        if not is_active and not args.ignore_hours:
            print(f"ℹ️ Notice: Current time ({now_pst.strftime('%I:%M:%S %p PST')}) is outside standard operating hours ({window_desc}). Running single cycle...")
        asyncio.run(automation.run_cycle(target_date_str=args.date))


if __name__ == "__main__":
    main()
