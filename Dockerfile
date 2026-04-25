FROM tailscale/tailscale:latest AS tailscale

FROM python:3.13-slim-bookworm

WORKDIR /app

# Copy Tailscale binaries from official image (avoids apt-get)
COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py prompt.txt start.sh ./
RUN chmod +x start.sh
COPY util/ util/
COPY web/ web/

# The container expects these env vars at runtime:
#   TELEGRAM_BOT_TOKEN
#   DATABASE_URL             (PostgreSQL connection string)
#   OPENROUTER_API_KEY
#   OPENROUTER_LLM_VERSION   (optional)
#   SERVICE_URL              (optional, for health-check nudge)
#   TAILSCALE_AUTHKEY        (optional, Tailscale auth key for joining tailnet)

CMD ["./start.sh"]
