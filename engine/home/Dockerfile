# The engine's image: Python, the 1Password CLI, nothing else. The code is
# not in the image; the repo is mounted at run time (docker-compose.yml), so
# a deploy is `git pull` and a restart, and this image is rebuilt only when
# the interpreter or the op CLI should move.
#
# The same Python as the workstations, on purpose: the engine is stdlib only
# and tested on 3.14, and Debian's 3.11 is not what the tests ran on.
FROM python:3.14-slim

# The op CLI from 1Password's own repository (there is no Debian package).
# gnupg is needed only to dearmor the key and is removed again.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -sS https://downloads.1password.com/linux/keys/1password.asc \
      | gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" \
      > /etc/apt/sources.list.d/1password.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends 1password-cli; \
    apt-get purge -y --auto-remove gnupg curl; \
    rm -rf /var/lib/apt/lists/*

# Not root. uid 2001 is also what owns /var/lib/agentsee-engine on the host
# (README). 2000 is Stalwart's.
RUN useradd --system --uid 2001 --home-dir /var/lib/agentsee-engine --shell /usr/sbin/nologin engine
USER engine

WORKDIR /opt/agentsee/repo
ENV ENGINE_DATA=/var/lib/agentsee-engine \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8877

# op run resolves engine/engine.env against the service account whose token
# the env file gives the container (OP_SERVICE_ACCOUNT_TOKEN), then execs
# the engine with the credentials in its environment and nowhere on disk.
# 0.0.0.0 because the only way in is cloudflared on the compose network; the
# port is not published to the host.
CMD ["op", "run", "--env-file=engine/engine.env", "--", "python3", "engine/engine.py", "serve", "--host", "0.0.0.0", "--port", "8877"]
