FROM node:22-alpine AS build

ARG APP_NAME
ARG APP_BASE
WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps ./apps
COPY packages/web ./packages/web
RUN npm ci
RUN npm run build --workspace="apps/${APP_NAME}" \
    && mkdir -p "/output/html/${APP_BASE}" \
    && cp -R "apps/${APP_NAME}/dist/." "/output/html/${APP_BASE}/" \
    && cp "apps/${APP_NAME}/nginx.conf" /output/nginx.conf

FROM nginx:1.27-alpine
COPY --from=build /output/html /usr/share/nginx/html
COPY --from=build /output/nginx.conf /etc/nginx/conf.d/default.conf
