FROM node:20-slim AS builder
WORKDIR /app
COPY ui/package.json ui/package-lock.json* ./
RUN npm install
COPY ui/ .
ARG VITE_TEMPORAL_UI_URL=http://localhost:8233
ARG VITE_MAILHOG_UI_URL=http://localhost:8025
ENV VITE_TEMPORAL_UI_URL=$VITE_TEMPORAL_UI_URL
ENV VITE_MAILHOG_UI_URL=$VITE_MAILHOG_UI_URL
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
