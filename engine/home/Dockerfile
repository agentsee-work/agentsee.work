# The engine's image: Python, the 1Password CLI, one Python package and one
# font. The code is not in the image; the repo is mounted at run time
# (docker-compose.yml), so a deploy is `git pull` and a restart, and this
# image is rebuilt only when the interpreter, the op CLI or that package
# should move.
#
# The same Python as the workstations, on purpose: the engine is tested on
# 3.14, and Debian's 3.11 is not what the tests ran on.
FROM python:3.14-slim

# The op CLI from 1Password's own repository (there is no Debian package).
# gnupg is needed only to dearmor the key and is removed again. DejaVu is
# the TrueType font the release's PDF embeds (engine/release.py), so a
# guest's name is set in whatever alphabet it is written in.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -sS https://downloads.1password.com/linux/keys/1password.asc \
      | gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" \
      > /etc/apt/sources.list.d/1password.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends 1password-cli fonts-dejavu-core; \
    apt-get purge -y --auto-remove gnupg curl; \
    rm -rf /var/lib/apt/lists/*

# fpdf2 makes the release's signed copy (engine/release.py): the engine's one
# dependency beyond the standard library, pinned here and in
# .github/workflows/check.yml, where the suite renders a PDF with it.
RUN pip install --no-cache-dir fpdf2==2.8.9

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
