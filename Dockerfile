FROM python:3.13-slim
WORKDIR /app
COPY mosque_bridge /app/mosque_bridge
ENV PORT=8766 MOSQUE_CACHE_FILE=/data/cache.json
RUN mkdir /data
VOLUME /data
EXPOSE 8766
CMD ["python", "-m", "mosque_bridge.service"]
