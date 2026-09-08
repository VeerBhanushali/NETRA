# NETRA dashboard. Multi-stage so the runtime image ships no build tools.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json ./
RUN npm install --omit=dev --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
COPY apps/web/package.json ./
RUN npm install --no-audit --no-fund
COPY apps/web/ ./
# Standalone output keeps the runtime image small and lets it start
# without node_modules present.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
