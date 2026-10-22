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
    return Payload({"inputs": path}, ctx.verified, [], Quality.PASS)
