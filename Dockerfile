# ICT Trading Bot Dockerfile
# Note: MT5 requires Windows, so this is for running the API/dashboard only

# Backend API
FROM python:3.11-slim as backend

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY trading_bot/ ./trading_bot/
COPY .env.example ./.env

# Create data directories
RUN mkdir -p logs data

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Run API server
CMD ["python", "-m", "uvicorn", "trading_bot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]


# Dashboard (separate build stage)
FROM node:20-alpine as dashboard-build

WORKDIR /app

# Install dependencies
COPY dashboard/package*.json ./
RUN npm ci

# Copy dashboard source
COPY dashboard/ .

# Build
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


# Dashboard production
FROM node:20-alpine as dashboard

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Copy from build stage
COPY --from=dashboard-build /app/.next/standalone ./
COPY --from=dashboard-build /app/.next/static ./.next/static
COPY --from=dashboard-build /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
