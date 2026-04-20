# 🥭 Mango Man - Advanced Student Assistant Bot

<div align="center">
  <img src="https://i.ibb.co/Q7Sss5ps/mangoman.gif" alt="Mango Man PFP" width="200" style="border-radius: 50%;">
</div>

<br>

<p align="center">
  <strong>Mango Man</strong> is a multifunctional Discord bot designed to streamline student life at Modern Academy. It integrates directly with Moodle LMS, manages assignments, downloads social media content, and provides an organized file archive.
</p>

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

### 📂 Central Study Archive
- **Persistent Library:** Stores all downloaded Moodle files in a searchable JSON database.
- **Interactive UI:** Users can browse subjects via dropdown menus, select up to 5 files, and receive them directly via DM.
- **Metadata Support:** Includes professor instructions/descriptions with downloaded files for context.

### 📱 Social Media Downloader
- **TikTok & Instagram:** Download reels and videos without watermarks using RapidAPI.
- **Direct Links:** Users can send links in `#links-inbox` or use the `/download` command.
- **Telegram Bridge:** Forwards TikTok/Instagram links from a Telegram group to Discord seamlessly.

### 🛠️ Admin & Utility Tools
- **DM Logging:** Logs all bot DMs to a private admin channel for support and monitoring.
- **Manual Assignments:** Staff can add manual deadlines using `/assignment` with image attachments.
- **Process Management:** Uses robust shell scripts (`start_bot.sh`, `stop_bot.sh`) for reliable Linux deployment.

## 🚀 Installation & Setup

### Prerequisites
- Python 3.8+
- A Discord Bot Token
- Moodle Web Service Token
- RapidAPI Keys (for TikTok/Instagram)

### Quick Start

1. **Clone the Repository**
   ```bash
   git clone https://github.com/KhaledNasserFathala/Mango-Man-Bot.git
   cd Mango-Man-Bot
