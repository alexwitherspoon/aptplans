FROM caddy:2-alpine

RUN apk add --no-cache curl

COPY docker/Caddyfile /etc/caddy/Caddyfile
RUN mkdir -p /srv/site

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD curl -fsS http://127.0.0.1/ >/dev/null
