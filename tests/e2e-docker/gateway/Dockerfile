# syntax=docker/dockerfile:1
#
# captive-gateway — simulates the always-on intercept that a hotel / airport /
# coffee-shop AP performs:
#   * dnsmasq answers every A query with the gateway IP (real DNS hijack
#     behavior) AND hands out DHCP leases on the captive subnet.
#   * nginx listens :80 default_server on every Host: header, returns
#     302 -> /portal for arbitrary URLs, proxies the well-known mock portal
#     endpoints through to the existing mockportal Python server.
#   * mockportal runs on 127.0.0.1:18080 inside the container — never exposed
#     to the captive subnet, since it returns request bodies + headers via
#     /log and we don't want callers exfiltrating their own probes.
#
FROM alpine:3.20

RUN apk add --no-cache \
        bash \
        dnsmasq \
        nginx \
        python3 \
        tini \
    && mkdir -p /run/nginx /etc/dnsmasq.d

# Bring in the existing mockportal package verbatim — single source of truth
# for the portal HTML, /generate_204 sequencing, /login flow, /log, /reset.
COPY mockportal /opt/mockportal/mockportal
COPY tests/e2e-docker/gateway/dnsmasq.conf /etc/dnsmasq.conf
COPY tests/e2e-docker/gateway/nginx.conf   /etc/nginx/nginx.conf
COPY tests/e2e-docker/gateway/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Captive subnet pins:
#   gateway IP: 172.30.0.2   (compose-assigned)
#   client lease range: 172.30.0.50–172.30.0.150
#   dnsmasq listens on :53 (UDP+TCP)
#   nginx listens on :80
EXPOSE 53/udp 53/tcp 80/tcp 67/udp

ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
