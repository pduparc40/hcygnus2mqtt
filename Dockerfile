FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY hcygnus2mqtt ./hcygnus2mqtt
RUN pip install --no-cache-dir .
COPY registers ./registers
# config.yaml est monté dans /config ; le chemin "registers:" y est relatif
VOLUME ["/config"]
ENTRYPOINT ["hcygnus2mqtt"]
CMD ["run", "-c", "/config/config.yaml"]
