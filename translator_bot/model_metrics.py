"""Model-runtime measurements; missing native cache telemetry is not zero."""
import math


def model_measurement(payload,wall_seconds,*,prompt_characters,structured):
    def value(name):
        n=payload.get(name)
        return n if type(n) in (int,float) and math.isfinite(n) and n>=0 else None
    def seconds(name):
        n=value(name)
        return n/1e9 if n is not None else None
    return dict(wall_seconds=wall_seconds,prompt_characters=prompt_characters,
        prompt_tokens=value('prompt_eval_count'),native_cached_tokens=value('prompt_eval_cached_count'),
        output_tokens=value('eval_count'),prefill_seconds=seconds('prompt_eval_duration'),
        generation_seconds=seconds('eval_duration'),load_seconds=seconds('load_duration'),structured=structured)
