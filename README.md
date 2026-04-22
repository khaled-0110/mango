# 🥭 Mango Man - Advanced Student Assistant Bot

<div align="center">
  <img src="https://i.ibb.co/Q7Sss5ps/mangoman.gif" alt="Mango Man PFP" width="200" style="border-radius: 50%;">
</div>

<br>

<p align="center">
  <strong>Mango Man</strong> is a multifunctional Discord bot designed to streamline student life at Modern Academy. It integrates directly with Moodle LMS, manages assignments, downloads social media content, and provides an organized file archive—all wrapped in a sleek, automated interface.
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#tech-stack">Tech Stack</a> •
  <a href="#license">License</a>
</p>

---

## ✨ Features

### 📚 Moodle Integration & ETL Pipeline
- **Auto-Discovery:** Automatically detects new PDFs, PPTs, and resources uploaded to Moodle courses.
- **Smart Notifications:** Sends files directly to subject-specific Discord channels with proper formatting.
- **Professor's Notes:** Extracts and displays any text/descriptions added by professors alongside files (HTML cleaning included).
- **Quiz Detection:** Automatically tracks Moodle quizzes and adds them to the reminder system.

### ⏰ Precision Reminder System
- **Daily Alerts:** Sends reminders every day at 9:00 AM Cairo time until the deadline passes.
- **Urgent Warnings:** Sends a "Final Warning" exactly 90 minutes before a deadline.
- **Status Tracking:** Visual indicators for "On Track" (🟢), "Due Soon" (🟠), and "Overdue" (🔴).

### 📂 Central Study Archive (V2)
- **Persistent Library:** Stores all downloaded Moodle files in a searchable JSON database.
- **Interactive UI:** Users can browse subjects via dropdown menus, select up to 5 files, and receive them directly via DM.
- **Auto-Updating Stats:** The archive message automatically updates its file count and "Last Updated" timer every 30 minutes.
- **Metadata Support:** Includes professor instructions/descriptions with downloaded files for context.

### 📱 Social Media Downloader
- **TikTok & Instagram:** Download reels and videos without watermarks using RapidAPI.
- **Direct Links:** Users can send links in `#links-inbox` or use the `/download` command.
- **Telegram Bridge:** Forwards TikTok/Instagram links from a Telegram group to Discord seamlessly.

### 🛠️ Admin & Utility Tools
- **Dual Assignment System:** 
  - `/assignment`: Add tasks with **up to 3 images**.
  - `/assignment_v2`: Quick-add tasks via a **pop-up form**.
- **Management Commands:** `/edit_assignment`, `/delete_assignment`, `/list_assignments`, and `/list_quizzes`.
- **DM Logging:** Logs all bot DMs to a private admin channel for support and monitoring.
- **Process Management:** Uses robust shell scripts (`start_bot.sh`, `stop_bot.sh`) for reliable Linux deployment.

---

## 🚀 Installation & Setup

### Prerequisites
- Python 3.8+
- A Discord Bot Token
- Moodle Web Service Token
- RapidAPI Keys (for TikTok/Instagram)

### Quick Start

1. **Clone the Repository**
   ```bash
   git clone https://github.com/khaled-0110/mango
   cd Mango-Man-Bot
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**
   Create a `.env` file in the root directory based on `.env.example`:
   ```env
   DISCORD_BOT_TOKEN=your_discord_bot_token
   GUILD_ID=your_server_id
   INSTAGRAM_API_KEY=your_rapidapi_key
   # ... see .env.example for full list
   ```

4. **Run the Bot**
   Use the provided shell scripts for reliable process management on Linux:
   ```bash
   chmod +x start_bot.sh stop_bot.sh
   ./start_bot.sh
   ```

---

## 🛠️ Tech Stack

- **Language:** Python 3.10+
- **Libraries:** `discord.py` (v2.4+), `python-telegram-bot`, `requests`, `pytz`, `asyncio`
- **Deployment:** Linux VM (Azure), Bash Scripting, NoHup Process Management
- **Data Storage:** JSON-based local storage for assignments, cache, and alerts
- **UI Components:** Discord Components V2 (Select Menus, Modals, Buttons)

## 📁 Project Structure

```text
Mango-Man-Bot/
├── bot_main.py          # Main bot logic and event loops
├── start_bot.sh         # Startup script with logging
├── stop_bot.sh          # Graceful shutdown script
├── requirements.txt     # Python dependencies
├── .env.example         # Template for environment variables
├── .gitignore           # Excludes secrets and logs
└── README.md            # This file
```

## 🛡️ Security

- **Secrets Management:** All API tokens are stored in `.env` and excluded from Git via `.gitignore`.
- **Error Handling:** Robust try-except blocks and logging ensure the bot stays online even if APIs fail.

## 📝 License

This project is open-source for educational purposes.

---

<div align="center">
  <sub>Built with ❤️ by <a href="https://www.linkedin.com/in/khalednasserfathala">Khaled Nasser</a></sub>
</div>
