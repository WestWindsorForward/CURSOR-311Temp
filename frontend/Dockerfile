# Build stage
FROM node:26-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./
RUN npm ci

# Copy source and build. The `prebuild` script regenerates
# public/third-party-licenses.txt from the installed dependency tree, so the
# notices for everything compiled into the bundle ship with the bundle.
COPY . .
RUN npm run build

# Production stage - serve with nginx
FROM nginx:alpine

LABEL org.opencontainers.image.title="Pinpoint 311 Frontend" \
      org.opencontainers.image.description="Resident and staff web interface for the Pinpoint 311 municipal service-request platform" \
      org.opencontainers.image.source="https://github.com/Pinpoint-311/Pinpoint-311" \
      org.opencontainers.image.licenses="MIT"

# Copy built assets from builder (includes third-party-licenses.txt, which
# nginx then serves at /third-party-licenses.txt)
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
