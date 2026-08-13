FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.8.13

WORKDIR /code
COPY pyproject.toml uv.lock* ./
COPY src ./src
COPY proto ./proto

RUN uv sync --frozen --no-dev \
    && mkdir -p /etc/tqa

EXPOSE 443

ENV TQA_TLS_CERT=/etc/tqa/tls.crt
ENV TQA_TLS_KEY=/etc/tqa/tls.key

CMD ["/code/.venv/bin/python", "-m", "countersign_tqa.server"]
