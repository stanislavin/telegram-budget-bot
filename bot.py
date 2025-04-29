import logging

from util.health import start_health_check, start_nudge
from util.telegram import start_telegram_polling

logger = logging.getLogger(__name__)

def main():
    """Start the bot."""
    # Start health check server and nudge service
    start_health_check()

    # Start nudge pinger (workaround to avoid sleep in koyeb)
    start_nudge()
    
    # Start polling telegram bot
    start_telegram_polling()

if __name__ == '__main__':
    main() 