"""torch 2.1's weight_norm, backported onto this board's torch.

The Jetson build is 2.1.0a0+nv23.06 -- an alpha branched before
parametrizations.weight_norm landed, so the version number reads 2.1 while the
function is absent. chatterbox's s3gen imports it directly, in f0_predictor and
hifigan.

This copies the upstream implementation rather than substituting the older
torch.nn.utils.weight_norm, because the two are not interchangeable: the old one
stores the magnitude and direction as `weight_g` and `weight_v`, the new one as
`parametrizations.weight.original0` and `original1`. chatterbox's released
checkpoints carry the new names, and hifigan.py imports remove_weight_norm from
the old API alongside weight_norm from the new one -- so swapping in the old
function would quietly change what a checkpoint key means. Copying the real
implementation keeps the key names and the arithmetic identical, which is what
makes the Orin's output comparable to the 2060's.

Everything it depends on is already present in the alpha: torch._weight_norm,
torch.norm_except_dim, and parametrize.register_parametrization.
"""

import torch
from torch.nn import Module
from torch.nn.utils import parametrize


class _WeightNorm(Module):
    def __init__(self, dim=0):
        super().__init__()
        self.dim = -1 if dim is None else dim

    def forward(self, weight_g, weight_v):
        return torch._weight_norm(weight_v, weight_g, self.dim)

    def right_inverse(self, weight):
        return torch.norm_except_dim(weight, 2, self.dim), weight


def weight_norm(module, name="weight", dim=0):
    """Reparametrize ``module``'s parameter as a magnitude and a direction."""
    parametrize.register_parametrization(module, name, _WeightNorm(dim), unsafe=True)

    # Lets a checkpoint written by the old API still load. Upstream ships this
    # hook; without it the compatibility direction is silently lost.
    def _compat_hook(state_dict, prefix, local_metadata, strict,
                     missing_keys, unexpected_keys, error_msgs):
        g_key, v_key = f"{prefix}{name}_g", f"{prefix}{name}_v"
        if g_key in state_dict and v_key in state_dict:
            state_dict[f"{prefix}parametrizations.{name}.original0"] = state_dict.pop(g_key)
            state_dict[f"{prefix}parametrizations.{name}.original1"] = state_dict.pop(v_key)

    module._register_load_state_dict_pre_hook(_compat_hook)
    return module


def install():
    """Add weight_norm to torch's parametrizations if this torch lacks it.

    A no-op on a torch that has its own, so the same image layer stays correct
    if the base is ever moved forward.
    """
    import torch.nn.utils.parametrizations as parametrizations

    if hasattr(parametrizations, "weight_norm"):
        return False
    parametrizations.weight_norm = weight_norm
    parametrizations._WeightNorm = _WeightNorm
    if hasattr(parametrizations, "__all__") and "weight_norm" not in parametrizations.__all__:
        parametrizations.__all__.append("weight_norm")
    return True


install()
