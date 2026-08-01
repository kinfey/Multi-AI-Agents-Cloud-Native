ARG KARS_OPENCLAW_BASE_IMAGE=ghcr.io/azure/openclaw-sandbox:v0.1.25
FROM ${KARS_OPENCLAW_BASE_IMAGE}

USER root
# The renderer bakes Chinese/English text into frames with Pillow, so the image
# must ship a CJK-capable face. The Azure Linux base carries no fonts and its
# repos have no CJK package, so FrameRenderer fails closed with "no CJK font
# found". Fetch Noto Sans CJK (also covers Latin) into a path FrameRenderer
# already scans, so discovery works without extra configuration.
RUN set -eux; \
    mkdir -p /usr/share/fonts/opentype/noto; \
    curl -sSL -o /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
      "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTC/NotoSansCJK-Regular.ttc"; \
    test -s /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc

# VideoComposer shells out to ffmpeg/ffprobe (libx264 + aac + xfade) to compose
# the daily video. The Azure Linux base ships neither binary and its repos carry
# no ffmpeg package (codec licensing), so composition fails with
# "FileNotFoundError: 'ffprobe'". Fetch a static GPL build and keep only the two
# binaries on PATH; the self-check fails the build if either is unusable.
RUN set -eux; \
    tdnf install -y tar xz; \
    curl -sSL -o /tmp/ffmpeg.tar.xz \
      "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"; \
    mkdir -p /tmp/ffx; \
    tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffx --strip-components=1 --wildcards '*/bin/ffmpeg' '*/bin/ffprobe'; \
    install -m 0755 /tmp/ffx/bin/ffmpeg /usr/local/bin/ffmpeg; \
    install -m 0755 /tmp/ffx/bin/ffprobe /usr/local/bin/ffprobe; \
    rm -rf /tmp/ffmpeg.tar.xz /tmp/ffx; \
    ffmpeg -version | head -n 1; \
    ffprobe -version | head -n 1
COPY agents/media-claw-agent/requirements.txt /opt/media-claw-agent/requirements.txt
RUN python3 -m pip install --no-cache-dir \
    -r /opt/media-claw-agent/requirements.txt

COPY agents/media-claw-agent/src /opt/media-claw-agent/src
COPY agents/media-claw-agent/workspace /opt/media-claw-workspace
COPY agents/media-claw-agent/workspace/skills/media-finance-video \
    /opt/clawhub-skills/media-finance-video
COPY agents/media-claw-agent/openclaw-entrypoint.sh /usr/local/bin/media-claw-entrypoint.sh
RUN mv /usr/local/bin/entrypoint.sh /usr/local/bin/kars-openclaw-entrypoint.sh \
    && chmod 0755 /usr/local/bin/media-claw-entrypoint.sh \
    && chmod -R a-w /opt/media-claw-agent/src /opt/media-claw-workspace

ENV PYTHONPATH=/opt/media-claw-agent/src \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.title="Media-Claw OpenClaw Agent" \
      org.opencontainers.image.description="KARS OpenClaw agent for daily Chinese financial video production"

ENTRYPOINT ["/usr/local/bin/media-claw-entrypoint.sh"]
# The wrapper delegates PID 1 to the official KARS OpenClaw entrypoint.
