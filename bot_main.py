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

# === 4. INTERACTIVE UI ===
class FileSelectionView(discord.ui.View):
    def __init__(self, lookup, options):
        super().__init__(timeout=180)
        self.lookup = lookup
        self.options = options
        self.selected_indices = []
        self.file_selector = discord.ui.Select(
            placeholder="Step 1: Pick up to 5 files...",
            min_values=1,
            max_values=min(5, len(options)),
            options=options
        )
        self.file_selector.callback = self.pick_files_callback
        self.add_item(self.file_selector)

    async def pick_files_callback(self, interaction: discord.Interaction):
        self.selected_indices = self.file_selector.values
        self.confirm_btn.disabled = False
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Step 2: Download Selected", style=discord.ButtonStyle.success, emoji="📥", disabled=True)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        success_count = 0
        loop = asyncio.get_event_loop()

        for idx in self.selected_indices:
            f_data = self.lookup.get(idx)
            if not f_data:
                continue
            try:
                res = await loop.run_in_executor(None, lambda u=f_data['url']: requests.get(u, timeout=20))
                path = TEMP_DIR / f_data['name']
                with open(path, "wb") as fh:
                    fh.write(res.content)

                desc = f_data.get('description', '')
                content_msg = f"📄 **Archive Request:** {f_data['name']}"
                if desc: content_msg += f"\n\n📝 **Instructions:**\n{desc}"

                await interaction.user.send(content=content_msg, file=discord.File(path))
                path.unlink()
                success_count += 1
            except Exception:
                pass

        await interaction.followup.send(f"✅ Successfully sent {success_count} files to your DMs!", ephemeral=True)
        self.stop()

class PermanentFileView(discord.ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot = bot_instance

    @discord.ui.select(
        custom_id="permanent_subject_select",
        placeholder="📂 Browse Subject Archives...",
        options=[discord.SelectOption(label=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()]
    )
    async def select_subject(self, interaction: discord.Interaction, select: discord.ui.Select):
        library = load_data(LIBRARY_FILE)
        subject = select.values[0]
        files = library.get(subject, [])

        if not files:
            return await interaction.response.send_message(f"No files found for **{subject}** yet.", ephemeral=True)

        sorted_files = sorted(files, key=lambda x: x['timestamp'], reverse=True)[:25]
        file_lookup = {str(i): f for i, f in enumerate(sorted_files)}

        options = []
        for i, f in file_lookup.items():
            date_str = datetime.fromtimestamp(f['timestamp']).strftime('%d/%b %H:%M')
            options.append(discord.SelectOption(
                label=(f['name'][:85] + (" 📝" if f.get('description') else "")),
                description=f"Uploaded: {date_str}",
                value=i
            ))

        await interaction.response.send_message(
            content=f"### 📂 {subject.upper()} Archive\nSelect files to receive them in DMs.",
            view=FileSelectionView(file_lookup, options),
            ephemeral=True
        )

# === 5. MAIN BOT ENGINE ===
class GigaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(PermanentFileView(self))
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
        chan = self.get_channel(config.get('request_channel_id'))
        if not chan: return
        try:
            msg = await chan.fetch_message(config.get('master_message_id'))
            lib = load_data(LIBRARY_FILE)
            total = sum(len(v) for v in lib.values())
            embed = discord.Embed(
                title="🏛️ Central Study Archive",
                description=f"📂 **Total Materials:** {total}\n🔄 **Last Update:** <t:{int(time.time())}:R>",
                color=discord.Color.blue()
            )
            await msg.edit(embed=embed, view=PermanentFileView(self))
        except Exception:
            pass

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

@bot.tree.command(name="assignment", description="Add assignment")
@app_commands.choices(subject=[app_commands.Choice(name=k.title(), value=k) for k in SUBJECT_CHANNELS.keys()])
@app_commands.choices(type=[app_commands.Choice(name="Lecture", value="Lecture"), app_commands.Choice(name="Section", value="Section")])
async def add_assignment(interaction: discord.Interaction, subject: app_commands.Choice[str], deadline: str, type: app_commands.Choice[str], details: str, image1: discord.Attachment = None):
    try:
        ts = get_cairo_9am_timestamp(deadline)
    except Exception:
        return await interaction.response.send_message("❌ Use YYYY-MM-DD.", ephemeral=True)

    data = load_data(ASSIGNMENTS_FILE)
    task = {
        "subject": subject.value,
        "timestamp": int(ts),
        "type": type.value,
        "details": details,
        "image_urls": [image1.url] if image1 else []
    }
    data.append(task)
    save_data(ASSIGNMENTS_FILE, data)

    chan_name = SUBJECT_CHANNELS.get(subject.value)
    channel = discord.utils.get(interaction.guild.text_channels, name=chan_name)
    if channel:
        await bot.send_stacked_embed(channel, task, "📌 NEW ASSIGNMENT LOGGED")

    await interaction.response.send_message("✅ Logged!", ephemeral=True)

@bot.tree.command(name="setup_archive", description="Init archive")
async def setup_archive(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admins only!", ephemeral=True)
    msg = await interaction.channel.send(embed=discord.Embed(title="🏛️ Archive"), view=PermanentFileView(bot))
    save_data(CONFIG_FILE, {"master_message_id": msg.id, "request_channel_id": interaction.channel_id})
    await interaction.response.send_message("✅ Done!", ephemeral=True)

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
