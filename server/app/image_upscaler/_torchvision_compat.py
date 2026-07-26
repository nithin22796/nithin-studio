"""basicsr (a GFPGAN/Real-ESRGAN dependency) imports
`torchvision.transforms.functional_tensor.rgb_to_grayscale`, which was
removed in torchvision >= 0.17 — it lives at
`torchvision.transforms.functional.rgb_to_grayscale` now. Inject a shim
module before anything imports basicsr, rather than pinning torchvision to
an old version (which would conflict with the rest of the ML stack here).

Import this module first, before `basicsr`/`gfpgan`/`realesrgan`.
"""

import sys
import types

if "torchvision.transforms.functional_tensor" not in sys.modules:
    from torchvision.transforms import functional as _functional

    shim = types.ModuleType("torchvision.transforms.functional_tensor")
    shim.rgb_to_grayscale = _functional.rgb_to_grayscale
    sys.modules["torchvision.transforms.functional_tensor"] = shim
