"""Write the OpenAPI schema to a file without starting a server.

The front end's types are generated from this. Dumping it from the app
object rather than curling a running /openapi.json means regenerating is a
single command with nothing to start, stop, or forget to restart — which is
what keeps the generated types honest as api.py's response models move.

    python dump_openapi.py [output_path]
"""

import json
import sys
from pathlib import Path

from api import app

DEFAULT = Path(__file__).parent / "frontend" / "openapi.json"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
