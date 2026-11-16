from ..provenance import write_json
from ..schema import NeedsInput, Payload, Quality


def run(ctx, out):
    if ctx.core.blocked_reasons:
        raise NeedsInput("; ".join(ctx.core.blocked_reasons))
    if ctx.source_error:
        raise ValueError(ctx.source_error)
    path = out / "verified-inputs.json"
    write_json(
        path,
        {
            "core": ctx.core.uid,
            "sources": ctx.source_hashes,
            "profile": ctx.config["profile"],
            "validation": ctx.verified,
            "metadata": ctx.core.metadata,
        },
    )
    metrics = dict(ctx.verified)
    if ctx.config["illumination"]["mode"] == "basicpy":
        metrics.update(ctx.timings)
    return Payload({"inputs": path}, metrics, [], Quality.PASS)
