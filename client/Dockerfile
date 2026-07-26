FROM node:24-slim AS build

WORKDIR /app
RUN corepack enable
ENV PNPM_CONFIG_MINIMUM_RELEASE_AGE=0
ENV CI=true

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
