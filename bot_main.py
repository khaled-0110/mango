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
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from discord.ext import tasks
from discord import app_commands

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# === 1. CONFIGURATION ===
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GUILD_ID = int(os.getenv("GUILD_ID", 0))

# 🔒 IMPORTANT: Create a private channel and paste its ID here to log DMs
DM_LOG_CHANNEL_ID = 123456789012345678  # <-- REPLACE THIS WITH YOUR CHANNEL ID

CAIRO_TZ = pytz.timezone('Africa/Cairo')
DEAD_GPA_ROLE_ID = 1431231714440777808

# Channel Mapping
TARGET_CHANNEL_NAME = "shared-videos"
LISTEN_CHANNEL_NAME = "links-inbox"
SUBJECT_CHANNELS = {
    "principles of managerial accounting": "皿👮‍♂️principles-of-man-jail-counting",
    "principles of financial management": "💵principles-of-finally-no-money-management",
    "business analysis.": "🍆business-anal-sis",
    "principles of microeconomics": "📈principles-of-micro-dicks",
    "management information systems": "💻manage-mental-info-give-up-systems"
}

# Paths
MOODLE_CACHE = "moodle_seen_items.json"
ASSIGNMENTS_FILE = "assignments.json"
LIBRARY_FILE = "moodle_files_library.json"
CONFIG_FILE = "system_config.json"
ALERTS_FILE = "sent_alerts.json"
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)

# === 2. UTILITIES ===
def normalize_subject(name):
    return " ".join(str(name).split()).lower().strip()

def load_data(path):
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

