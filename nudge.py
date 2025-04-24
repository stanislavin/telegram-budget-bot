import requests
import time
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env')

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get service URL from environment variable, fallback to localhost for development
SERVICE_URL = os.getenv('SERVICE_URL', 'http://0.0.0.0:8000')

def ping_nudge():
    """Ping the /nudge endpoint."""
    try:
        # Use the full service URL
        response = requests.get(f"{SERVICE_URL}/nudge")
        if response.status_code == 200:
            logger.info("Successfully pinged /nudge endpoint")
        else:
            logger.error(f"Failed to ping /nudge endpoint: {response.status_code}")
    except Exception as e:
        logger.error(f"Error pinging /nudge endpoint: {str(e)}")

def main():
    """Run the nudge pinger every minute."""
    logger.info(f"Starting nudge pinger for {SERVICE_URL}...")
    while True:
        ping_nudge()
        time.sleep(60)  # Sleep for 1 minute

if __name__ == '__main__':
    main() 