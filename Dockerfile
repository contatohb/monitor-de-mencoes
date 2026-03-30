FROM python:3.11-slim

WORKDIR /app

# Dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código da aplicação
COPY . .

# Criar diretórios de dados e logs
RUN mkdir -p data logs

# Porta padrão do Render
ENV PORT=8000

EXPOSE 8000

# Gunicorn com 1 worker (monitor é CPU-bound e roda 1 vez/dia)
# timeout=600s para acomodar o tempo de execução do monitor (~5-8 min)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--timeout", "600", "--log-level", "info", "app:app"]