def save_data(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save {path}: {e}")

def get_cairo_9am_timestamp(date_str):
    dt_naive = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
    dt_cairo = CAIRO_TZ.localize(dt_naive)
    return int(dt_cairo.timestamp())

def get_status_ui(target_ts):
    now_ts = int(time.time())
    diff = target_ts - now_ts
    if diff < 0:
        return discord.Color.red(), "🚨 OVERDUE"
    elif diff < 86400:
        return discord.Color.orange(), "⚠️ DUE SOON"
    else:
        return discord.Color.green(), "✅ ON TRACK"

# === 3. RAPIDAPI DOWNLOAD LOGIC ===

def get_tiktok_video(url):
    host = "tiktok-video-no-watermark2.p.rapidapi.com"
    for key in [os.getenv("ACCOUNT_A_KEY"), os.getenv("ACCOUNT_B_KEY")]:
        if not key: continue
        try:
            headers = {"x-rapidapi-key": key, "x-rapidapi-host": host}
            res = requests.get(f"https://{host}/", headers=headers, params={"url": url}, timeout=15)
            video = res.json().get("data", {}).get("play")
            if video: return video
        except Exception: continue
    return None

def get_instagram_video(url):
    host = "instagram-downloader-v2-scraper-reels-igtv-posts-stories.p.rapidapi.com"
    key = os.getenv("INSTAGRAM_API_KEY")
    
    if not key:
        logger.error("Instagram: No API key found in env.")
        return None

    try:
        headers = {
            "x-rapidapi-key": key,
            "x-rapidapi-host": host
        }
        
        response = requests.get(
            f"https://{host}/get-post", 
            headers=headers, 
            params={"url": url}, 
            timeout=15
        )
        
        if response.status_code != 200:
            logger.warning(f"Instagram API Status: {response.status_code}")
            return None

        data = response.json()

        if isinstance(data, dict) and "media" in data:
            media_list = data["media"]
            
            for item in media_list:
                if item.get("is_video") is True:
                    video_url = item.get("url")
                    if video_url and video_url.startswith("http"):
                        logger.info(f"✅ Instagram V2: Found video URL → {video_url[:80]}...")
                        return video_url
            
            logger.warning("Instagram V2: Media found, but no video type detected.")
            return None
        
        if "message" in data:
            logger.error(f"Instagram API Error Message: {data['message']}")
            
        return None

    except Exception as e:
        logger.error(f"Instagram V2 API Exception: {e}\n{traceback.format_exc()}")
        return None

# === 4. INTERACTIVE UI (V2 UPGRADED) ===

class FileSelectionViewV2(discord.ui.View):
    def __init__(self, lookup, options):
        super().__init__(timeout=180)
        self.lookup = lookup
        
        # V2 Select Menu: Supports up to 100 options!
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

        # V2 allows up to 100 options! We show the latest 100.
        sorted_files = sorted(files, key=lambda x: x['timestamp'], reverse=True)[:100]
        file_lookup = {str(i): f for i, f in enumerate(sorted_files)}
        
        options = []
        for i, f in file_lookup.items():
            date_str = datetime.fromtimestamp(f['timestamp']).strftime('%d/%b %H:%M')
            has_desc = " 📝" if f.get('description') else ""
            # Truncate name to fit in V2 option label (max 100 chars)
            label = f"{f['name'][:90]}{has_desc}"
            
            options.append(discord.SelectOption(
                label=label,
                description=f"Uploaded: {date_str}",
                value=i
            ))

        await interaction.response.send_message(
            content=f"### 📂 {subject.upper()} Archive (Showing latest 100)\nSelect up to 10 files to receive them in DMs.",
            view=FileSelectionViewV2(file_lookup, options),
            ephemeral=True
        )

# === 5. MAIN BOT ENGINE ===
class GigaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Add the V2 View for persistence
        self.add_view(PermanentFileViewV2(self))
        
        self.monitor_moodle.start()
        self.monitor_quizzes.start()
        self.precision_scheduler.start()
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

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

                if c_id not in seen_data:
                    seen_data[c_id] = []
                subj_key = self.match_subj(c_id, courses)
                if subj_key not in library: library[subj_key] = []

                for sec in sec_res:
                    for mod in sec.get('modules', []):
                        m_id = str(mod.get('id'))
                        if m_id not in seen_data[c_id] and mod.get('modname') == 'resource':
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

            q_res = (await loop.run_in_executor(None, lambda: requests.get(
                api_url,
                params=[('wstoken', token), ('wsfunction', 'mod_quiz_get_quizzes_by_courses'), ('moodlewsrestformat', 'json')] +
                       [('courseids[]', cid) for cid in course_ids]
            ))).json()

            data = load_data(ASSIGNMENTS_FILE)
            existing = [t.get('moodle_id') for t in data if t.get('moodle_id')]

            for q in q_res.get('quizzes', []):
                m_id = f"quiz_{q.get('id')}"
                if m_id not in existing and q.get('timeclose', 0) > time.time():
                    subj = self.match_subj(q.get('course'), c_res)
                    task = {"subject": subj, "timestamp": q['timeclose'], "type": "Moodle Quiz", "details": f"📝 **{q['name']}**", "moodle_id": m_id}
                    data.append(task)
                    chan = discord.utils.get(self.get_guild(GUILD_ID).text_channels, name=SUBJECT_CHANNELS.get(subj))
                    if chan: await self.send_stacked_embed(chan, task, "🆕 NEW MOODLE QUIZ")

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
            if not target: continue
            if (now - target > 172800): continue

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
                    if key in name: return key
        return "management information systems"

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
            
            # Get Real-Time Data
            library = load_data(LIBRARY_FILE)
            total = sum(len(v) for v in library.values())
            
            # Create Updated Embed
            embed = discord.Embed(
                title="🏛️ Central Study Archive",
                description=(
                    "Welcome to the **Mango Man** file repository!\n\n"
                    "📂 **How to abuse:**\n"
                    "1. Select a **Subject** from the dropdown below.\n"
                    "2. Choose up to **10 files** you need.\n"
                    "3. Click **Download** to receive them in your DMs.\n\n"
                    f"📊 **Current Status:** {total} materials archived.\n"
                    f"🔄 **Last Updated:** <t:{int(time.time())}:R>"  # Moved here!
                ),
                color=discord.Color.blue()
            )
            embed.set_thumbnail(url="https://i.ibb.co/Q7Sss5ps/mangoman.gif")
            embed.set_footer(text="Mango Man v1.0") # Keep footer clean

            # Re-attach the View
            view = PermanentFileViewV2(self)
            await msg.edit(embed=embed, view=view)
            
        except Exception as e:
            logger.error(f"Failed to refresh archive embed: {e}")

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
        main = discord.Embed(title=title, color=col)
        main.add_field(name="📘 Subject", value=f"**{task['subject'].upper()}**", inline=False)
        main.add_field(name="📊 Status", value=f"**{stat}**", inline=True)
        main.add_field(name="⏳ Submission", value=f"<t:{task['timestamp']}:F>\n(<t:{task['timestamp']}:R>)", inline=False)
        main.add_field(name="📝 Details", value=task.get('details', 'No details'), inline=False)

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
        if is_slash: await ctx.response.defer()
        else: await ctx.add_reaction("⏳")

        loop = asyncio.get_event_loop()
        vid_url = await loop.run_in_executor(None, get_tiktok_video, url) if "tiktok.com" in url else await loop.run_in_executor(None, get_instagram_video, url)

        if not vid_url:
            msg = "❌ Could not extract video. The link may be private, expired, or the API quota may be exhausted."
            return await ctx.followup.send(msg) if is_slash else await ctx.reply(msg)

        try:
            path = TEMP_DIR / f"vid_{int(time.time())}.mp4"
            res = await loop.run_in_executor(None, lambda u=vid_url: requests.get(u, timeout=30))
            with open(path, "wb") as f: f.write(res.content)

            guild = self.get_guild(GUILD_ID)
            if is_slash:
                target = ctx.channel
                mention = ctx.user.mention
            else:
                target = discord.utils.get(guild.text_channels, name=TARGET_CHANNEL_NAME) if guild else ctx.channel
                mention = ctx.author.mention

            if path.stat().st_size > 8 * 1024 * 1024:
                msg_content = f"{mention}\n⚠️ File too large to upload. Direct download link:\n{vid_url}"
                if is_slash: await ctx.followup.send(msg_content)
                else: await target.send(msg_content)
            else:
                cap = f"📹 Video for {mention}"
                if is_slash: await ctx.followup.send(content=cap, file=discord.File(path))
                else: await target.send(content=cap, file=discord.File(path))

            if path.exists(): path.unlink()
            if not is_slash: await ctx.add_reaction("✅")
        except Exception:
            logger.error(f"process_media error:\n{traceback.format_exc()}")
            if is_slash: await ctx.followup.send("❌ Failed to upload the video.")
            else: await ctx.add_reaction("❌")

    async def on_message(self, message):
        if message.author == self.user: return
        if message.guild is None:
            log_chan = self.get_channel(DM_LOG_CHANNEL_ID)
            if log_chan:
                embed = discord.Embed(
                    title="📩 New DM",
                    description=message.content or "*No text*",
                    color=discord.Color.blue()
                )
                embed.set_author(name=message.author, icon_url=message.author.display_avatar.url)
                await log_chan.send(embed=embed)
            return

        if getattr(message.channel, 'name', None) == LISTEN_CHANNEL_NAME:
            m = re.search(r'https?://(?:vt|vm|www|m)?\.?tiktok\.com/[^\s<>"\']+|https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s<>"\']+', message.content)
            if m: await self.process_media(message, m.group(0))

bot = GigaBot()

# === SLASH COMMANDS ===

class AssignmentModal(discord.ui.Modal, title="📝 Add New Assignment"):
    def __init__(self, subject_value, type_value):
        super().__init__()
        self.subject_value = subject_value
        self.type_value = type_value
        
        self.deadline = discord.ui.TextInput(
            label="Deadline (YYYY-MM-DD)",
            placeholder="e.g., 2026-05-20",
            required=True,
            min_length=10,
            max_length=10
        )
        self.details = discord.ui.TextInput(
            label="Assignment Details",
            style=discord.TextStyle.long,
            placeholder="Describe the assignment or paste instructions here...",
            required=True,
            max_length=1000
        )
        self.add_item(self.deadline)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ts = get_cairo_9am_timestamp(self.deadline.value)
        except Exception:
            return await interaction.response.send_message("❌ Invalid Date format. Please use YYYY-MM-DD.", ephemeral=True)

        data = load_data(ASSIGNMENTS_FILE)
        
        # Create task object (No images for V2 modal)
        task = {
            "subject": self.subject_value,
            "timestamp": int(ts),
            "type": self.type_value,
            "details": self.details.value,
            "image_urls": [] 
        }
        
        data.append(task)
        save_data(ASSIGNMENTS_FILE, data)

        # Send confirmation to the specific subject channel
        chan_name = SUBJECT_CHANNELS.get(self.subject_value)
        channel = discord.utils.get(interaction.guild.text_channels, name=chan_name)
        
        if channel:
            await bot.send_stacked_embed(channel, task, "📌 NEW ASSIGNMENT LOGGED (Fast)", include_images=False)

        await interaction.response.send_message(f"✅ Assignment logged for **{self.subject_value}** due <t:{ts}:F>.", ephemeral=True)

@bot.tree.command(name="assignment_v2", description="Quickly add an assignment via pop-up form (No Images)")
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
@app_commands.choices(type=[
    app_commands.Choice(name="Lecture", value="Lecture"), 
    app_commands.Choice(name="Section", value="Section")
])
async def assignment_v2(interaction: discord.Interaction, subject: app_commands.Choice[str], type: app_commands.Choice[str]):
    modal = AssignmentModal(subject.value, type.value)
    await interaction.response.send_modal(modal)

@bot.tree.command(name="download", description="Download a TikTok or Instagram reel video")
@app_commands.describe(url="The TikTok or Instagram reel URL to download")
async def download_video(interaction: discord.Interaction, url: str):
    is_instagram = "instagram.com" in url
    is_tiktok = "tiktok.com" in url
    if not is_instagram and not is_tiktok:
        return await interaction.response.send_message(
            "❌ Only TikTok and Instagram reel links are supported.", ephemeral=True
        )
    await bot.process_media(interaction, url, is_slash=True)

@bot.tree.command(name="assignment", description="Add a new assignment")
@app_commands.describe(
    subject="The subject name",
    deadline="Deadline date (YYYY-MM-DD)",
    type="Type of task",
    details="Description or notes",
    image1="First image (optional)",
    image2="Second image (optional)",
    image3="Third image (optional)",
    image4="fourth image (optional)",
    image5="fifth image (optional)",
    image6="sixth image (optional)",
    image7="seventh image (optional)",
    image8="eightth image (optional)",
    image9="nineth image (optional)",
    image10="tenth image (optional)"
)
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
@app_commands.choices(type=[
    app_commands.Choice(name="Lecture", value="Lecture"), 
    app_commands.Choice(name="Section", value="Section")
])
async def add_assignment(
    interaction: discord.Interaction, 
    subject: app_commands.Choice[str], 
    deadline: str, 
    type: app_commands.Choice[str], 
    details: str, 
    image1: discord.Attachment = None,
    image2: discord.Attachment = None,
    image3: discord.Attachment = None,
    image4: discord.Attachment = None,
    image5: discord.Attachment = None,
    image6: discord.Attachment = None,
    image7: discord.Attachment = None,
    image8: discord.Attachment = None,
    image9: discord.Attachment = None,
    image10: discord.Attachment = None
):
    try:
        ts = get_cairo_9am_timestamp(deadline)
    except Exception:
        return await interaction.response.send_message("❌ Invalid Date. Use YYYY-MM-DD format.", ephemeral=True)

    # Collect all valid image URLs
    images = []
    if image1: images.append(image1.url)
    if image2: images.append(image2.url)
    if image3: images.append(image3.url)
    if image4: images.append(image4.url)
    if image5: images.append(image5.url)
    if image6: images.append(image6.url)
    if image7: images.append(image7.url)
    if image8: images.append(image8.url)
    if image9: images.append(image9.url)
    if image10: images.append(image10.url)

    data = load_data(ASSIGNMENTS_FILE)
    
    # Create the task object
    task = {
        "subject": subject.value,
        "timestamp": int(ts),
        "type": type.value,
        "details": details,
        "image_urls": images
    }
    
    data.append(task)
    save_data(ASSIGNMENTS_FILE, data)

    # Send confirmation to the specific subject channel
    chan_name = SUBJECT_CHANNELS.get(subject.value)
    channel = discord.utils.get(interaction.guild.text_channels, name=chan_name)
    
    if channel:
        await bot.send_stacked_embed(channel, task, "📌 NEW ASSIGNMENT LOGGED", include_images=True)

    await interaction.response.send_message(f"✅ Assignment logged for **{subject.name}** due <t:{ts}:F>.", ephemeral=True)


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
    initial_len = len(data)
    
    # Filter out the specific assignment
    new_data = [t for t in data if not (t['subject'] == subject.value and t['timestamp'] == target_ts)]
    
    if len(new_data) == initial_len:
        return await interaction.response.send_message("❌ No active assignment found with that subject and deadline.", ephemeral=True)

    save_data(ASSIGNMENTS_FILE, new_data)
    await interaction.response.send_message(f"✅ Deleted assignment for **{subject.name}** due <t:{target_ts}:F>.", ephemeral=True)


@bot.tree.command(name="edit_assignment", description="Edit details or deadline of an assignment")
@app_commands.describe(
    subject="Current subject",
    old_deadline="Current deadline (YYYY-MM-DD)",
    new_details="New description (leave blank to keep current)",
    new_deadline="New deadline (YYYY-MM-DD) (leave blank to keep current)",
    new_image1="New image 1 (optional)"
)
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
async def edit_assignment(
    interaction: discord.Interaction, 
    subject: app_commands.Choice[str], 
    old_deadline: str, 
    new_details: str = "", 
    new_deadline: str = "",
    new_image1: discord.Attachment = None
):
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
            # Update fields if provided
            if new_details:
                task['details'] = new_details
            
            if new_deadline:
                try:
                    task['timestamp'] = int(get_cairo_9am_timestamp(new_deadline))
                except:
                    return await interaction.response.send_message("❌ Invalid New Date.", ephemeral=True)
            
            if new_image1:
                task['image_urls'] = [new_image1.url] # Replace images for simplicity
                
            break

    if not found:
        return await interaction.response.send_message("❌ Assignment not found.", ephemeral=True)

    save_data(ASSIGNMENTS_FILE, data)
    await interaction.response.send_message("✅ Assignment updated successfully!", ephemeral=True)
    
    # Re-post the updated embed to the channel
    chan_name = SUBJECT_CHANNELS.get(subject.value)
    channel = discord.utils.get(interaction.guild.text_channels, name=chan_name)
    if channel:
        # Find the updated task again to send embed
        for task in data:
            if task['subject'] == subject.value and task['timestamp'] == (int(get_cairo_9am_timestamp(new_deadline)) if new_deadline else old_ts):
                await bot.send_stacked_embed(channel, task, "🔄 ASSIGNMENT UPDATED", include_images=True)
                break

@bot.tree.command(name="list_assignments", description="View all active assignments and quizzes")
async def list_assignments(interaction: discord.Interaction):
    data = load_data(ASSIGNMENTS_FILE)
    
    if not data:
        return await interaction.response.send_message("📭 No active assignments or quizzes found.", ephemeral=True)

    # Sort by timestamp (soonest first)
    sorted_data = sorted(data, key=lambda x: x['timestamp'])
    
    # Group by subject for cleaner display
    subjects = {}
    for task in sorted_data:
        subj = task['subject']
        if subj not in subjects:
            subjects[subj] = []
        subjects[subj].append(task)

    embeds = []
    current_embed = discord.Embed(title="📋 Active Assignments & Quizzes", color=discord.Color.blue())
    char_count = 0
    
    for subj, tasks in subjects.items():
        field_value = ""
        for t in tasks:
            ts = t['timestamp']
            status_icon = "🚨" if ts < time.time() else ("⚠️" if (ts - time.time()) < 86400 else "✅")
            deadline_str = f"<t:{ts}:F>"
            relative_str = f"<t:{ts}:R>"
            
            # Format: [Icon] **Type**: Details | Due: Time
            line = f"{status_icon} **{t.get('type', 'Task')}**: {t['details'][:50]}{'...' if len(t['details'])>50 else ''}\n   └ Due: {deadline_str} ({relative_str})\n\n"
            
            # Check if adding this line exceeds Discord's limit (1024 chars per field)
            if len(field_value) + len(line) > 1024:
                # Add current field to embed
                current_embed.add_field(name=f"📘 {subj.title()}", value=field_value, inline=False)
                char_count += len(field_value)
                
                # If embed is getting full (6000 chars total), start a new one
                if char_count > 5000:
                    embeds.append(current_embed)
                    current_embed = discord.Embed(title="📋 Active Assignments (Cont.)", color=discord.Color.blue())
                    char_count = 0
                
                field_value = line
            else:
                field_value += line
        
        # Add remaining field for this subject
        if field_value:
            current_embed.add_field(name=f"📘 {subj.title()}", value=field_value, inline=False)

    if current_embed.fields:
        embeds.append(current_embed)

    # Send the first embed immediately, then follow up with others if they exist
    await interaction.response.send_message(embed=embeds[0], ephemeral=True)
    for embed in embeds[1:]:
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="view_assignments", description="Browse and view details of manual assignments privately")
async def view_assignments(interaction: discord.Interaction):
    data = load_data(ASSIGNMENTS_FILE)
    
    # Filter out Moodle Quizzes to show only manual assignments
    manual_assignments = [t for t in data if t.get('type') != 'Moodle Quiz']
    
    if not manual_assignments:
        return await interaction.response.send_message("📭 No manual assignments found.", ephemeral=True)

    # Sort by deadline (soonest first)
    sorted_assignments = sorted(manual_assignments, key=lambda x: x['timestamp'])
    
    # Create lookup dictionary for easy retrieval
    assignment_lookup = {str(i): t for i, t in enumerate(sorted_assignments)}
    
    # Create options for the dropdown
    options = []
    for i, t in enumerate(sorted_assignments):
        ts = t['timestamp']
        img_count = len(t.get('image_urls', []))
        img_icon = f" 📷{img_count}" if img_count > 0 else ""
        
        subj_name = t['subject'].title()[:40]
        
        options.append(discord.SelectOption(
            label=f"{subj_name} | <t:{ts}:R>{img_icon}",
            description=f"Due: <t:{ts}:F> | Type: {t.get('type', 'Task')}",
            value=str(i)
        ))

    class AssignmentView(discord.ui.View):
        def __init__(self, lookup):
            super().__init__(timeout=180)
            self.lookup = lookup
            
        @discord.ui.select(
            placeholder="Select an assignment to view details...",
            min_values=1,
            max_values=1,
            options=options
        )
        async def select_assignment(self, interaction: discord.Interaction, select: discord.ui.Select):
            idx = select.values[0]
            task = self.lookup.get(idx)
            
            if not task:
                return await interaction.response.send_message("❌ Error loading assignment.", ephemeral=True)
            
            # Construct the embed manually to ensure it's clean and ephemeral
            col, stat = get_status_ui(task['timestamp'])
            embeds = []
            
            main = discord.Embed(title="📌 ASSIGNMENT DETAILS", color=col)
            main.add_field(name="📘 Subject", value=f"**{task['subject'].upper()}**", inline=False)
            main.add_field(name="📊 Status", value=f"**{stat}**", inline=True)
            main.add_field(name="📍 Type", value=task.get('type', 'Assignment'), inline=True)
            main.add_field(name="⏳ Submission", value=f"<t:{task['timestamp']}:F>\n(<t:{task['timestamp']}:R>)", inline=False)
            main.add_field(name="📝 Details", value=task.get('details', 'No details'), inline=False)
            
            imgs = task.get('image_urls', [])
            if imgs:
                main.set_image(url=imgs[0])
                embeds.append(main)
                # Add additional images as separate embeds if they exist
                for u in imgs[1:]:
                    extra = discord.Embed(color=col)
                    extra.set_image(url=u)
                    embeds.append(extra)
            else:
                embeds.append(main)
                
            # Send as EPHEMERAL followup (Visible ONLY to the user who clicked)
            # No content/mention is added here, just the embeds
            await interaction.response.send_message(embeds=embeds, ephemeral=True)

    # Send the initial message with the dropdown (also ephemeral so it doesn't clutter chat)
    await interaction.response.send_message(
        content="### 📋 Select an Assignment\nChoose below to view its full details and images privately.",
        view=AssignmentView(assignment_lookup),
        ephemeral=True
    )

