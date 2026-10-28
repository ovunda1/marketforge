# MarketForge dashboard image (M11). Stage 1 builds the static site with Vite; the
# Overview's numbers are read at build time from the committed results files
# (bench/results, results/, research/showcase, the Tiingo manifest: dashboard/facts.config.ts).
# Stage 2 is nginx: it serves the site and proxies /api and /ws to the gateway
# (deploy/nginx.conf), overwriting X-Forwarded-For with the address it saw.
FROM node:22-alpine AS build
WORKDIR /src/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY bench/results /src/bench/results
COPY results /src/results
COPY research/showcase /src/research/showcase
COPY research/data/manifests /src/research/data/manifests
COPY tests/fixtures /src/tests/fixtures
COPY dashboard ./
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dashboard/dist /usr/share/nginx/html
EXPOSE 80
