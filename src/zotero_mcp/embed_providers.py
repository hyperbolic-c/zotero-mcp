"""
Local embedding providers for Zotero MCP.

Supports Ollama, vLLM, LM Studio, llamafile and other OpenAI-compatible local servers.
"""

import os
import logging
from typing import List

import requests
from chromadb import Documents, EmbeddingFunction, Embeddings

logger = logging.getLogger(__name__)


class OllamaEmbeddingFunction(EmbeddingFunction):
    """
    Ollama embeddings with GPU layer support.

    Requires Ollama server running with the embedding model pulled.
    Example: ollama pull nomic-embed-text
    """

    def __init__(self,
                 model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434",
                 gpu_layers: int = 0,
                 num_ctx: int = 4096):
        """
        Initialize Ollama embedding function.

        Args:
            model: Model name to use (e.g., 'nomic-embed-text', 'mxbai-embed-large')
            base_url: Ollama server URL
            gpu_layers: Number of GPU layers to use (0 for CPU only)
            num_ctx: Context window size
        """
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.gpu_layers = gpu_layers
        self.num_ctx = num_ctx

    def name(self) -> str:
        """Return the name of this embedding function."""
        return f"ollama-{self.model}"

    def __call__(self, input: Documents) -> Embeddings:
        """
        Generate embeddings using Ollama API.

        Args:
            input: List of text documents to embed

        Returns:
            List of embedding vectors
        """
        embeddings = []

        for text in input:
            try:
                payload = {
                    "model": self.model,
                    "prompt": text,
                    "options": {
                        "num_ctx": self.num_ctx,
                    }
                }

                # Add gpu_layers if specified and greater than 0
                if self.gpu_layers > 0:
                    payload["options"]["gpu_layers"] = self.gpu_layers

                response = requests.post(
                    f"{self.base_url}/api/embeddings",
                    json=payload,
                    timeout=120
                )
                response.raise_for_status()
                result = response.json()

                embeddings.append(result["embedding"])

            except requests.exceptions.RequestException as e:
                logger.error(f"Ollama API error for model {self.model}: {e}")
                raise RuntimeError(
                    f"Failed to get embeddings from Ollama at {self.base_url}: {e}"
                ) from e

        return embeddings


class OpenAILocalEmbeddingFunction(EmbeddingFunction):
    """
    OpenAI-compatible local server embeddings.

    Supports vLLM, LM Studio, llamafile and any server implementing
    the OpenAI embeddings API format.
    """

    def __init__(self,
                 model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434",
                 api_key: str | None = None):
        """
        Initialize OpenAI-compatible embedding function.

        Args:
            model: Model name to use
            base_url: Server URL (e.g., 'http://localhost:8000/v1' or 'http://localhost:1234/v1')
            api_key: Optional API key (defaults to OPENAI_API_KEY env var)
        """
        self.model = model
        # Normalize base_url - ensure it ends with /v1
        base_url = base_url.rstrip('/')
        if not base_url.endswith('/v1'):
            base_url = f"{base_url}/v1"
        self.base_url = base_url
        self.api_key = api_key if api_key else os.getenv("OPENAI_API_KEY")

    def name(self) -> str:
        """Return the name of this embedding function."""
        return f"local-openai-{self.model}"

    def __call__(self, input: Documents) -> Embeddings:
        """
        Generate embeddings using OpenAI-compatible API.

        Args:
            input: List of text documents to embed

        Returns:
            List of embedding vectors
        """
        headers = {
            "Content-Type": "application/json"
        }

        # Add authorization header if API key is provided
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        embeddings = []

        # Process in batches to avoid overwhelming the server
        batch_size = 100
        for i in range(0, len(input), batch_size):
            batch = input[i:i + batch_size]

            try:
                payload = {
                    "model": self.model,
                    "input": batch,
                }

                response = requests.post(
                    f"{self.base_url}/embeddings",
                    json=payload,
                    headers=headers,
                    timeout=120
                )
                response.raise_for_status()
                result = response.json()

                # Extract embeddings from response
                for data in result.get("data", []):
                    embeddings.append(data["embedding"])

            except requests.exceptions.RequestException as e:
                logger.error(f"OpenAI-compatible API error: {e}")
                raise RuntimeError(
                    f"Failed to get embeddings from {self.base_url}: {e}"
                ) from e

        return embeddings


def create_local_embedding_function(
    provider: str = "ollama",
    model: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
    gpu_layers: int = 0,
    num_ctx: int = 4096
) -> EmbeddingFunction:
    """
    Factory function to create the appropriate local embedding function.

    Args:
        provider: Provider type ('ollama', 'vllm', 'lm-studio', 'llamafile')
        model: Model name
        base_url: Server URL
        gpu_layers: GPU layers (Ollama only)
        num_ctx: Context window size (Ollama only)

    Returns:
        Configured embedding function
    """
    provider = provider.lower()

    if provider == "ollama":
        return OllamaEmbeddingFunction(
            model=model,
            base_url=base_url,
            gpu_layers=gpu_layers,
            num_ctx=num_ctx
        )
    else:
        # vLLM, LM Studio, llamafile and other OpenAI-compatible servers
        return OpenAILocalEmbeddingFunction(
            model=model,
            base_url=base_url
        )
