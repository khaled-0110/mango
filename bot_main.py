
import os
import re
import json
import asyncio
import requests
import discord
import threading
import time
import pytz
import traceback
import html
import logging
import aiohttp
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from discord.ext import tasks
from discord import app_commands
from playwright.async_api import async_playwright

download_lock = asyncio.Lock()
# ==============================================================================
# 1. LOGGING & ENVIRONMENT
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
load_dotenv()

# ==============================================================================
# 2. CONFIGURATION
# ==============================================================================
# --- Academy Results ---
STUDENT_MAPPING = {
    813895808674955284: "32502664",
    1116865121428451399: "32501089",
    468519467203166250: "32500622",
    534807654917341205: "32500116",
    767428264842625044: "32500072",
    693791864406540339: "32500144"
}
RESULTS_CACHE_FILE = "sent_results.json"
ENABLE_SCRAPER = False  # Set to True to enable Playwright results scanning

# --- Core Tokens & IDs ---
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
DM_LOG_CHANNEL_ID = 123456789012345678  # <-- REPLACE WITH YOUR ACTUAL DM LOG CHANNEL ID

CAIRO_TZ = pytz.timezone('Africa/Cairo')
DEAD_GPA_ROLE_ID = 1431231714440777808

# --- Channel Mapping ---
TARGET_CHANNEL_NAME = "shared-videos"
LISTEN_CHANNEL_NAME = "links-inbox"
SUBJECT_CHANNELS = {
    "principles of macroeconomics": "💦principles-of-macro-ejaculation",
    "principles of investment": "📉principles-of-in-sex-ment",
    "databases": "💃data-bitches",
    "design of computer programs": "🔞design-of-computer-pornography",
    "information technology infra structure": "💥it-infra-destruction",
    "web design": "🍆web-dick-sign"
}

# --- File Paths ---
MOODLE_CACHE = "moodle_seen_items.json"
ASSIGNMENTS_FILE = "assignments.json"
LIBRARY_FILE = "moodle_files_library.json"
CONFIG_FILE = "system_config.json"
ALERTS_FILE = "sent_alerts.json"
MOODLE_ALERTS_FILE = "moodle_alerts_sent.json"
TEMP_DIR = Path(__file__).parent / "temp_files"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ==============================================================================
# 3. UTILITIES
# ==============================================================================
def normalize_subject(name: str) -> str:
    return " ".join(str(name).split()).lower().strip()

def load_data(path: str):
    if not os.path.exists(path):
        return {} if any(x in str(path) for x in ["seen", "library", "config", "alerts"]) else []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if any(x in str(path) for x in ["seen", "library", "config", "alerts"]):
                return data if isinstance(data, dict) else {}
            return data if isinstance(data, list) else []
    except Exception:
        return {} if any(x in str(path) for x in ["seen", "library", "config", "alerts"]) else []

