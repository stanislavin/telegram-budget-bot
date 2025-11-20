import logging

from util.health import start_health_check, start_nudge
from util.telegram import start_telegram_polling
from util.config import (
    RUN_TELEGRAM_BOT,
    SERVICE_URL,
    HEALTH_CHECK_HOST,
    HEALTH_CHECK_PORT,
    GOOGLE_SHEET_ID,
    OPENROUTER_LLM_VERSION,
)

logger = logging.getLogger(__name__)

def main():
    """Start the bot."""
    logger.info(
        "Startup config:\n"
        "RUN_TELEGRAM_BOT: %s\n"
        "HEALTH_CHECK_HOST: %s\n"
        "HEALTH_CHECK_PORT: %s\n"
        "SERVICE_URL: %s\n"
        "GOOGLE_SHEET_ID: %s\n"
        "OPENROUTER_LLM_VERSION: %s",
        str(RUN_TELEGRAM_BOT).lower(),
        HEALTH_CHECK_HOST,
        HEALTH_CHECK_PORT,
        SERVICE_URL,
        GOOGLE_SHEET_ID or "<missing>",
        OPENROUTER_LLM_VERSION,
    )
    # Start health check server and nudge service
    health_thread = start_health_check()
    
    # Start nudge pinger (workaround to avoid sleep in koyeb)
    start_nudge()
    
    # Start polling telegram bot if enabled
    if RUN_TELEGRAM_BOT:
        start_telegram_polling()
    else:
        logger.info("RUN_TELEGRAM_BOT is disabled; only health/web interface is running.")
        # Keep process alive to serve HTTP
        health_thread.join()

if __name__ == '__main__':
    main() 
