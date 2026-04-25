FROM tailscale/tailscale:latest AS tailscale

# Fetch recent commits via GitHub API (works with shallow clones)
FROM alpine:latest AS git-info
RUN apk add --no-cache curl jq
ARG GITHUB_REPO=stanislavin/telegram-budget-bot
RUN curl -sf "https://api.github.com/repos/${GITHUB_REPO}/commits?per_page=3" \
    | jq -r '.[] | "\(.sha[0:7]) \(.commit.message | split("\n")[0])"' \
    > /tmp/git_recent_commits 2>/dev/null || echo '' > /tmp/git_recent_commits

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

# Bake recent commits from git-info stage
COPY --from=git-info /tmp/git_recent_commits /tmp/git_recent_commits

# The container expects these env vars at runtime:
#   TELEGRAM_BOT_TOKEN
#   DATABASE_URL             (PostgreSQL connection string)
#   OPENROUTER_API_KEY
#   OPENROUTER_LLM_VERSION   (optional)
#   SERVICE_URL              (optional, for health-check nudge)
#   TAILSCALE_AUTHKEY        (optional, Tailscale auth key for joining tailnet)

CMD ["./start.sh"]
