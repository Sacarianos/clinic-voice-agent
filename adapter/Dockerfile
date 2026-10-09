FROM node:24-slim
ENV NODE_ENV=production
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --prod
COPY src ./src
# clinic.json comes from the repo root. Mount it at /clinic.json, as infra/compose.yaml does.
USER node
EXPOSE 3000
# Node strips the TypeScript types itself, so there is no build step.
CMD ["node", "src/server.ts"]
