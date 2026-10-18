# Build context: the verified OpenMAIC v1.2.0 checkout after patch_openmaic.py.
# Its .dockerignore excludes .env*, openmaic.yml and all local credentials.
FROM node:24-bookworm-slim AS builder
WORKDIR /app
RUN npm install --global pnpm@10.28.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ ./packages/
COPY scripts/ ./scripts/
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY . .
RUN NODE_OPTIONS=--max-old-space-size=1024 pnpm run build:packages
RUN NODE_OPTIONS=--max-old-space-size=4096 pnpm build

FROM node:24-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
RUN apt-get update && apt-get install -y --no-install-recommends libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libjpeg62-turbo libgif7 librsvg2-2 && rm -rf /var/lib/apt/lists/*
COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
RUN mkdir -p /app/data && chown -R node:node /app/data
USER node
EXPOSE 3000
CMD ["node", "server.js"]
