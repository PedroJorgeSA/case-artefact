"""Os testes rodam offline: busca no manual só com BM25 (sem baixar o modelo de embeddings)."""

import os

os.environ["EMPORIO_EMBEDDINGS"] = "off"
os.environ["HF_HUB_OFFLINE"] = "1"
