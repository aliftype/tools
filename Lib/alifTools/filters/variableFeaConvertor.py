import re

from ufo2ft.filters import BaseFilter

tag = r"[a-zA-Z0-9]{4}"
number = r"-?\d+(?:\.\d+)?"
# tag=number:number
feaLib_vf_pos_re = re.compile(rf"{tag}\s*=\s*{number}\s*:{number}")
# tag:number
axis_spec = rf"{tag}\s*:\s*{number}"
axis_spec_re = re.compile(axis_spec)
# (...) | number
token_re = re.compile(rf"\(.*?\)|{number}")
# <...>
value_record_re = re.compile(r"<\s*([^>;]+?)\s*>")
# number (axis_spec) number
scalar_re = re.compile(rf"{number}(?:\s*\((?:\s*{axis_spec}\s*)+\)\s*{number})+")


def has_feaLib_vf_gpos(fea: str):
    return feaLib_vf_pos_re.search(fea) is not None


def translate_axis_spec(axes: str):
    # Converts `(wdth:80)` to `wdth=80`.
    axes = axes.strip("() ")
    parts: list[str] = axis_spec_re.findall(axes)
    converted = []
    for part in parts:
        axis, val = part.split(":")
        converted.append(f"{axis.strip()}={val.strip()}")
    return ",".join(converted)


def translate_scalar(match: re.Match, default_coords: str):
    # Converts `10 (wdth:80) 20` to `(wght=400:10 wdth=80:20)`.
    tokens: list[str] = token_re.findall(match.group(0))
    if not tokens:
        return match.group(0)

    default_val = tokens.pop(0)
    entries = [f"{default_coords}:{default_val}"]

    for i in range(0, len(tokens), 2):
        assert tokens[i].startswith("(")
        axes = translate_axis_spec(tokens[i])
        val = tokens[i + 1]
        entries.append(f"{axes}:{val}")

    return f"({' '.join(entries)})"


def translate_value_record(match: re.Match, default_coords: str):
    # Converts `<10 0 5 0 (wdth:80) 20 10 5 2 ...>` to
    # `<(wdth=400:10 wdth=80:20) (wdth=400:0 wdth=80:10)
    #   (wdth=400:5 wdth=80:5) (wdth=400:0 wdth=80:2)>`.
    tokens: list[str] = token_re.findall(match.group(1).strip())
    if len(tokens) < 5:
        return match.group(0)

    default_vals = tokens[:4]
    masters: list[tuple[str, list[str]]] = []
    for i in range(4, len(tokens), 5):
        axes = translate_axis_spec(tokens[i])
        vals = tokens[i + 1 : i + 5]
        masters.append((axes, vals))

    scalars: list[str] = []
    for i in range(4):
        if all(vals[i] == default_vals[i] for _, vals in masters):
            scalars.append(default_vals[i])
        else:
            entries = [f"{default_coords}:{default_vals[i]}"]
            for axes, vals in masters:
                entries.append(f"{axes}:{vals[i]}")
            scalars.append(f"({' '.join(entries)})")

    return f"<{' '.join(scalars)}>"


def transtate_gpos(fea, default_coords: str):
    if has_feaLib_vf_gpos(fea):
        return fea

    # Convert ValueRecords
    fea = value_record_re.sub(lambda m: translate_value_record(m, default_coords), fea)

    # Convert Single Scalars
    fea = scalar_re.sub(lambda m: translate_scalar(m, default_coords), fea)

    return fea


class VariableFeaConvertorFilter(BaseFilter):
    _args = ["default"]

    def __call__(self, font, glyphSet=None):
        default_coords: str = self.options.default
        font.features.text = transtate_gpos(font.features.text, default_coords)
        return set()
