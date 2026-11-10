"""Evaluate a trusted template in a child process with a parent-side deadline."""
import json
import math
import sys

from starlark_go import Starlark, StarlarkError


def main():
    job = json.load(sys.stdin)
    try:
        engine = Starlark()
        engine.exec(job["source"], print=lambda *_: None)
        engine.set(registers={int(k): v for k, v in job["registers"].items()}, order=job["order"])
        result = engine.eval("transform(registers, order)", print=lambda *_: None)
        if not isinstance(result, dict) or not 1 <= len(result) <= 256:
            raise ValueError("Expected a dictionary of 1 to 256 measurements")
        for key, value in result.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 100:
                raise ValueError("Measurement keys must be non-empty strings")
            if type(value) not in (int, float, bool) or (
                type(value) is float and not math.isfinite(value)
            ):
                raise ValueError("Measurement values must be finite numbers or booleans")
        print(json.dumps(result, allow_nan=False))
    except (StarlarkError, ValueError) as error:
        print(str(error)[:512], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
