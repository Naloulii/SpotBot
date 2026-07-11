FROM python:3.11-slim

# Étape obligatoire pour que le conteneur puisse utiliser les commandes Git
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir discord.py colorthief requests GitPython

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]