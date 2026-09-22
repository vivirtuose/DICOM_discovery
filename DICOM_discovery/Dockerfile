# DICOM_discovery — container image for hospital NAS / servers (Synology Container Manager,
# QNAP Container Station, TrueNAS, any Docker/Podman host). Research Use Only.
#
# The image needs the network only at BUILD time. At run time it is meant to run with
# `--network none`, the DICOM share mounted read-only on /data and a writable /output
# (see deploy/nas/docker-compose.yml and docs/NAS_DEPLOYMENT.md).
#
#   docker build -t dicom-discovery:0.9.0 .
#   docker run --rm --network none -v /volume1/DICOM:/data:ro -v /volume1/qc:/output dicom-discovery:0.9.0

ARG PYTHON_VERSION=3.12

# ---- build: turn the package and its dependencies into wheels ---------------------------
FROM python:${PYTHON_VERSION}-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- runtime -------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim

LABEL org.opencontainers.image.title="DICOM_discovery" \
      org.opencontainers.image.description="Cohort-level QC for longitudinal DICOM-RT data (Research Use Only)" \
      org.opencontainers.image.source="https://github.com/vivirtuose/DICOM_discovery" \
      org.opencontainers.image.licenses="MIT"

# HOME=/tmp: the container may run under any NAS uid:gid (PUID/PGID) with a read-only root
# filesystem; nothing is ever written outside /output and /tmp.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels DICOM_discovery \
 && rm -rf /wheels \
 && groupadd --gid 1000 dicom \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin dicom \
 && mkdir -p /data /output /opt/dicom-discovery \
 && chown dicom:dicom /output

COPY protocol.brain_rt_followup.yaml /opt/dicom-discovery/

USER dicom
WORKDIR /output
VOLUME ["/output"]

ENTRYPOINT ["dicom-discovery"]
# Default = the scheduled NAS run. Override with any subcommand, e.g. `report --dry-run ...`.
CMD ["job", "--root", "/data", "--output-dir", "/output"]