def save_data(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save {path}: {e}")

def get_cairo_9am_timestamp(date_str: str) -> int:
    dt_naive = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
    dt_cairo = CAIRO_TZ.localize(dt_naive)
    return int(dt_cairo.timestamp())

def get_status_ui(target_ts: int):
    now_ts = int(time.time())
    diff = target_ts - now_ts
    if diff < 0:
        return discord.Color.red(), "🚨 OVERDUE"
    elif diff < 86400:
        return discord.Color.orange(), "⚠️ DUE SOON"
    else:
        return discord.Color.green(), "✅ ON TRACK"
# ==============================================================================
# 4. SMART FALLBACK DOWNLOAD LOGIC
# ==============================================================================
def extract_instagram_shortcode(url: str):
    match = re.search(r'(?:reel|p)/([A-Za-z0-9_-]+)', url)
    return match.group(1) if match else None

def format_count(count):
    """Formats numbers like 43000 to 43.0K"""
    if count is None:
        return "0"
    if count >= 1000000:
        return f"{count/1000000:.1f}M"
    if count >= 1000:
        return f"{count/1000:.1f}K"
    return str(count)

def get_video_metadata(url: str):
    """Fetches video metadata (author, stats, description) using yt-dlp"""
    try:
        result = subprocess.run(
            # ADDED: --cookies argument
            ["yt-dlp", "-J", "--no-warnings", "--cookies", "/home/GIGA/cookies.txt", url],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            logger.error(f"yt-dlp failed for {url}. Error: {result.stderr.strip()}")
            return None
    except Exception as e:
        logger.error(f"yt-dlp metadata exception: {e}")
        return None

def download_video_file(url: str, filepath: str):
    cmd = ["yt-dlp", "-f", "best", "-o", filepath, "--no-warnings"]

    # Pass proxy if downloading from YouTube
    if "youtube.com" in url or "youtu.be" in url:
        # Example proxy usage (replace with your proxy address if available)
        cmd.extend(["--proxy", "http://your_proxy_ip:port"])

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"yt-dlp download error: {e}")
        return False

async def download_youtube_via_cobalt(url: str, filepath: str) -> bool:
    """Downloads YouTube videos using the cobalt.tools API to bypass Azure IP bans"""
    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        data = {
            "url": url,
            "videoQuality": "720", # 720p is a good balance of quality and file size (<20MB)
            "audioFormat": "mp3",
            "filenamePattern": "basic"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.cobalt.tools/", json=data, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("status") == "stream" or result.get("status") == "redirect":
                        download_url = result.get("url")

                        # Download the actual file
                        async with session.get(download_url, timeout=60) as file_resp:
                            if file_resp.status == 200:
                                with open(filepath, "wb") as f:
                                    f.write(await file_resp.read())
                                return True
        return False
    except Exception as e:
        logger.error(f"Cobalt download error for {url}: {e}")
        return False


# ==============================================================================
# 5. INTERACTIVE UI
# ==============================================================================
class FileSelectionViewV2(discord.ui.View):
    def __init__(self, lookup, options):
        super().__init__(timeout=180)
        self.lookup = lookup
        self.selected_indices = []
        self.file_selector = discord.ui.Select(
            placeholder="Step 1: Pick up to 10 files...",
            min_values=1,
            max_values=min(10, len(options)),
            options=options,
            custom_id="file_select_v2"
        )
        self.file_selector.callback = self.pick_files_callback
        self.add_item(self.file_selector)

    async def pick_files_callback(self, interaction: discord.Interaction):
        self.selected_indices = self.file_selector.values
        self.confirm_btn.disabled = False
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Step 2: Download Selected", style=discord.ButtonStyle.success, emoji="📥", disabled=True, custom_id="download_btn_v2")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        success_count = 0
        loop = asyncio.get_event_loop()
        for idx in self.selected_indices:
            f_data = self.lookup.get(idx)
            if not f_data: continue
            try:
                res = await loop.run_in_executor(None, lambda u=f_data['url']: requests.get(u, timeout=20))
                path = TEMP_DIR / f_data['name']
                with open(path, "wb") as fh: fh.write(res.content)
                desc = f_data.get('description', '')
                content_msg = f"📄 **Archive Request:** {f_data['name']}"
                if desc: content_msg += f"\n\n📝 **Instructions:**\n{desc}"
                await interaction.user.send(content=content_msg, file=discord.File(path))
                path.unlink()
                success_count += 1
            except Exception: pass
        await interaction.followup.send(f"✅ Successfully sent {success_count} files to your DMs!", ephemeral=True)
        self.stop()

class PermanentFileViewV2(discord.ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot = bot_instance

    @discord.ui.select(
        custom_id="permanent_subject_select_v2",
        placeholder="📂 Browse Subject Archives...",
        options=[discord.SelectOption(label=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()]
    )
    async def select_subject(self, interaction: discord.Interaction, select: discord.ui.Select):
        library = load_data(LIBRARY_FILE)
        subject = select.values[0]
        files = library.get(subject, [])
        if not files:
            return await interaction.response.send_message(f"No files found for **{subject}** yet.", ephemeral=True)
        sorted_files = sorted(files, key=lambda x: x['timestamp'], reverse=True)[:100]
        file_lookup = {str(i): f for i, f in enumerate(sorted_files)}
        options = []
        for i, f in file_lookup.items():
            date_str = datetime.fromtimestamp(f['timestamp']).strftime('%d/%b %H:%M')
            has_desc = " 📝" if f.get('description') else ""
            label = f"{f['name'][:90]}{has_desc}"
            options.append(discord.SelectOption(label=label, description=f"Uploaded: {date_str}", value=i))
        await interaction.response.send_message(
            content=f"### 📂 {subject.upper()} Archive (Showing latest 100)\nSelect up to 10 files to receive them in DMs.",
            view=FileSelectionViewV2(file_lookup, options),
            ephemeral=True
        )

# ==============================================================================
# 6. MAIN BOT ENGINE
# ==============================================================================
class GigaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)
        self._moodle_alert_lock = asyncio.Lock()
        self._moodle_history_cache = {}

    async def setup_hook(self):
        if ENABLE_SCRAPER:
            self.check_academy_results.start()
        else:
            logger.info("⏸️ Scraper task is currently DISABLED. Skipping Playwright initialization.")

        self.add_view(PermanentFileViewV2(self))
        self.monitor_moodle.start()
        self.monitor_quizzes.start()
        self.precision_scheduler.start()

        # --- GLOBAL COMMAND SYNC ONLY ---
        # This registers commands globally, making them available in DMs and all servers.
        try:
            synced_commands = await self.tree.sync()
            logger.info(f"✅ Global command sync complete ({len(synced_commands)} commands registered).")
        except Exception as e:
            logger.error(f"❌ Command sync failed: {e}")

    async def _moodle_recent_alerts(self, channel):
        channel_id = channel.id
        if channel_id not in self._moodle_history_cache:
            alerts = []
            try:
                async for message in channel.history(limit=None):
                    if not self.user or message.author.id != self.user.id:
                        continue
                    for embed in message.embeds:
                        alerts.append({
                            "url": embed.url,
                            "footer": embed.footer.text or "",
                            "fields": {field.name: field.value for field in embed.fields},
                        })
            except discord.Forbidden:
                logger.error(
                    "Cannot reconcile old Moodle alerts in #%s; grant the bot Read Message History.",
                    channel.name,
                )
                raise
            self._moodle_history_cache[channel_id] = alerts
        return self._moodle_history_cache[channel_id]

    async def _send_moodle_alert(
        self, channel, item_id, embed, content, item_url=None,
        item_name=None, item_subject=None, due_timestamp=None,
        stacked_task=None, stacked_title=None,
    ):
        async with self._moodle_alert_lock:
            sent_alerts = load_data(MOODLE_ALERTS_FILE)
            if item_id in sent_alerts:
                return False

            recent_alerts = await self._moodle_recent_alerts(channel)
            for alert in recent_alerts:
                if item_id in alert["footer"]:
                    sent_alerts[item_id] = True
                    save_data(MOODLE_ALERTS_FILE, sent_alerts)
                    logger.info("Moodle alert already exists in Discord history: %s", item_id)
                    return False
                if item_url and alert["url"] and alert["url"].rstrip("/") == item_url.rstrip("/"):
                    sent_alerts[item_id] = True
                    save_data(MOODLE_ALERTS_FILE, sent_alerts)
                    logger.info("Moodle alert already exists in Discord history: %s", item_id)
                    return False
                if item_name and due_timestamp:
                    details = alert["fields"].get("📝 Details", "")
                    submission = alert["fields"].get("⏳ Submission", "")
                    subject = alert["fields"].get("📘 Subject", "")
                    if (
                        item_name.casefold() in details.casefold()
                        and f"<t:{due_timestamp}:F>" in submission
                        and (not item_subject or item_subject.casefold() in subject.casefold())
                    ):
                        sent_alerts[item_id] = True
                        save_data(MOODLE_ALERTS_FILE, sent_alerts)
                        logger.info("Moodle alert already exists in Discord history: %s", item_id)
                        return False

            if stacked_task:
                await self.send_stacked_embed(channel, stacked_task, stacked_title)
            else:
                await channel.send(content=content, embed=embed)
            sent_alerts[item_id] = True
            save_data(MOODLE_ALERTS_FILE, sent_alerts)
            logger.info("Sent Moodle alert: %s", item_id)
            return True

    @tasks.loop(minutes=30)
    async def monitor_moodle(self):
        await self.wait_until_ready()
        token, base_url = os.getenv("MOODLE_TOKEN"), os.getenv("MOODLE_URL")
        if not token or not base_url: return
        api_url = f"{base_url}/webservice/rest/server.php"
        loop = asyncio.get_event_loop()

        try:
            seen_data = load_data(MOODLE_CACHE)
            library = load_data(LIBRARY_FILE)
            params = {'wstoken': token, 'wsfunction': 'core_course_get_enrolled_courses_by_timeline_classification', 'moodlewsrestformat': 'json', 'classification': 'all'}
            res = (await loop.run_in_executor(None, lambda: requests.get(api_url, params=params))).json()
            courses = res.get('courses', []) if isinstance(res, dict) else []

            for course in courses:
                c_id = str(course.get('id'))
                c_name = course.get('fullname', 'Unknown')
                sec_params = {'wstoken': token, 'wsfunction': 'core_course_get_contents', 'moodlewsrestformat': 'json', 'courseid': c_id}
                sec_res = (await loop.run_in_executor(None, lambda: requests.get(api_url, params=sec_params))).json()
                if not isinstance(sec_res, list): continue

                if c_id not in seen_data: seen_data[c_id] = []
                subj_key = self.match_subj(c_id, courses)
                if subj_key not in library: library[subj_key] = []

                for sec in sec_res:
                    for mod in sec.get('modules', []):
                        m_id = str(mod.get('id'))
                        is_new = m_id not in seen_data[c_id]
                        if mod.get("modname") == "assign":
                            guild = self.get_guild(GUILD_ID)
                            channel_name = SUBJECT_CHANNELS.get(subj_key)
                            chan = (
                                discord.utils.get(guild.text_channels, name=channel_name)
                                if guild and channel_name
                                else None
                            )
                            if not chan:
                                logger.warning(
                                    "No Discord channel for Moodle assignment in course %s (subject key: %s)",
                                    c_name,
                                    subj_key,
                                )
                                continue

                            assignment_name = str(mod.get("name") or "New assignment")
                            raw_text = mod.get("description") or mod.get("intro") or ""
                            if not isinstance(raw_text, str):
                                raw_text = ""
                            description = " ".join(
                                html.unescape(re.sub(r"<[^<]+?>", "", raw_text)).split()
                            )
                            assignment_url = mod.get("url") or (
                                f"{base_url.rstrip('/')}/mod/assign/view.php?id={m_id}"
                            )
                            embed = discord.Embed(
                                title=f"🆕 Moodle assignment: {assignment_name}"[:256],
                                url=assignment_url,
                                description=description[:4096] or "No description provided.",
                                color=discord.Color.gold(),
                            )
                            embed.add_field(name="Course", value=str(c_name)[:1024], inline=False)
                            dates = mod.get("dates")
                            if isinstance(dates, list):
                                for date in dates:
                                    if not isinstance(date, dict):
                                        continue
                                    if "due" not in str(date.get("label", "")).lower():
                                        continue
                                    try:
                                        due_timestamp = int(date.get("timestamp"))
                                    except (TypeError, ValueError):
                                        continue
                                    if due_timestamp > 0:
                                        embed.add_field(
                                            name="Due",
                                            value=f"<t:{due_timestamp}:F> (<t:{due_timestamp}:R>)",
                                            inline=False,
                                        )
                                        break
                            item_id = f"assign_{c_id}_{m_id}"
                            embed.set_footer(text=f"Moodle item: {item_id}")
                            await self._send_moodle_alert(
                                chan,
                                item_id,
                                embed,
                                f"<@&{DEAD_GPA_ROLE_ID}> New Moodle assignment posted:",
                                item_url=assignment_url,
                            )

                        if is_new:
                            if mod.get('modname') == 'resource':
                                contents = mod.get('contents', [])
                                if contents:
                                    f_info = contents[0]
                                    f_url = f"{f_info.get('fileurl')}&token={token}"
                                    f_name = f_info.get('filename')
                                    raw_text = next((mod.get(k) for k in ['intro', 'description', 'content', 'summary'] if mod.get(k)), "")
                                    clean_desc = " ".join(html.unescape(re.sub('<[^<]+?>', '', raw_text)).split()) if raw_text else ""
                                    existing_file = next((f for f in library[subj_key] if f['name'] == f_name), None)
                                    if existing_file:
                                        if not existing_file.get('description') and clean_desc:
                                            existing_file['description'] = clean_desc
                                    else:
                                        library[subj_key].append({"name": f_name, "url": f_url, "timestamp": time.time(), "description": clean_desc})
                                        await self.process_moodle_upload(c_name, f_name, f_url, clean_desc)
                            elif subj_key == "field training 2" and mod.get("modname") != "assign":
                                mod_name = mod.get('modname', 'Activity')
                                mod_url = mod.get('url', '')
                                actual_url = mod['contents'][0].get('fileurl', mod_url) if mod_name == 'url' and mod.get('contents') else mod_url
                                raw_text = mod.get('description', '') or mod.get('intro', '')
                                clean_desc = " ".join(html.unescape(re.sub('<[^<]+?>', '', raw_text)).split()) if raw_text else ""
                                details = f"🔗 **Link:** [Open Activity]({actual_url})"
                                if clean_desc: details += f"\n\n📝 **Details:**\n{clean_desc[:500]}"
                                task = {"subject": subj_key, "timestamp": int(time.time()), "type": mod_name.capitalize(), "details": details, "image_urls": []}
                                chan = discord.utils.get(self.get_guild(GUILD_ID).text_channels, name=SUBJECT_CHANNELS.get(subj_key))
                                if chan: await self.send_stacked_embed(chan, task, f"🆕 NEW {mod_name.upper()} DETECTED")
                        if m_id not in seen_data[c_id]: seen_data[c_id].append(m_id)

            save_data(MOODLE_CACHE, seen_data)
            save_data(LIBRARY_FILE, library)
            await self.refresh_master_embed()
        except Exception:
            logger.error(f"Moodle Scan Error:\n{traceback.format_exc()}")

    @tasks.loop(minutes=10)
    async def monitor_quizzes(self):
        await self.wait_until_ready()
        token, base_url = os.getenv("MOODLE_TOKEN"), os.getenv("MOODLE_URL")
        if not token or not base_url: return
        api_url = f"{base_url}/webservice/rest/server.php"
        loop = asyncio.get_event_loop()
        try:
            info = (await loop.run_in_executor(None, lambda: requests.get(api_url, params={'wstoken': token, 'wsfunction': 'core_webservice_get_site_info', 'moodlewsrestformat': 'json'}))).json()
            u_id = info.get('userid')
            c_res = (await loop.run_in_executor(None, lambda: requests.get(api_url, params={'wstoken': token, 'wsfunction': 'core_enrol_get_users_courses', 'moodlewsrestformat': 'json', 'userid': u_id}))).json()
            course_ids = [c['id'] for c in c_res if isinstance(c, dict)]
            q_res = (await loop.run_in_executor(None, lambda: requests.get(api_url, params=[('wstoken', token), ('wsfunction', 'mod_quiz_get_quizzes_by_courses'), ('moodlewsrestformat', 'json')] + [('courseids[]', cid) for cid in course_ids]))).json()
            data = load_data(ASSIGNMENTS_FILE)
            existing = {t.get('moodle_id') for t in data if t.get('moodle_id')}
            quizzes = q_res.get('quizzes', []) if isinstance(q_res, dict) else []
            guild = self.get_guild(GUILD_ID)
            for q in quizzes:
                if not isinstance(q, dict) or q.get('id') is None:
                    continue
                m_id = f"quiz_{q['id']}"
                quiz_name = str(q.get('name') or "Moodle quiz")
                subj = self.match_subj(q.get('course'), c_res)
                chan = (
                    discord.utils.get(guild.text_channels, name=SUBJECT_CHANNELS.get(subj))
                    if guild
                    else None
                )
                if not chan:
                    logger.warning(
                        "No Discord channel for Moodle quiz %s (subject key: %s)",
                        quiz_name,
                        subj,
                    )
                    continue

                try:
                    due_timestamp = int(q.get('timeclose') or 0)
                except (TypeError, ValueError):
                    due_timestamp = 0

                if due_timestamp > int(time.time()):
                    task = {
                        "subject": subj,
                        "timestamp": due_timestamp,
                        "type": "Moodle Quiz",
                        "details": f"📝 **{quiz_name}**",
                        "moodle_id": m_id,
                        "moodle_url": q.get('url'),
                    }
                    if m_id not in existing:
                        data.append(task)
                        existing.add(m_id)
                else:
                    task = None

                description = q.get('intro') or ""
                if not isinstance(description, str):
                    description = ""
                description = " ".join(
                    html.unescape(re.sub(r"<[^<]+?>", "", description)).split()
                )
                quiz_url = q.get('url')
                if not isinstance(quiz_url, str) or not quiz_url:
                    quiz_url = None
                embed = discord.Embed(
                    title=f"🆕 Moodle quiz: {quiz_name}"[:256],
                    url=quiz_url,
                    description=description[:4096] or "Quiz posted in Moodle.",
                    color=discord.Color.purple(),
                )
                embed.add_field(name="Course", value=str(subj)[:1024], inline=False)
                if due_timestamp > 0:
                    due_label = "Due" if due_timestamp > time.time() else "Due (already passed)"
                    embed.add_field(
                        name=due_label,
                        value=f"<t:{due_timestamp}:F> (<t:{due_timestamp}:R>)",
                        inline=False,
                    )
                else:
                    embed.add_field(name="Due", value="No closing time set.", inline=False)
                embed.set_footer(text=f"Moodle item: {m_id}")
                await self._send_moodle_alert(
                    chan,
                    m_id,
                    embed,
                    f"<@&{DEAD_GPA_ROLE_ID}> New Moodle quiz: **{quiz_name}**",
                    item_name=quiz_name,
                    item_subject=subj,
                    due_timestamp=due_timestamp or None,
                    stacked_task=task,
                    stacked_title="🆕 NEW MOODLE QUIZ",
                )
            save_data(ASSIGNMENTS_FILE, data)
        except Exception:
            logger.error(f"Quiz Monitor Error:\n{traceback.format_exc()}")

    @tasks.loop(minutes=1)
    async def precision_scheduler(self):
        await self.wait_until_ready()
        data = load_data(ASSIGNMENTS_FILE)
        now = int(time.time())
        c_now = datetime.now(CAIRO_TZ)
        guild = self.get_guild(GUILD_ID)
        if not guild: return
        rem = []
        sent_alerts = load_data(ALERTS_FILE)
        if not isinstance(sent_alerts, dict): sent_alerts = {}
        for t in data:
            target = t.get('timestamp')
            if not target or (now - target > 172800): continue
            chan = discord.utils.get(guild.text_channels, name=SUBJECT_CHANNELS.get(t['subject']))
            if not chan:
                rem.append(t)
                continue
            if c_now.hour == 9 and c_now.minute <= 1:
                alert_key_daily = f"daily_{t['subject']}_{target}_{c_now.strftime('%Y-%m-%d')}"
                if alert_key_daily not in sent_alerts:
                    await self.send_stacked_embed(chan, t, "☀️ DAILY REMINDER", include_images=True)
                    sent_alerts[alert_key_daily] = True
            diff = target - now
            alert_key_90 = f"90min_{t['subject']}_{target}"
            if 5340 <= diff <= 5460 and alert_key_90 not in sent_alerts:
                await self.send_stacked_embed(chan, t, "⏰ FINAL 90-MIN WARNING", include_images=True)
                sent_alerts[alert_key_90] = True
            rem.append(t)
        save_data(ASSIGNMENTS_FILE, rem)
        save_data(ALERTS_FILE, sent_alerts)

    def match_subj(self, c_id, courses):
        for c in courses:
            if str(c.get('id')) == str(c_id):
                name = normalize_subject(c.get('fullname', ''))
                for key in SUBJECT_CHANNELS.keys():
                    if key in name:
                        return key
        # Updated fallback to a valid new subject
        return "web design"

    async def refresh_master_embed(self):
        config = load_data(CONFIG_FILE)
        if not config: return
        channel_id = config.get('request_channel_id')
        message_id = config.get('master_message_id')
        if not channel_id or not message_id: return
        try:
            channel = self.get_channel(channel_id)
            if not channel: return
            msg = await channel.fetch_message(message_id)
            library = load_data(LIBRARY_FILE)
            total = sum(len(v) for v in library.values())
            embed = discord.Embed(
                title="🏛️ Central Study Archive",
                description=(
                    "Welcome to the **Mango Man** file repository!\n\n"
                    "📂 **How to use:**\n"
                    "1. Select a **Subject** from the dropdown below.\n"
                    "2. Choose up to **10 files** you need.\n"
                    "3. Click **Download** to receive them in your DMs.\n\n"
                    f"📊 **Current Status:** {total} materials archived.\n"
                    f"🔄 **Last Updated:** <t:{int(time.time())}:R>"
                ),
                color=discord.Color.blue()
            )
            embed.set_thumbnail(url="https://i.ibb.co/Q7Sss5ps/mangoman.gif")
            embed.set_footer(text="Mango Man v1.0")
            await msg.edit(embed=embed, view=PermanentFileViewV2(self))
            logger.info("✅ Archive embed refreshed successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to refresh archive embed: {e}")

    async def process_moodle_upload(self, course_name, f_name, url, description=""):
        guild = self.get_guild(GUILD_ID)
        chan = discord.utils.get(guild.text_channels, name=SUBJECT_CHANNELS.get(normalize_subject(course_name)))
        if chan:
            try:
                loop = asyncio.get_event_loop()
                res = await loop.run_in_executor(None, lambda u=url: requests.get(u, timeout=20))
                path = TEMP_DIR / f_name
                with open(path, "wb") as f: f.write(res.content)
                content = f"📚 New material for **{course_name}**!\n<@&{DEAD_GPA_ROLE_ID}>"
                if description: content += f"\n\n📝 **Professor's Note:**\n{description}"
                await chan.send(content=content, file=discord.File(path))
                path.unlink()
            except Exception as e:
                logger.error(f"Failed to process Moodle upload: {e}")

    async def send_stacked_embed(self, channel, task, title, include_images=False):
        col, stat = get_status_ui(task['timestamp'])
        embeds = []
        main = discord.Embed(title=title, url=task.get('moodle_url'), color=col)
        main.add_field(name="📘 Subject", value=f"**{task['subject'].upper()}**", inline=False)
        main.add_field(name="📊 Status", value=f"**{stat}**", inline=True)
        main.add_field(name="⏳ Submission", value=f"<t:{task['timestamp']}:F>\n(<t:{task['timestamp']}:R>)", inline=False)
        main.add_field(name="📝 Details", value=task.get('details', 'No details'), inline=False)
        if task.get('moodle_id'):
            main.set_footer(text=f"Moodle item: {task['moodle_id']}")
        imgs = task.get('image_urls', []) if include_images else []
        if imgs:
            main.set_image(url=imgs[0])
            embeds.append(main)
            for u in imgs[1:]:
                extra = discord.Embed(color=col)
                extra.set_image(url=u)
                embeds.append(extra)
        else:
            embeds.append(main)
        await channel.send(content=f"<@&{DEAD_GPA_ROLE_ID}> Reminder Update!", embeds=embeds)

    async def process_media(self, ctx, url, is_slash=False):
        if is_slash:
            await ctx.response.defer()
        else:
            await ctx.add_reaction("⏳")

        loop = asyncio.get_event_loop()

        # Determine platform for styling
        if "tiktok.com" in url:
            platform = "TikTok"
            platform_emoji = "🎵"
            color = discord.Color.teal()
        elif "instagram.com" in url:
            platform = "Instagram"
            platform_emoji = "📸"
            color = discord.Color.magenta()
        elif "facebook.com" in url or "fb.watch" in url:
            platform = "Facebook"
            platform_emoji = "📘"
            color = discord.Color.blue()
        elif "youtube.com" in url or "youtu.be" in url:
            platform = "YouTube"
            platform_emoji = "▶️"
            color = discord.Color.red()
        else:
            platform = "Video"
            platform_emoji = "🔗"
            color = discord.Color.greyple()

        # 1. Fetch Metadata
        metadata = await loop.run_in_executor(None, get_video_metadata, url)
        if not metadata:
            msg = "❌ Could not fetch video info. The link may be private, expired, or unsupported."
            return await ctx.followup.send(msg) if is_slash else await ctx.reply(msg)

        # Extract info
        author = metadata.get('uploader', 'Unknown')
        description = (metadata.get('description') or metadata.get('title', 'No description')).strip()
        if len(description) > 1000:
            description = description[:997] + "..."

        likes = format_count(metadata.get('like_count', 0))
        comments = format_count(metadata.get('comment_count', 0))
        shares = format_count(metadata.get('repost_count', 0) or metadata.get('share_count', 0))

        clean_url = url.split('?')[0]

        # 2. Download the file (Protected by lock to prevent RAM spikes)
        async with download_lock:
            path = TEMP_DIR / f"vid_{int(time.time())}.mp4"

            # Route YouTube to Cobalt (bypasses Azure IP bans), use yt-dlp for the rest
            if "youtube.com" in url or "youtu.be" in url:
                success = await download_youtube_via_cobalt(url, str(path))
            else:
                success = await loop.run_in_executor(None, download_video_file, url, str(path))

        # Fallback if download fails but metadata succeeded
        if not success or not path.exists():
            embed = discord.Embed(title=f"{platform_emoji} {platform} Video", description=description or "No description", color=color)
            embed.add_field(name="👤 Author", value=author, inline=True)
            embed.add_field(name="📊 Stats", value=f"❤️ {likes} | 💬 {comments} | 🔁 {shares}", inline=False)
            embed.url = clean_url

            if is_slash:
                await ctx.followup.send(content="⚠️ Download failed, but here is the video info:", embed=embed)
            else:
                await ctx.reply(content="⚠️ Download failed, but here is the video info:", embed=embed)
                await ctx.add_reaction("❌")
            return

        # 3. Check file size and build the SUCCESS embed
        file_size_mb = path.stat().st_size / (1024 * 1024)

        # Build the clean embed (Putting links inside the embed prevents Discord from auto-generating messy preview boxes)
        embed = discord.Embed(
            title=f"{platform_emoji} {platform} Video",
            description=description if description else "No description available.",
            color=color,
            url=clean_url
        )
        embed.add_field(name="👤 Author", value=f"[{author}]({clean_url})", inline=True)
        embed.add_field(name="📊 Stats", value=f"❤️ {likes}  •  💬 {comments}  •  🔁 {shares}", inline=True)
        embed.add_field(name="💾 File Size", value=f"**{file_size_mb:.1f} MB**", inline=False)
        embed.set_footer(text="Powered by Mango Man")

        # 4. Create interactive buttons
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=f"Open in {platform}", url=clean_url, style=discord.ButtonStyle.link, emoji=platform_emoji))

        # Try to get a direct download link for the button using yt-dlp (Async safe)
        direct_url = clean_url
        try:
            res = await loop.run_in_executor(
                None,
                lambda: subprocess.run(["yt-dlp", "-g", "--no-warnings", url], capture_output=True, text=True, timeout=15)
            )
            if res.returncode == 0 and res.stdout.strip():
                direct_url = res.stdout.strip().split('\n')[0]
        except Exception:
            pass # Fallback to clean_url if direct fetch fails

        # Discord limits button URLs to 512 characters. Only add if it fits.
        if len(direct_url) <= 512:
            view.add_item(discord.ui.Button(label="Download Video", url=direct_url, style=discord.ButtonStyle.link, emoji="📥"))

        # 5. Send the message
        if file_size_mb > 20:
            # File too large, send embed with download link but no file attachment
            msg_content = f"⚠️ File is too large to upload directly ({file_size_mb:.1f} MB > 20 MB limit). Use the download button below!"
            if is_slash:
                await ctx.followup.send(content=msg_content, embed=embed, view=view)
            else:
                target = discord.utils.get(self.get_guild(GUILD_ID).text_channels, name=TARGET_CHANNEL_NAME) or ctx.channel
                await target.send(content=msg_content, embed=embed, view=view)
        else:
            # File is good, send embed + file attachment + buttons
            if is_slash:
                await ctx.followup.send(embed=embed, file=discord.File(path), view=view)
            else:
                target = discord.utils.get(self.get_guild(GUILD_ID).text_channels, name=TARGET_CHANNEL_NAME) or ctx.channel
                await target.send(embed=embed, file=discord.File(path), view=view)

        # Cleanup temp file
        if path.exists():
            path.unlink()
        if not is_slash:
            await ctx.add_reaction("✅")

    async def on_message(self, message):
        if message.author == self.user: return
        if message.guild is None:
            log_chan = self.get_channel(DM_LOG_CHANNEL_ID)
            if log_chan:
                embed = discord.Embed(title="📩 New DM", description=message.content or "*No text*", color=discord.Color.blue())
                embed.set_author(name=message.author, icon_url=message.author.display_avatar.url)
                await log_chan.send(embed=embed)
            return
        if getattr(message.channel, 'name', None) == LISTEN_CHANNEL_NAME:
            m = re.search(r'https?://(?:vt|vm|www|m)?\.?tiktok\.com/[^\s<>"\']+|https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s<>"\']+', message.content)
            if m: await self.process_media(message, m.group(0))

    @tasks.loop(minutes=5)
    async def check_academy_results(self):
        await self.wait_until_ready()
        sent_results = load_data(RESULTS_CACHE_FILE)
        if not isinstance(sent_results, list): sent_results = []
        rush_the_rest = False
        for discord_id, student_code in STUDENT_MAPPING.items():
            if str(student_code) in [str(c) for c in sent_results]: continue
            pdf_path = await self.fetch_results_pdf(student_code)
            if pdf_path:
                try:
                    user = await self.fetch_user(discord_id)
                    if user:
                        await user.send(content=f"📊 **Academic Results Released!**\nHere is your private results file for student code: `{student_code}`.", file=discord.File(pdf_path))
                        sent_results.append(str(student_code))
                        save_data(RESULTS_CACHE_FILE, sent_results)
                except Exception as e:
                    logger.error(f"⚠️ Failed to send DM to Discord ID {discord_id}: {e}")
                finally:
                    if os.path.exists(pdf_path): os.remove(pdf_path)
                rush_the_rest = True
            await asyncio.sleep(0 if rush_the_rest else 10)

    async def fetch_results_pdf(self, student_code):
        url = "https://mamresult.fahadalsheref.com/ShowResult"
        path = TEMP_DIR / f"results_{student_code}.pdf"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = await context.new_page()
            try:
                logger.info(f"[~] Testing portal access for Student Code: {student_code}")
                await page.goto(url, timeout=45000, wait_until="networkidle")
                await page.fill("#TxtStdcode", str(student_code))
                await page.click("button[type='submit']")
                await page.wait_for_timeout(6000)
                page_text = await page.inner_text("body")
                target_keywords = ["الفصل الدراسي", "المستوي", "التقدير", "التخصص"]
                is_explicitly_empty = "لا يوجد نتائج" in page_text
                has_valid_metrics = any(kw in page_text for kw in target_keywords)
                if is_explicitly_empty:
                    logger.info(f"[-] Code {student_code}: Results not out yet ('لا يوجد نتائج' found).")
                    return None
                if not has_valid_metrics:
                    logger.warning(f"[⚠️] Blazor Interface Glitch detected for Code {student_code}. Aborting capture.")
                    return None
                logger.info(f"🎉 Valid data fields matched for Code {student_code}! Compiling PDF capture...")
                await page.pdf(path=str(path), format="A4", print_background=True)
                return str(path)
            except Exception as e:
                logger.error(f"❌ Dynamic engine exception tracking code {student_code}: {e}")
                return None
            finally:
                await browser.close()

    @check_academy_results.before_loop
    async def before_check_results(self):
        await self.wait_until_ready()
        logger.info("⏳ Staggering Playwright engine launch to preserve boot stability...")
        await asyncio.sleep(15)

    @monitor_moodle.before_loop
    async def before_monitor_moodle(self):
        await self.wait_until_ready()
        await asyncio.sleep(5)

    @monitor_quizzes.before_loop
    async def before_monitor_quizzes(self):
        await self.wait_until_ready()
        await asyncio.sleep(10)

bot = GigaBot()

# ==============================================================================
# 7. SLASH COMMANDS
# ==============================================================================
class AssignmentModal(discord.ui.Modal, title="📝 Add New Assignment"):
    def __init__(self, subject_value, type_value):
        super().__init__()
        self.subject_value = subject_value
        self.type_value = type_value
        self.deadline = discord.ui.TextInput(label="Deadline (YYYY-MM-DD)", placeholder="e.g., 2026-05-20", required=True, min_length=10, max_length=10)
        self.details = discord.ui.TextInput(label="Assignment Details", style=discord.TextStyle.long, placeholder="Describe the assignment...", required=True, max_length=1000)
        self.add_item(self.deadline)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ts = get_cairo_9am_timestamp(self.deadline.value)
        except Exception:
            return await interaction.response.send_message("❌ Invalid Date format. Please use YYYY-MM-DD.", ephemeral=True)
        data = load_data(ASSIGNMENTS_FILE)
        task = {"subject": self.subject_value, "timestamp": int(ts), "type": self.type_value, "details": self.details.value, "image_urls": []}
        data.append(task)
        save_data(ASSIGNMENTS_FILE, data)
        channel = discord.utils.get(interaction.guild.text_channels, name=SUBJECT_CHANNELS.get(self.subject_value))
        if channel: await bot.send_stacked_embed(channel, task, "📌 NEW ASSIGNMENT LOGGED (Fast)", include_images=False)
        await interaction.response.send_message(f"✅ Assignment logged for **{self.subject_value}** due <t:{ts}:F>.", ephemeral=True)
class ArchiveSetupView(discord.ui.View):
    def __init__(self, library_keys):
        super().__init__(timeout=300)  # 5 minute timeout
        self.target_channel_id = None
        self.selected_subjects = []

        # Dynamically create subject options (max 25 for Discord select menus)
        subject_options = []
        for subj in list(library_keys)[:25]:
            subject_options.append(discord.SelectOption(label=subj[:100], value=subj))

        self.subject_select = discord.ui.Select(
            placeholder="Select one or more subjects to archive...",
            min_values=1,
            max_values=min(25, len(subject_options)),
            options=subject_options
        )
        self.subject_select.callback = self.subject_callback
        self.add_item(self.subject_select)

        self.channel_select = discord.ui.ChannelSelect(
            placeholder="Select the target archive channel...",
            channel_types=[discord.ChannelType.text]
        )
        self.channel_select.callback = self.channel_callback
        self.add_item(self.channel_select)

        self.start_btn = discord.ui.Button(label="▶️ Start Archiving", style=discord.ButtonStyle.success, disabled=True)
        self.start_btn.callback = self.start_callback
        self.add_item(self.start_btn)

    async def subject_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        self.selected_subjects = self.subject_select.values
        self._check_ready(interaction)

    async def channel_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        # FIX: Store the channel ID instead of the AppCommandChannel object
        self.target_channel_id = self.channel_select.values[0].id
        self._check_ready(interaction)

    def _check_ready(self, interaction: discord.Interaction):
        if self.target_channel_id and len(self.selected_subjects) > 0:
            self.start_btn.disabled = False
            asyncio.create_task(interaction.response.edit_message(view=self))

    async def start_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(f"⏳ Starting archive process for **{len(self.selected_subjects)}** subject(s). This may take a while...", ephemeral=True)

        # FIX: Fetch the actual TextChannel object using the ID
        target_channel = interaction.client.get_channel(self.target_channel_id)
        if not target_channel:
            return await interaction.followup.send("❌ Could not find the target channel. Make sure the bot has access to it.", ephemeral=True)

        library = load_data(LIBRARY_FILE)
        total_files = sum(len(library.get(subj, [])) for subj in self.selected_subjects)
        processed = 0
        loop = asyncio.get_event_loop()

        for subj in self.selected_subjects:
            files = library.get(subj, [])
            if not files:
                continue

            # Sort oldest first
            sorted_files = sorted(files, key=lambda x: x['timestamp'])

            # Send header (NO MENTIONS)
            await target_channel.send(f"📂 **Archiving: {subj.upper()}** ({len(sorted_files)} files)")

            for f in sorted_files:
                try:
                    res = await loop.run_in_executor(None, lambda u=f['url']: requests.get(u, timeout=30))
                    res.raise_for_status()

                    path = TEMP_DIR / f['name']
                    with open(path, "wb") as fh:
                        fh.write(res.content)

                    content_msg = f"📄 **{f['name']}**"
                    if f.get('description'):
                        desc = f['description'][:200] + ('...' if len(f['description']) > 200 else '')
                        content_msg += f"\n📝 *{desc}*"

                    await target_channel.send(content=content_msg, file=discord.File(path))

                    if path.exists():
                        path.unlink()

                    processed += 1
                    # CRITICAL: Rate limit protection (prevents Discord from blocking the bot)
                    await asyncio.sleep(1.2)

                except Exception as e:
                    logger.error(f"Failed to archive file {f['name']}: {e}")
                    await target_channel.send(f"⚠️ Failed to process: `{f['name']}`")
                    await asyncio.sleep(1.2)

        await interaction.followup.send(f"✅ **Archive Complete!**\nSuccessfully processed **{processed}/{total_files}** files.", ephemeral=True)
        self.stop()

@bot.tree.command(name="assignment_v2", description="Quickly add an assignment via pop-up form (No Images)")
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
@app_commands.choices(type=[app_commands.Choice(name="Lecture", value="Lecture"), app_commands.Choice(name="Section", value="Section")])
async def assignment_v2(interaction: discord.Interaction, subject: app_commands.Choice[str], type: app_commands.Choice[str]):
    await interaction.response.send_modal(AssignmentModal(subject.value, type.value))

@bot.tree.command(name="dow", description="Download a video from TikTok, Instagram, Facebook, or YouTube")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(url="The video URL to download")
async def download_video(interaction: discord.Interaction, url: str):
    if not any(x in url.lower() for x in ["tiktok.com", "instagram.com", "facebook.com", "fb.watch", "youtube.com", "youtu.be"]):
        return await interaction.response.send_message(
            "❌ Please provide a valid TikTok, Instagram, Facebook, or YouTube link.",
            ephemeral=True
        )
    await bot.process_media(interaction, url, is_slash=True)

@bot.tree.command(name="schedule", description="Look up your academic schedule (mirrors the college webpage)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(code="Student code or 14-digit national ID")
async def schedule_command(interaction: discord.Interaction, code: str):
    # ... rest of your schedule command code ...
    await interaction.response.defer(ephemeral=True)

    clean_code = code.strip()
    is_national_id = bool(re.match(r"^[0-9]{14}$", clean_code))

    headers = {"x-api-key": "MAM_ULTRA_2026"}
    base_url = "http://mamstd.fahadalsheref.com"

    # Lecture times extracted directly from the college webpage
    LECTURE_TIMES = {
        "1": "08:30 - 09:40", "2": "09:45 - 10:55", "3": "11:00 - 12:10",
        "4": "12:15 - 01:25", "5": "01:30 - 02:40", "6": "02:45 - 03:55",
        "7": "04:00 - 05:00", "8": "05:05 - 06:05"
    }
    DAYS_ORDER = ["السبت", "الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس"]

    async with aiohttp.ClientSession() as session:
        try:
            if is_national_id:
                url = f"{base_url}/api/student-lookup"
                async with session.post(url, headers={**headers, "Content-Type": "application/json"}, json={"national_id": clean_code}) as resp:
                    if resp.status != 200:
                        return await interaction.followup.send("❌ Could not find student data. Please check your national ID.", ephemeral=True)
                    data = await resp.json()
            else:
                url = f"{base_url}/api/student/{clean_code}"
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return await interaction.followup.send("❌ Could not find student data. Please check your student code.", ephemeral=True)
                    data = await resp.json()

            student = data.get("student", {})
            rows = data.get("schedule", [])

            if not student or not rows:
                return await interaction.followup.send("⚠️ No registered classes found or tuition fees may not be paid.", ephemeral=True)

            # Group by Section (if available)
            sections = {}
            for r in rows:
                sec = str(r.get("section_no") or r.get("SECTION") or r.get("السكشن") or "غير محدد").strip()
                if sec not in sections:
                    sections[sec] = []
                sections[sec].append(r)

            embed = discord.Embed(
                title="📚 جدولك الدراسي | الأكاديمية الحديثة",
                description="العام الدراسي 2026–2027 · اعرف مواعيد محاضراتك وأماكنها بكل وضوح.",
                color=0x2C3E50 # Dark blue/grey matching the college theme
            )

            # Student Info Header (matching webpage)
            embed.add_field(name="🆔 كود الطالب", value=str(student.get('student_id', '-')), inline=True)
            embed.add_field(name="👤 اسم الطالب", value=str(student.get('student_name', '-')), inline=True)
            embed.add_field(name="🎓 التخصص", value=str(student.get('spec', '-')), inline=True)
            embed.add_field(name="📊 المستوى", value=str(student.get('level_name', '-')), inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True) # Spacer

            # Process each section
            for section_num, section_rows in sections.items():
                if len(sections) > 1:
                    embed.add_field(name=f"📂 سكشن الطالب: {section_num}", value="\u200b", inline=False)

                # Group section rows by Day
                schedule_by_day = {day: [] for day in DAYS_ORDER}
                other_days = []

                for r in section_rows:
                    day = str(r.get("day") or r.get("day_real") or "غير محدد").strip()
                    if day in DAYS_ORDER:
                        schedule_by_day[day].append(r)
                    else:
                        other_days.append(r)

                for day in DAYS_ORDER:
                    classes = schedule_by_day[day]
                    if not classes:
                        continue

                    # Sort by lecture number
                    def get_lec_num(x):
                        try: return int(x.get('lecture_no', 0))
                        except: return 99
                    classes.sort(key=get_lec_num)

                    day_text = f"**{len(classes)} محاضرة**\n" + "━" * 20 + "\n"

                    for c in classes:
                        lec = str(c.get('lecture_no') or "-")
                        time = LECTURE_TIMES.get(lec, "غير محدد")
                        mat = c.get("MAT") or c.get("MAT_NAME") or "مادة غير مسماة"
                        dr = c.get("DR_NAME") or "غير محدد"
                        place = c.get("PLACE") or "غير محدد"

                        # Format mimicking the webpage's table cell
                        day_text += f"🕐 **المحاضرة {lec}** (`{time}`)\n"
                        day_text += f"📘 **{mat}**\n"
                        day_text += f"👨‍🏫 {dr} | 📍 {place}\n\n"

                    # Discord field limit is 1024 characters
                    if len(day_text) > 1000:
                        day_text = day_text[:990] + "...\n*(تم الاختصار بسبب طول النص)*"

                    embed.add_field(name=f"📅 {day}", value=day_text, inline=False)

                # Handle unmatched days
                if other_days:
                    other_text = "━" * 20 + "\n"
                    for c in other_days:
                        day = c.get("day") or c.get("day_real") or "-"
                        lec = c.get("lecture_no") or "-"
                        mat = c.get("MAT") or c.get("MAT_NAME") or "مادة"
                        dr = c.get("DR_NAME") or "-"
                        place = c.get("PLACE") or "-"
                        other_text += f"🕐 **{day} (محاضرة {lec})**: {mat}\n👨‍🏫 {dr} | 📍 {place}\n\n"

                    if len(other_text) > 1000:
                        other_text = other_text[:990] + "..."
                    embed.add_field(name="📅 أيام أخرى", value=other_text, inline=False)

            # Webpage Footer Info
            embed.set_footer(text="💡 معلومة عن دخول Moodle: اسم المستخدم هو كود الطالب، وكلمة المرور: رقم 1 ثم آخر خمسة أرقام من الرقم القومي.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Schedule command error: {e}")
            await interaction.followup.send("⚠️ A technical error occurred while communicating with the college server. Please try again later.", ephemeral=True)

@bot.tree.command(name="assignment", description="Add a new assignment")
@app_commands.describe(subject="The subject name", deadline="Deadline date (YYYY-MM-DD)", type="Type of task", details="Description or notes", image1="First image (optional)", image2="Second image (optional)", image3="Third image (optional)", image4="fourth image (optional)", image5="fifth image (optional)", image6="sixth image (optional)", image7="seventh image (optional)", image8="eightth image (optional)", image9="nineth image (optional)", image10="tenth image (optional)")
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
@app_commands.choices(type=[app_commands.Choice(name="Lecture", value="Lecture"), app_commands.Choice(name="Section", value="Section")])
async def add_assignment(interaction: discord.Interaction, subject: app_commands.Choice[str], deadline: str, type: app_commands.Choice[str], details: str, image1: discord.Attachment = None, image2: discord.Attachment = None, image3: discord.Attachment = None, image4: discord.Attachment = None, image5: discord.Attachment = None, image6: discord.Attachment = None, image7: discord.Attachment = None, image8: discord.Attachment = None, image9: discord.Attachment = None, image10: discord.Attachment = None):
    try:
        ts = get_cairo_9am_timestamp(deadline)
    except Exception:
        return await interaction.response.send_message("❌ Invalid Date. Use YYYY-MM-DD format.", ephemeral=True)
    images = [img.url for img in [image1, image2, image3, image4, image5, image6, image7, image8, image9, image10] if img]
    data = load_data(ASSIGNMENTS_FILE)
    data.append({"subject": subject.value, "timestamp": int(ts), "type": type.value, "details": details, "image_urls": images})
    save_data(ASSIGNMENTS_FILE, data)
    channel = discord.utils.get(interaction.guild.text_channels, name=SUBJECT_CHANNELS.get(subject.value))
    if channel: await bot.send_stacked_embed(channel, data[-1], "📌 NEW ASSIGNMENT LOGGED", include_images=True)
    await interaction.response.send_message(f"✅ Assignment logged for **{subject.name}** due <t:{ts}:F>.", ephemeral=True)

@bot.tree.command(name="test_results_engine", description="Force test the Playwright PDF engine with Keyword Gate")
async def test_results_engine(interaction: discord.Interaction):
    if interaction.user.id != 693791864406540339:
        return await interaction.response.send_message("❌ This test command is locked to the designated system tester.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    status_message = await interaction.followup.send("⏳ Testing isolated headless engine with dynamic Arabic keyword checks for code `32500144`...", ephemeral=True)
    pdf_path = await bot.fetch_results_pdf("32500144")
    if pdf_path and os.path.exists(pdf_path):
        try:
            await interaction.user.send(content="📊 **Playwright Engine Test Success!**\nVerified metrics caught. Layout snapshot delivered successfully.", file=discord.File(pdf_path))
            os.remove(pdf_path)
            await interaction.followup.edit_message(message_id=status_message.id, content="✅ Diagnostic file compiled and sent to your DMs!")
        except Exception as e:
            await interaction.followup.edit_message(message_id=status_message.id, content=f"❌ Document generated but failed to DM profile: `{e}`")
    else:
        await interaction.followup.edit_message(message_id=status_message.id, content="❌ Portal evaluated as empty or unhydrated. Capture dropped safely.")

@bot.tree.command(name="delete_assignment", description="Delete an active assignment")
@app_commands.describe(subject="The subject of the assignment", deadline="The deadline date (YYYY-MM-DD)")
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
async def delete_assignment(interaction: discord.Interaction, subject: app_commands.Choice[str], deadline: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
    try:
        target_ts = get_cairo_9am_timestamp(deadline)
    except Exception:
        return await interaction.response.send_message("❌ Invalid Date. Use YYYY-MM-DD.", ephemeral=True)
    data = load_data(ASSIGNMENTS_FILE)
    new_data = [t for t in data if not (t['subject'] == subject.value and t['timestamp'] == target_ts)]
    if len(new_data) == len(data):
        return await interaction.response.send_message("❌ No active assignment found with that subject and deadline.", ephemeral=True)
    save_data(ASSIGNMENTS_FILE, new_data)
    await interaction.response.send_message(f"✅ Deleted assignment for **{subject.name}** due <t:{target_ts}:F>.", ephemeral=True)

@bot.tree.command(name="broadcast", description="Send a custom DM to a list of user IDs (Admin/Owner Only)")
@app_commands.describe(ids_string="Comma-separated list of Discord User IDs", message="The message to send to the users")
async def broadcast(interaction: discord.Interaction, ids_string: str, message: str):
    app_owner_id = bot.application.owner.id if bot.application and bot.application.owner else None
    if interaction.user.id != app_owner_id and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("⛔ **Access Denied:** This command is strictly restricted to the system administrator.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    user_ids = [id_str.strip() for id_str in ids_string.split(",") if id_str.strip().isdigit()]
    if not user_ids:
        return await interaction.followup.send("❌ **Error:** Please provide a valid list of numeric user IDs separated by commas.", ephemeral=True)
    await interaction.followup.send(f"⏳ **Broadcast Initialized:** Processing DMs for {len(user_ids)} users...", ephemeral=True)
    success_count, failed_logs = 0, []
    for u_id in user_ids:
        try:
            user = await bot.fetch_user(int(u_id))
            if user:
                await user.send(message)
                success_count += 1
        except discord.Forbidden:
            failed_logs.append(f"• <@{u_id}> ({u_id}) - DMs are closed/blocked.")
        except discord.NotFound:
            failed_logs.append(f"• {u_id} - Invalid Discord User ID.")
        except Exception as e:
            failed_logs.append(f"• {u_id} - Unexpected error: {e}")
    report_embed = discord.Embed(title="📊 Broadcast Execution Report", color=discord.Color.green() if success_count == len(user_ids) else discord.Color.orange())
    report_embed.add_field(name="Total Target Users", value=str(len(user_ids)), inline=True)
    report_embed.add_field(name="Successful DMs", value=f"✅ {success_count}", inline=True)
    if failed_logs:
        error_summary = "\n".join(failed_logs)
        report_embed.add_field(name="Failed Deliveries", value=error_summary[:1000] + "\n... *(truncated)*" if len(error_summary) > 1024 else error_summary, inline=False)
    await interaction.followup.send(embed=report_embed, ephemeral=True)

@bot.tree.command(name="edit_assignment", description="Edit details or deadline of an assignment")
@app_commands.describe(subject="Current subject", old_deadline="Current deadline (YYYY-MM-DD)", new_details="New description (leave blank to keep current)", new_deadline="New deadline (YYYY-MM-DD) (leave blank to keep current)", new_image1="New image 1 (optional)")
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
async def edit_assignment(interaction: discord.Interaction, subject: app_commands.Choice[str], old_deadline: str, new_details: str = "", new_deadline: str = "", new_image1: discord.Attachment = None):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
    try:
        old_ts = get_cairo_9am_timestamp(old_deadline)
    except Exception:
        return await interaction.response.send_message("❌ Invalid Old Date. Use YYYY-MM-DD.", ephemeral=True)
    data = load_data(ASSIGNMENTS_FILE)
    found = False
    for task in data:
        if task['subject'] == subject.value and task['timestamp'] == old_ts:
            found = True
            if new_details: task['details'] = new_details
            if new_deadline:
                try: task['timestamp'] = int(get_cairo_9am_timestamp(new_deadline))
                except: return await interaction.response.send_message("❌ Invalid New Date.", ephemeral=True)
            if new_image1: task['image_urls'] = [new_image1.url]
            break
    if not found:
        return await interaction.response.send_message("❌ Assignment not found.", ephemeral=True)
    save_data(ASSIGNMENTS_FILE, data)
    await interaction.response.send_message("✅ Assignment updated successfully!", ephemeral=True)
    channel = discord.utils.get(interaction.guild.text_channels, name=SUBJECT_CHANNELS.get(subject.value))
    if channel:
        for task in data:
            if task['subject'] == subject.value and task['timestamp'] == (int(get_cairo_9am_timestamp(new_deadline)) if new_deadline else old_ts):
                await bot.send_stacked_embed(channel, task, "🔄 ASSIGNMENT UPDATED", include_images=True)
                break

@bot.tree.command(name="list_assignments", description="View all active assignments and quizzes")
async def list_assignments(interaction: discord.Interaction):
    data = load_data(ASSIGNMENTS_FILE)
    if not data:
        return await interaction.response.send_message("📭 No active assignments or quizzes found.", ephemeral=True)
    sorted_data = sorted(data, key=lambda x: x['timestamp'])
    subjects = {}
    for task in sorted_data:
        subjects.setdefault(task['subject'], []).append(task)
    embeds = []
    current_embed = discord.Embed(title="📋 Active Assignments & Quizzes", color=discord.Color.blue())
    char_count = 0
    for subj, tasks in subjects.items():
        field_value = ""
        for t in tasks:
            ts = t['timestamp']
            status_icon = "🚨" if ts < time.time() else ("⚠️" if (ts - time.time()) < 86400 else "✅")
            line = f"{status_icon} **{t.get('type', 'Task')}**: {t['details'][:50]}{'...' if len(t['details'])>50 else ''}\n   └ Due: <t:{ts}:F> (<t:{ts}:R>)\n\n"
            if len(field_value) + len(line) > 1024:
                current_embed.add_field(name=f"📘 {subj.title()}", value=field_value, inline=False)
                char_count += len(field_value)
                if char_count > 5000:
                    embeds.append(current_embed)
                    current_embed = discord.Embed(title="📋 Active Assignments (Cont.)", color=discord.Color.blue())
                    char_count = 0
                field_value = line
            else:
                field_value += line
        if field_value:
            current_embed.add_field(name=f"📘 {subj.title()}", value=field_value, inline=False)
    if current_embed.fields:
        embeds.append(current_embed)
    await interaction.response.send_message(embed=embeds[0], ephemeral=True)
    for embed in embeds[1:]:
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="view_assignments", description="Browse and view details of manual assignments privately")
async def view_assignments(interaction: discord.Interaction):
    data = load_data(ASSIGNMENTS_FILE)
    manual_assignments = [t for t in data if t.get('type') != 'Moodle Quiz']
    if not manual_assignments:
        return await interaction.response.send_message("📭 No manual assignments found.", ephemeral=True)
    sorted_assignments = sorted(manual_assignments, key=lambda x: x['timestamp'])
    assignment_lookup = {str(i): t for i, t in enumerate(sorted_assignments)}
    options = []
    for i, t in enumerate(sorted_assignments):
        ts = t['timestamp']
        img_icon = f" 📷{len(t.get('image_urls', []))}" if t.get('image_urls') else ""
        options.append(discord.SelectOption(label=f"{t['subject'].title()[:40]} | <t:{ts}:R>{img_icon}", description=f"Due: <t:{ts}:F> | Type: {t.get('type', 'Task')}", value=str(i)))

    class AssignmentView(discord.ui.View):
        def __init__(self, lookup):
            super().__init__(timeout=180)
            self.lookup = lookup
        @discord.ui.select(placeholder="Select an assignment to view details...", min_values=1, max_values=1, options=options)
        async def select_assignment(self, interaction: discord.Interaction, select: discord.ui.Select):
            task = self.lookup.get(select.values[0])
            if not task:
                return await interaction.response.send_message("❌ Error loading assignment.", ephemeral=True)
            col, stat = get_status_ui(task['timestamp'])
            embeds = [discord.Embed(title="📌 ASSIGNMENT DETAILS", color=col)]
            embeds[0].add_field(name="📘 Subject", value=f"**{task['subject'].upper()}**", inline=False)
            embeds[0].add_field(name="📊 Status", value=f"**{stat}**", inline=True)
            embeds[0].add_field(name="📍 Type", value=task.get('type', 'Assignment'), inline=True)
            embeds[0].add_field(name="⏳ Submission", value=f"<t:{task['timestamp']}:F>\n(<t:{task['timestamp']}:R>)", inline=False)
            embeds[0].add_field(name="📝 Details", value=task.get('details', 'No details'), inline=False)
            imgs = task.get('image_urls', [])
            if imgs:
                embeds[0].set_image(url=imgs[0])
                for u in imgs[1:]:
                    extra = discord.Embed(color=col)
                    extra.set_image(url=u)
                    embeds.append(extra)
            await interaction.response.send_message(embeds=embeds, ephemeral=True)

    await interaction.response.send_message(content="### 📋 Select an Assignment\nChoose below to view its full details and images privately.", view=AssignmentView(assignment_lookup), ephemeral=True)

@bot.tree.command(name="list_quizzes", description="View only active Moodle Quizzes")
async def list_quizzes(interaction: discord.Interaction):
    data = load_data(ASSIGNMENTS_FILE)
    quizzes = [t for t in data if t.get('type') == 'Moodle Quiz']
    if not quizzes:
        return await interaction.response.send_message("📭 No active Moodle Quizzes found.", ephemeral=True)
    embed = discord.Embed(title="📝 Active Moodle Quizzes", color=discord.Color.purple())
    for q in sorted(quizzes, key=lambda x: x['timestamp']):
        ts = q['timestamp']
        status_icon = "🚨" if ts < time.time() else ("⚠️" if (ts - time.time()) < 86400 else "✅")
        name = q['details'].replace("📝 **", "").replace("**", "")
        embed.add_field(name=f"📘 {q['subject'].title()}", value=f"{status_icon} **{name}**\n   └ Due: <t:{ts}:F> (<t:{ts}:R>)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="Display the user guide and command list")
async def help_command(interaction: discord.Interaction):
    config = load_data(CONFIG_FILE)
    archive_link = f"<#{config.get('request_channel_id')}>" if config.get('request_channel_id') else "#setup-archive-channel"
    embed = discord.Embed(title="🥭 Mango Man User Guide", description="Welcome! I am here to streamline your study life at Modern Academy. Here is how you can use me:", color=discord.Color.gold())
    embed.add_field(name="📅 Academic Schedule",value="• `/schedule [code]` - Look up your weekly lecture schedule using your student code or 14-digit national ID.",inline=False)
    embed.add_field(name="📚 Assignments & Reminders", value="• `/assignment` - Log a new manual deadline with **up to 10 images**.\n• `/delete_assignment` - Remove an active assignment.\n• `/edit_assignment` - Update details or deadline of a task.\n• `/list_assignments` - View all upcoming tasks & quizzes.\n• `/list_quizzes` - View only Moodle Quizzes.\n• `/view_assignments` - Browse manual assignments privately.", inline=False)
    embed.add_field(name="📂 Moodle File Archive", value=f"• **Auto-Sync:** I automatically download new PDFs/PPTs from Moodle.\n• **Browse Files:** Use the dropdown menu in {archive_link} to select a subject.\n• **Download:** Select up to 10 files, and I will DM them to you with professor notes if there are any.", inline=False)
    embed.add_field(name="📱 Social Media Downloader", value=f"• `/dow [url]` - Download TikTok or Instagram Reels without watermarks.\n• **Auto-Forward:** Send links in <#{discord.utils.get(interaction.guild.text_channels, name=LISTEN_CHANNEL_NAME).id}> to auto-download.", inline=False)
    embed.set_footer(text="Mango Man v1.0")
    embed.set_thumbnail(url="https://i.ibb.co/Q7Sss5ps/mangoman.gif")
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="View Bot's REPO", url="https://github.com/khaled-0110/mango", style=discord.ButtonStyle.link, emoji="🐙"))
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    await interaction.followup.send(content=f"Developed by <@{interaction.user.id}>", ephemeral=False)



@bot.tree.command(name="setup_archive", description="Initialize or update the study archive menu")
async def setup_archive(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    library = load_data(LIBRARY_FILE)
    total_files = sum(len(files) for files in library.values())
    embed = discord.Embed(title="🏛️ Central Study Archive", description=f"Welcome to the **Mango Man** file repository!\n\n📂 **How to use:**\n1. Select a **Subject** from the dropdown below.\n2. Choose up to **10 files** you need.\n3. Click **Download** to receive them in your DMs.\n\n📊 **Current Status:** {total_files} materials archived.\n🔄 **Last Updated:** <t:{int(time.time())}:R>", color=discord.Color.blue())
    embed.set_thumbnail(url="https://i.ibb.co/Q7Sss5ps/mangoman.gif")
    embed.set_footer(text="Mango Man v1.0")
    msg = await interaction.channel.send(embed=embed, view=PermanentFileViewV2(bot))
    save_data(CONFIG_FILE, {"master_message_id": msg.id, "request_channel_id": interaction.channel_id})
    await interaction.followup.send("✅ Archive menu initialized!", ephemeral=True)

@bot.tree.command(name="refresh_archive", description="Force update the archive message stats & timer")
async def refresh_archive(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        await bot.refresh_master_embed()
        await interaction.followup.send("✅ Archive message updated successfully!", ephemeral=True)
    except Exception as e:
        logger.error(f"Manual refresh failed: {e}")
        await interaction.followup.send(f"❌ Failed to update. Check logs: `{e}`", ephemeral=True)

@bot.tree.command(name="move", description="Move an AFK user between voice channels to wake them up")
@app_commands.describe(user="User currently in a voice channel", times="Number of moves (1-20)")
async def move_user(interaction: discord.Interaction, user: discord.Member, times: int = 5):
    role_id = 1431238736246603846
    if not interaction.user.guild_permissions.administrator and not any(r.id == role_id for r in interaction.user.roles):
        return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    if times < 1 or times > 20:
        return await interaction.response.send_message("❌ Please enter a number between 1 and 20.", ephemeral=True)
    if user.voice is None or user.voice.channel is None:
        return await interaction.response.send_message("❌ User is not in a voice channel.", ephemeral=True)
    original_channel = user.voice.channel
    target_channels = [c for c in interaction.guild.voice_channels if c != original_channel and c.permissions_for(interaction.guild.me).connect]
    if not target_channels:
        return await interaction.response.send_message("❌ No other accessible voice channels to move to.", ephemeral=True)
    await interaction.response.defer()
    import random
    for _ in range(times):
        try:
            await user.move_to(random.choice(target_channels))
            await asyncio.sleep(0.8)
        except discord.Forbidden:
            return await interaction.followup.send("❌ I don't have permission to move users between channels.", ephemeral=True)
        except Exception as e:
            logger.error(f"Move error: {e}")
            break
    try:
        await user.move_to(original_channel)
    except Exception as e:
        logger.error(f"Return move error: {e}")
    await interaction.followup.send(f"✅ Successfully moved {user.mention} `{times}` times! They've been returned to `{original_channel.name}`.")
@bot.tree.command(name="send_to_archive", description="Archive old subject materials via dropdowns (Admin Only)")
async def send_to_archive(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)

    library = load_data(LIBRARY_FILE)
    if not library:
        return await interaction.response.send_message("❌ No files found in the bot's library.", ephemeral=True)

    view = ArchiveSetupView(library.keys())
    await interaction.response.send_message(
        content="🗄️ **Archive Setup**\nPlease select the target channel and the subjects you want to archive below:",
        view=view,
        ephemeral=True
    )
# ==============================================================================
# 8. TELEGRAM INTEGRATION & MAIN LOOP
# ==============================================================================
async def tg_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        m = re.search(r'https?://(?:vt|vm|www|m)?\.?tiktok\.com/[^\s<>"\']+|https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s<>"\']+', update.message.text)
        if m and DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": update.message.text})
            await update.message.reply_text("🚀 Forwarded!")

def run_telegram():
    if not TELEGRAM_TOKEN: return
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), tg_msg))
    app.run_polling(stop_signals=(), close_loop=False)

if __name__ == "__main__":
    if TELEGRAM_TOKEN:
        threading.Thread(target=run_telegram, daemon=True).start()
    bot.run(DISCORD_TOKEN)
