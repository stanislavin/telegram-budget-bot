import logging

from util.health import start_health_check
from util.telegram import telegram_start_polling
from util.config import logging

logger = logging.getLogger(__name__)

def main():
    """Start the bot."""
    # Start health check server
    start_health_check()
    
    # Create and start the Telegram application
    telegram_start_polling()

if __name__ == '__main__':
    main() 