"""Entry point for the generated shims; run with `python3 -I -S` so PYTHONPATH, user site and site-packages
(with their .pth files) never leak in: Keyfit needs only the standard library."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from keyfit.shim import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
