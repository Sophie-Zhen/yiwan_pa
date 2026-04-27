# Multi-arch base — Docker pulls the right variant for your host (arm64 on
# Raspberry Pi 4/5, x86_64 on a Mac/cloud VM, etc.). The "slim" tag drops
# build tools and locales, ~150MB instead of ~1GB for the full image.
FROM python:3.12-slim

WORKDIR /app

# Install Python deps in a separate layer ahead of copying the code.
# Docker caches each layer; this means a code-only change reuses this layer
# instead of reinstalling dependencies on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project. The .dockerignore excludes .env, .git,
# data/inbox.md, etc. so they don't bake into the image.
COPY . .

# `python -u` disables stdout buffering — log lines appear in
# `docker logs` immediately instead of being held until the buffer fills.
CMD ["python", "-u", "bot.py"]
