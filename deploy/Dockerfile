# MarketForge services image (M10): one image for the matcher, projector, gateway,
# backtest worker and migrations; compose picks the command.
#
# Stage 1 builds the engine and the mfengine extension into a wheel with
# scikit-build-core (Release, invariant walk off: the live configuration). Stage 2 is a
# slim runtime with the wheel and the services' dependencies, no compiler. No secret
# is passed at build time: the database password and the Tiingo key arrive as runtime
# environment from compose (.env), and .dockerignore keeps .env out of the context.

FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ make \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "cmake>=3.24" ninja "scikit-build-core>=0.10"
WORKDIR /src
COPY CMakeLists.txt pyproject.toml ./
COPY engine engine
COPY bindings bindings
COPY reference reference
COPY research research
COPY services services
RUN pip wheel --no-deps --no-build-isolation -w /wheels \
      -Ccmake.build-type=Release -Ccmake.define.MF_CHECK_INVARIANTS=OFF -Cbuild-dir=/tmp/build . \
 && python -c "import glob; print(glob.glob('/wheels/*.whl'))"

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    MF_INSTRUMENTS=/app/deploy/config/instruments.yaml \
    MF_DATA_DIR=/var/lib/marketforge/data \
    MF_RUNS_DIR=/var/lib/marketforge/runs
COPY --from=build /wheels /wheels
RUN wheel="$(ls /wheels/marketforge-*.whl)" && pip install "${wheel}[services]" && rm -rf /wheels \
 && python -c "import mfengine; assert not mfengine.INVARIANT_CHECKS; print('mfengine', mfengine.BUILD_TYPE)"
RUN useradd --system --uid 10001 --home-dir /app mf \
 && mkdir -p /app /var/lib/marketforge/data /var/lib/marketforge/runs /tmp/prom \
 && chown -R mf /app /var/lib/marketforge /tmp/prom
WORKDIR /app
COPY deploy/config /app/deploy/config
USER mf
EXPOSE 8000
CMD ["python", "-m", "services.matcher"]
