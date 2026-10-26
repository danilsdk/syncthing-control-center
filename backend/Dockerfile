FROM syncthing/syncthing:2.1.7@sha256:2b71da7c0d0a91a6ae5285ad079226fecc0a365d08b27720e1f7037b018f7546 AS syncthing
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/scc
COPY requirements.txt pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN python -m pip install --no-cache-dir --no-deps . \
    && useradd --system --uid 10001 --create-home scc
# The extra build context contains only image scripts, never runtime/private files.
COPY --from=deployment / /opt/deploy/
COPY --from=syncthing /bin/syncthing /usr/local/bin/syncthing
USER 10001:10001
ENTRYPOINT ["python", "/opt/deploy/entrypoint.py"]
CMD ["api"]
