import os
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Load environment variables
load_dotenv('.env')

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Google Sheets Configuration
GOOGLE_CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
SHEET_NAME = 'Form Responses 1'
RANGE_NAME = f'{SHEET_NAME}!A:F'
GOOGLE_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# OpenRouter Configuration
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_LLM_VERSION = os.getenv('OPENROUTER_LLM_VERSION', 'anthropic/claude-3-opus-20240229')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Health Check Configuration
SERVICE_URL = os.getenv('SERVICE_URL', 'http://0.0.0.0:8000')
HEALTH_CHECK_PORT = 8000
HEALTH_CHECK_HOST = '0.0.0.0'

# Prompt Configuration
_LLM_PROMPT = None

def get_llm_prompt():
    """Lazily load and return the LLM prompt."""
    global _LLM_PROMPT
    if _LLM_PROMPT is None:
        try:
            with open('prompt.txt', 'r') as f:
                _LLM_PROMPT = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to load prompt.txt: {str(e)}")
    return _LLM_PROMPT 