import logging
import requests
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

def nudge_service():
    """Ping the service endpoint to keep it alive."""
    nudge_url = f"{SERVICE_URL}/nudge"
    logger.info(f"Pinging {nudge_url}...")
    
    try:
        response = requests.get(nudge_url)
        if response.status_code == 200:
            logger.info(f"Successfully pinged {nudge_url}")
        else:
            logger.error(f"Failed to ping {nudge_url}: {response.status_code}")
    except Exception as e:
        logger.error(f"Error pinging {nudge_url}: {str(e)}") 