from copy import deepcopy

from .base import MODELS as BASE_MODELS

MODELS = deepcopy(BASE_MODELS)
MODELS["llm"].update(device="cuda", gpu_layers=32, quantization="Q4_K_M")