@bot.tree.command(name="list_quizzes", description="View only active Moodle Quizzes")
async def list_quizzes(interaction: discord.Interaction):
    data = load_data(ASSIGNMENTS_FILE)
    
    # Filter only items where type is 'Moodle Quiz'
    quizzes = [t for t in data if t.get('type') == 'Moodle Quiz']
    
    if not quizzes:
        return await interaction.response.send_message("📭 No active Moodle Quizzes found.", ephemeral=True)

    # Sort by timestamp (soonest first)
    sorted_quizzes = sorted(quizzes, key=lambda x: x['timestamp'])
    
    embed = discord.Embed(title="📝 Active Moodle Quizzes", color=discord.Color.purple())
    
    for q in sorted_quizzes:
        ts = q['timestamp']
        status_icon = "🚨" if ts < time.time() else ("⚠️" if (ts - time.time()) < 86400 else "✅")
        
        # Format details
        name = q['details'].replace("📝 **", "").replace("**", "") # Clean up bold markers if present
        
        field_value = f"{status_icon} **{name}**\n   └ Due: <t:{ts}:F> (<t:{ts}:R>)"
        
        # Add field to embed
        embed.add_field(name=f"📘 {q['subject'].title()}", value=field_value, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="Display the user guide and command list")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🥭 Mango Man User Guide",
        description="Welcome! I am here to streamline your study life at Zingy Academy. Here is how you can abuse me:",
        color=discord.Color.gold()
    )

    # Load Config for Archive Channel Link
    config = load_data(CONFIG_FILE)
    archive_channel_id = config.get("request_channel_id")
    if archive_channel_id:
        archive_link = f"<#{archive_channel_id}>"
    else:
        archive_link = "#setup-archive-channel"

    embed.add_field(
        name="📚 Assignments & Reminders",
        value=(
            "• `/assignment` - Log a new manual deadline with **up to 10 images**.\n"
            "• `/delete_assignment` - Remove an active assignment.\n"
            "• `/edit_assignment` - Update details or deadline of a task.\n"
            "• `/list_assignments` - View all upcoming tasks & quizzes.\n"
            "• `/list_quizzes` - View only Moodle Quizzes.\n"
            "• `/view_assignments` - Browse manual assignments privately."
        ),
        inline=False
    )

    embed.add_field(
        name="📂 Moodle File Archive",
        value=(
            "• **Auto-Sync:** I automatically download new PDFs/PPTs from Moodle.\n"
            f"• **Browse Files:** Use the dropdown menu in {archive_link} to select a subject.\n"
            "• **Download:** Select up to 10 files, and I will DM them to you with professor notes if there are any."
        ),
        inline=False
    )

    embed.add_field(
        name="📱 Social Media Downloader",
        value=(
            "• `/download [url]` - Download TikTok or Instagram Reels without watermarks.\n"
            f"• **Auto-Forward:** Send links in <#{discord.utils.get(interaction.guild.text_channels, name=LISTEN_CHANNEL_NAME).id}> to auto-download."
        ),
        inline=False
    )

    embed.set_footer(text="Mango Man v1.0")
    embed.set_thumbnail(url="https://i.ibb.co/Q7Sss5ps/mangoman.gif")

    # Create the GitHub Button
    github_button = discord.ui.Button(
        label="View Bot's REPO", 
        url="https://github.com/khaled-0110/mango", 
        style=discord.ButtonStyle.link, 
        emoji="🐙"
    )
    
    view = discord.ui.View()
    view.add_item(github_button)

    # 1. Send Embed + Button Publicly
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    
    # 2. Send the Clickable Mention Below
    developer_mention = f"<@{interaction.user.id}>"
    await interaction.followup.send(content=f"Developed by {developer_mention}", ephemeral=False)


