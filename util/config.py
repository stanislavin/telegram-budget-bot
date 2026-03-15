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

def env_flag(name: str, default: bool = True) -> bool:
    """Parse boolean feature flags from the environment."""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ('1', 'true', 'yes', 'on')

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
RUN_TELEGRAM_BOT = env_flag('RUN_TELEGRAM_BOT', True)

# Google Sheets Configuration
GOOGLE_CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
SHEET_NAME = 'Form Responses 1'
RANGE_NAME = f'{SHEET_NAME}!A:F'
GOOGLE_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Local LLM Configuration (preferred, on tailnet)
LOCAL_LLM_URL = os.getenv('LOCAL_LLM_URL', 'http://localhost:1234/v1/chat/completions')
LOCAL_LLM_MODEL = os.getenv('LOCAL_LLM_MODEL', 'zai-org/glm-4.7-flash')
LOCAL_LLM_TIMEOUT = int(os.getenv('LOCAL_LLM_TIMEOUT', '15'))

# OpenRouter Configuration (fallback)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_LLM_VERSION = os.getenv('OPENROUTER_LLM_VERSION', 'arcee-ai/trinity-mini:free')
OPENROUTER_FALLBACK_MODELS = os.getenv('OPENROUTER_FALLBACK_MODELS', 'z-ai/glm-4.5-air:free,openrouter/aurora-alpha').split(',')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Health Check Configuration
SERVICE_URL = os.getenv('SERVICE_URL', 'http://0.0.0.0:8000')
HEALTH_CHECK_PORT = 8000
HEALTH_CHECK_HOST = '0.0.0.0'

# PostgreSQL Configuration (Neon)
DATABASE_URL = os.getenv('DATABASE_URL')

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
