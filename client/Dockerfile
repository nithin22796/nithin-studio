FROM node:24-slim AS build

WORKDIR /app
RUN corepack enable
ENV PNPM_CONFIG_MINIMUM_RELEASE_AGE=0
ENV CI=true

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# Optional override for the committed client/.env's VITE_API_URL — Vite
# bakes this in at build time, and an already-set env var here wins over
# the .env file's value (dotenv doesn't clobber existing env vars). Lets
# docker-compose.yml point this build at wherever the Docker server is
# actually served, independent of whatever native dev mode (`run.sh`) uses.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=${VITE_API_URL}

RUN pnpm build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