@bot.tree.command(name="setup_archive", description="Initialize or update the study archive menu")
async def setup_archive(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    # Initial Data
    library = load_data(LIBRARY_FILE)
    total_files = sum(len(files) for files in library.values())

    embed = discord.Embed(
        title="🏛️ Central Study Archive",
        description=(
            "Welcome to the **Mango Man** file repository!\n\n"
            "📂 **How to abuse:**\n"
            "1. Select a **Subject** from the dropdown below.\n"
            "2. Choose up to **10 files** you need.\n"
            "3. Click **Download** to receive them in your DMs.\n\n"
            f"📊 **Current Status:** {total_files} materials archived.\n"
            f"🔄 **Last Updated:** <t:{int(time.time())}:R>" # Moved here!
        ),
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url="https://i.ibb.co/Q7Sss5ps/mangoman.gif")
    embed.set_footer(text="Mango Man v1.0") # Keep footer clean

    # Create View
    view = PermanentFileViewV2(bot)

    # Send Message
    msg = await interaction.channel.send(embed=embed, view=view)
    
    # Save Config
    save_data(CONFIG_FILE, {
        "master_message_id": msg.id, 
        "request_channel_id": interaction.channel_id
    })

    await interaction.followup.send("✅ Archive menu initialized!", ephemeral=True)

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
    if TELEGRAM_TOKEN: threading.Thread(target=run_telegram, daemon=True).start()
    bot.run(DISCORD_TOKEN)
