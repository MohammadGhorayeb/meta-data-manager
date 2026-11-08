# The "everything included" install: one exact set of programs.
#
# The strongest cleans re-encode through ffmpeg, lame, poppler and LibreOffice, and
# two installs with different versions make different bytes -- so a set of cleaned
# files could show which setup cleaned them (E-INSTALL, docs/limits.md #88). Everyone
# using this image has the same programs, the same versions and the same fonts.
#
# Every package that shapes an output is pinned to an exact version. When one
# disappears from the archive the build FAILS, on purpose: a different version is a
# different fingerprint, so bump it knowingly and re-run scripts/e_install.py.
#
#   docker build -t scrub .
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work scrub in.jpg out.jpg
#   docker run --rm scrub doctor
FROM ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        python3=3.12.3-0ubuntu2.1 \
        python3-venv=3.12.3-0ubuntu2.1 \
        ffmpeg=7:6.1.1-3ubuntu5 \
        lame=3.100-6build1 \
        libjpeg-turbo-progs=2.1.5-2ubuntu2 \
        poppler-utils=24.02.0-1ubuntu9.11 \
        libreoffice-writer=4:24.2.7-0ubuntu0.24.04.7 \
        libimage-exiftool-perl=12.76+dfsg-1 \
        fonts-liberation=1:2.1.5-3 \
    && rm -rf /var/lib/apt/lists

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/scrub \
    && /opt/scrub/bin/pip install --no-cache-dir -q -r requirements.txt
COPY src ./src

# Any user id can run this (`--user`), so nothing may depend on a home directory.
ENV HOME=/tmp PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
USER 1000
WORKDIR /work
ENTRYPOINT ["/opt/scrub/bin/python", "-m", "src.scrub"]
