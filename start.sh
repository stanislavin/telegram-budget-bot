#!/bin/sh
set -e

if [ -n "$TAILSCALE_AUTHKEY" ]; then
    echo "Starting Tailscale..."
    tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock --tun=userspace-networking --outbound-http-proxy-listen=localhost:1055 --socks5-server=localhost:1055 &
    sleep 2
    tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="budget-bot"
    echo "Waiting for Tailscale to connect..."
    tailscale status --peers=false
    echo "Tailscale connected."
else
    echo "TAILSCALE_AUTHKEY not set, skipping Tailscale."
fi

exec python bot.py
