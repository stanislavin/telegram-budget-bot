FROM python:3.13-slim-bookworm

WORKDIR /app

# Install git, Tailscale, and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    curl \
    iptables \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY .git .git
COPY bot.py prompt.txt start.sh ./
RUN chmod +x start.sh
COPY util/ util/
COPY web/ web/

# The container expects these env vars at runtime:
#   TELEGRAM_BOT_TOKEN
#   GOOGLE_SHEET_ID
#   OPENROUTER_API_KEY
#   GOOGLE_CREDENTIALS_PATH  (path to the mounted credentials file)
#   OPENROUTER_LLM_VERSION   (optional)
#   SERVICE_URL              (optional, for health-check nudge)
#   TAILSCALE_AUTHKEY        (optional, Tailscale auth key for joining tailnet)

CMD ["./start.sh"]
