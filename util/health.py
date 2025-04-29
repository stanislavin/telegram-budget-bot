import logging
import requests
import asyncio
import time
from flask import Flask
from threading import Thread

from util.config import SERVICE_URL, HEALTH_CHECK_PORT, HEALTH_CHECK_HOST

logger = logging.getLogger(__name__)

def start_health_check():
    """Start the Flask server for health checks."""
    app = Flask(__name__)
    
    @app.route('/health')
    def health_check():
        return 'OK', 200
    
    @app.route('/nudge')
    def nudge():
        """Endpoint to keep the service alive."""
        return 'OK', 200
    
    def run_flask():
        app.run(host=HEALTH_CHECK_HOST, port=HEALTH_CHECK_PORT)
    
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Health check server started")

def nudge_pinger():
    """Run the nudge pinger in a separate thread."""
    nudge_url = f"{SERVICE_URL}/nudge"
    logger.info(f"Starting nudge pinger for {nudge_url}...")
    
    while True:
        try:
            response = requests.get(nudge_url)
            if response.status_code == 200:
                logger.info(f"Successfully pinged {nudge_url}")
            else:
                logger.error(f"Failed to ping {nudge_url}: {response.status_code}")
        except Exception as e:
            logger.error(f"Error pinging {nudge_url}: {str(e)}")
        time.sleep(60)  # Sleep for 1 minute

def start_nudge():
    """Run nudge pinger in a separate thread."""
    nudge_thread = Thread(target=nudge_pinger)
    nudge_thread.daemon = True
    nudge_thread.start() 