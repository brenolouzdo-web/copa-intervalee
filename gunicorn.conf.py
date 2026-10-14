# Configuração do gunicorn para produção (tempo real = conexões longas).
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = os.environ.get("WORKER_CLASS", "gthread")   # "gevent" aguenta milhares de conexões por worker
threads = int(os.environ.get("THREADS", "300"))            # cada torcedor conectado ocupa 1 thread
worker_connections = int(os.environ.get("WORKER_CONNECTIONS", "2000"))  # só para gevent
timeout = 120
keepalive = 75
accesslog = "-"
