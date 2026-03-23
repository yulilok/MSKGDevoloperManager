import os
from dotenv import load_dotenv

load_dotenv()

# Discord Bot Token
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
if not DISCORD_TOKEN or DISCORD_TOKEN.startswith('your_'):
    raise ValueError('DISCORD_TOKEN не задан. Заполните .env с DISCORD_TOKEN=...')

# Google Sheets
SHEET_ID = os.getenv('SHEET_ID')
if not SHEET_ID or SHEET_ID.startswith('your_'):
    raise ValueError('SHEET_ID не задан. Заполните .env с SHEET_ID=...')

CREDENTIALS_PATH = os.getenv('CREDENTIALS_PATH') or 'credentials.json'  # Path to your Google Sheets API credentials JSON file
if not os.path.exists(CREDENTIALS_PATH):
    raise FileNotFoundError(f"Файл {CREDENTIALS_PATH} не найден. Поместите credentials.json в корень проекта.")

# Optional guild ID for faster command registration when testing
GUILD_ID = int(os.getenv('GUILD_ID', '0') or 0)

# Channel IDs (optional, for specific channels)
IDEAS_CHANNEL_ID = 1485642925252677683  # Channel for submitting ideas
APPROVAL_CHANNEL_ID = 1485642952641613954  # Channel for approvals
TASKS_CHANNEL_ID = 1485684679960035448  # Channel for approved tasks