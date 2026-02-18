FROM python:3.13-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py prompt.txt ./
COPY util/ util/

# The container expects these env vars at runtime:
#   TELEGRAM_BOT_TOKEN
#   GOOGLE_SHEET_ID
#   OPENROUTER_API_KEY
#   GOOGLE_CREDENTIALS_PATH  (path to the mounted credentials file)
#   OPENROUTER_LLM_VERSION   (optional)
#   SERVICE_URL              (optional, for health-check nudge)

CMD ["python", "bot.py"]
