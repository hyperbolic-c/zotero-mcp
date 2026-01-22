# Local Embedding Model Support Design Document

Date: 2026-01-22

## Overview

Add support for local embedding models in the semantic search functionality. This enables users to run embedding models locally via Ollama, vLLM, LM Studio, or llamafile, reducing API costs and improving privacy.

## Goals

- Support Ollama with GPU layer configuration
- Support OpenAI-compatible local servers (vLLM, LM Studio, llamafile)
- Interactive setup via `zotero-mcp setup`
- Configuration file support with environment variable override
- No new dependencies (use existing `requests` library)

## Architecture

### Configuration Structure

Located in `~/.config/zotero-mcp/config.json`:

```json
{
  "semantic_search": {
    "embedding_model": "local",
    "embedding_config": {
      "provider": "ollama",
      "model": "nomic-embed-text",
      "base_url": "http://localhost:11434",
      "gpu_layers": 0,
      "num_ctx": 4096
    }
  }
}
```

### Provider Options

| Provider | API Endpoint | GPU Support |
|----------|-------------|-------------|
| ollama | `/api/embeddings` | Native via `gpu_layers` |
| vllm | `/v1/embeddings` | Server-side |
| lm-studio | `/v1/embeddings` | Server-side |
| llamafile | `/v1/embeddings` | Server-side |

### Environment Variable Override

| Variable | Description | Default |
|----------|-------------|---------|
| `ZOTERO_LOCAL_EMBEDDING_PROVIDER` | Provider type | `ollama` |
| `ZOTERO_LOCAL_EMBEDDING_MODEL` | Model name | `nomic-embed-text` |
| `ZOTERO_LOCAL_EMBEDDING_URL` | Server URL | `http://localhost:11434` |
| `ZOTERO_LOCAL_EMBEDDING_GPU_LAYERS` | GPU layers (Ollama only) | `0` |

## Component Design

### New File: `src/zotero_mcp/local_embedding.py`

Contains base `LocalEmbeddingFunction` class handling common functionality.

### New File: `src/zotero_mcp/embed_providers.py`

Provider-specific implementations:

```python
class OllamaEmbeddingFunction(EmbeddingFunction):
    """Ollama embeddings with GPU layer support."""
    def __init__(self, model: str, base_url: str, gpu_layers: int, num_ctx: int):
        self.model = model
        self.base_url = base_url
        self.gpu_layers = gpu_layers
        self.num_ctx = num_ctx

    def __call__(self, input: Documents) -> Embeddings:
        # Call Ollama /api/embeddings with gpu_layers in options
        pass

class OpenAILocalEmbeddingFunction(EmbeddingFunction):
    """OpenAI-compatible local server (vLLM, LM Studio, llamafile)."""
    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url

    def __call__(self, input: Documents) -> Embeddings:
        # Call {base_url}/v1/embeddings
        pass
```

### Modified: `src/zotero_mcp/chroma_client.py`

Add branch in `_create_embedding_function()`:

```python
elif self.embedding_model == "local":
    provider = self.embedding_config.get("provider", "ollama")
    model = self.embedding_config.get("model", "nomic-embed-text")
    base_url = self.embedding_config.get("base_url", "http://localhost:11434")
    gpu_layers = self.embedding_config.get("gpu_layers", 0)
    num_ctx = self.embedding_config.get("num_ctx", 4096)

    if provider == "ollama":
        return OllamaEmbeddingFunction(model, base_url, gpu_layers, num_ctx)
    else:
        return OpenAILocalEmbeddingFunction(model, base_url)
```

### Modified: `src/zotero_mcp/setup_helper.py`

Add interactive prompts for local embedding configuration:

1. "Use local embedding model? (y/n)"
2. "Local embedding provider: (ollama/vllm/lm-studio/llamafile)"
3. "Model name: [default nomic-embed-text]"
4. "Server URL: [default http://localhost:11434]"
5. "GPU layers (0 for CPU): [default 0]"

### Modified: `src/zotero_mcp/cli.py`

Update `--help` output to document local embedding options.

## Error Handling

| Scenario | Error Message | User Action |
|----------|---------------|-------------|
| Service unreachable | "Cannot connect to local embedding service at {url}" | Check service is running |
| Model not found | "Model '{model}' not found. Run: ollama pull {model}" | Pull model or check name |
| API error | "Local embedding API error: {error}" | Check service logs |
| GPU layers unsupported | "Warning: gpu_layers not supported by this provider" | Continue with CPU |

## Testing Strategy

### Unit Tests (tests/test_local_embedding.py)

- Mock API responses with `requests_mock`
- Test provider detection from config
- Test embedding output format
- Test error handling

### Integration Tests (Optional)

- Mark with `@pytest.mark.integration`
- Require running local service
- Run manually or in dedicated CI job

## Migration Path

Existing configurations continue to work unchanged. Users with `embedding_model: "qwen"` or similar will not be affected.

## Recommended Models

| Use Case | Model | Notes |
|----------|-------|-------|
| General purpose | `nomic-embed-text` | 137M params, high quality |
| High quality | `mxbai-embed-large` | 334M params |
| Lightweight | `all-minilm` | 90M params |

## Dependencies

No new dependencies required. Uses existing `requests` library.
