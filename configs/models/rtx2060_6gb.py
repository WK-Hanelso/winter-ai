from copy import deepcopy

from .base import MODELS as BASE_MODELS

MODELS = deepcopy(BASE_MODELS)
MODELS["llm"].update(
    device="gpu",
    backend="vulkan",
    gpu_layers=37,
    quantization="Q4_K_M",
)
