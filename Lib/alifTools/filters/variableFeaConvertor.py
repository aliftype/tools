import re
from types import SimpleNamespace

from fontTools.misc.roundTools import otRound
from ufo2ft.filters import BaseFilter

tag = r"[a-zA-Z0-9]{4}"
number = r"-?\d+(?:\.\d+)?"

# GPOS

# tag=number:number
feaLib_vf_pos_re = re.compile(rf"{tag}\s*=\s*{number}\s*:{number}")
# tag:number
axis_spec = rf"{tag}\s*:\s*{number}"
axis_spec_re = re.compile(axis_spec)
# (...) | number
token_re = re.compile(rf"\([\s\S]*?\)|{number}")
# comment | "string": matched first by the passes below and left unchanged
skip = r"#[^\n]*|\"[^\"\n]*\""
# <...>, allowing a nested <...> (e.g. a device table)
value_record = r"<\s*((?:[^<>;]|<[^<>;]*>)*?)\s*>"
value_record_re = re.compile(rf"{skip}|{value_record}")
# keywords marking records that are not plain value records
value_record_keyword_re = re.compile(r"\b(?:device|contourpoint|NULL)\b")
# number (axis_spec) number, with a leading value record alternative so
# numbers inside records and anchors are not read as scalars
scalar_re = re.compile(
    rf"{skip}|{value_record}|{number}(?:\s*\((?:\s*{axis_spec}\s*)+\)\s*{number})+"
)


def format_value(value: str):
    # feaLib scalar values must be integers.
    return str(otRound(float(value)))


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
    entries = [f"{default_coords}:{format_value(default_val)}"]

    for i in range(0, len(tokens), 2):
        if not tokens[i].startswith("("):
            raise ValueError(f"invalid variable position value: {match.group(0)!r}")
        axes = translate_axis_spec(tokens[i])
        val = tokens[i + 1]
        entries.append(f"{axes}:{format_value(val)}")

    return f"({' '.join(entries)})"


def translate_anchor(match: re.Match, record: str, default_coords: str):
    # Converts `<anchor 100 200 (wght:900) 150 260>` to
    # `<anchor (wght=400:100 wght=900:150) (wght=400:200 wght=900:260)>`.
    if (
        "(" not in record
        or "NULL" in record
        or "contourpoint" in record
        or feaLib_vf_pos_re.search(record)
    ):
        return match.group(0)
    tokens: list[str] = token_re.findall(record.strip()[len("anchor") :])
    if len(tokens) < 5 or (len(tokens) - 2) % 3 != 0:
        raise ValueError(f"invalid variable anchor: <{record}>")
    default_vals = [format_value(v) for v in tokens[:2]]
    masters: list[tuple[str, list[str]]] = []
    for i in range(2, len(tokens), 3):
        if not tokens[i].startswith("("):
            raise ValueError(f"invalid variable anchor: <{record}>")
        axes = translate_axis_spec(tokens[i])
        if not axes:
            raise ValueError(f"invalid axis location {tokens[i]} in anchor: <{record}>")
        vals = [format_value(v) for v in tokens[i + 1 : i + 3]]
        masters.append((axes, vals))
    scalars: list[str] = []
    for i in range(2):
        if all(vals[i] == default_vals[i] for _, vals in masters):
            scalars.append(default_vals[i])
        else:
            entries = [f"{default_coords}:{default_vals[i]}"]
            for axes, vals in masters:
                entries.append(f"{axes}:{vals[i]}")
            scalars.append(f"({' '.join(entries)})")
    return f"<anchor {' '.join(scalars)}>"


def translate_value_record(match: re.Match, default_coords: str):
    # Converts `<10 0 5 0 (wdth:80) 20 10 5 2 ...>` to
    # `<(wdth=400:10 wdth=80:20) (wdth=400:0 wdth=80:10)
    #   (wdth=400:5 wdth=80:5) (wdth=400:0 wdth=80:2)>`.
    record = match.group(1)
    if record.strip().startswith("anchor"):
        return translate_anchor(match, record, default_coords)
    if (
        "(" not in record
        or value_record_keyword_re.search(record)
        or feaLib_vf_pos_re.search(record)
    ):
        return match.group(0)

    tokens: list[str] = token_re.findall(record.strip())
    if len(tokens) < 9 or (len(tokens) - 4) % 5 != 0:
        raise ValueError(f"invalid variable value record: <{record}>")

    default_vals = [format_value(v) for v in tokens[:4]]
    masters: list[tuple[str, list[str]]] = []
    for i in range(4, len(tokens), 5):
        if not tokens[i].startswith("("):
            raise ValueError(f"invalid variable value record: <{record}>")
        axes = translate_axis_spec(tokens[i])
        if not axes:
            raise ValueError(
                f"invalid axis location {tokens[i]} in value record: <{record}>"
            )
        vals = [format_value(v) for v in tokens[i + 1 : i + 5]]
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


def translate_gpos(fea, context: SimpleNamespace):
    # Convert ValueRecords
    fea = value_record_re.sub(
        lambda m: (
            m.group(0)
            if m.group(0)[0] in '#"'
            else translate_value_record(m, context.default_coords)
        ),
        fea,
    )

    # Convert Single Scalars
    fea = scalar_re.sub(
        lambda m: (
            m.group(0)
            if m.group(0)[0] in '#"<'
            else translate_scalar(m, context.default_coords)
        ),
        fea,
    )

    return fea


# GSUB

# feature tag {
feature_start_re = re.compile(rf"feature\s+({tag})\s*\{{")
# condition ...;
condition = r"condition\s*([^;]*);"
condition_re = re.compile(condition)
condition_sub_re = re.compile(rf"{skip}|{condition}")
# number < tag < number, with either limit allowed to be missing
axis_range_re = re.compile(rf"(?:({number})\s*<\s*)?({tag})(?:\s*<\s*({number}))?")


def parse_conditions(params: str):
    # Parses 'min < tag < max' (allowing omitted bounds) into a tuple of (tag, min, max).
    if not params.strip():
        return None

    conditions: list[tuple[str, str, str]] = []
    for part in params.split(","):
        part = part.strip()
        assert "<" in part

        if match := axis_range_re.search(part):
            c_min, tag, c_max = match.groups()
            conditions.append(
                (
                    tag,
                    c_min if c_min is not None else "-10000",
                    c_max if c_max is not None else "10000",
                )
            )

    return tuple(sorted(conditions)) if conditions else None


def get_condition_set(conditions: list[tuple[str, str, str]], context: SimpleNamespace):
    # Gets the name of an condition set with the `conditions` or creates new one
    if conditions not in context.condition_sets:
        name = f"conditionset_{len(context.condition_sets) + 1}"
        conditions_str = ";\n".join([" ".join(c) for c in conditions])
        condition_set = f"""\
conditionset {name} {{
    {conditions_str};
}} {name};
"""
        context.condition_sets[conditions] = name
        return name, condition_set
    return context.condition_sets[conditions], None


def translate_condition(m: re.Match, context: SimpleNamespace, tag: str):
    # Converts `number < tag < number, ...` to
    conditions = parse_conditions(m.group(1))
    name, condition_set = get_condition_set(conditions, context)
    return f"""\
}} {tag};

{condition_set if condition_set is not None else ""}

variation {tag} {name} {{
"""


def translate_feature(body: str, masked_body: str, tag: str, context: SimpleNamespace):
    # Splits the feature at condition statements and inserts the feaLib conditionset
    # in the middle
    content = condition_sub_re.sub(
        lambda m: (
            m.group(0)
            if m.group(0)[0] in '#"'
            else translate_condition(m, context, tag)
        ),
        body,
    )

    return f"""\
feature {tag} {{
{content}
}} {tag};
"""


# comments and strings blanked, so scanning ignores braces and tags in them
comment_or_string_re = re.compile(skip)


def blank_comments_and_strings(fea: str):
    return comment_or_string_re.sub(lambda m: " " * len(m.group()), fea)


def match_block(masked: str, start: int):
    # index of the brace closing the block opened before `start`, or -1
    depth = 1
    for i in range(start, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def translate_gsub(fea: str, context: SimpleNamespace):
    masked = blank_comments_and_strings(fea)
    out = []
    pos = 0
    while m := feature_start_re.search(masked, pos):
        tag = m.group(1)
        close = match_block(masked, m.end())
        if close < 0:
            break
        tail = re.match(rf"\s*{tag}\s*;", masked[close + 1 :])
        end = close + 1 + (tail.end() if tail else 0)
        masked_body = masked[m.end() : close]
        if tail is None or not condition_re.search(masked_body):
            out.append(fea[pos:end])
        else:
            out.append(fea[pos : m.start()])
            out.append(
                translate_feature(fea[m.end() : close], masked_body, tag, context)
            )
        pos = end
    out.append(fea[pos:])
    return "".join(out)


class VariableFeaConvertorFilter(BaseFilter):
    _args = ["default"]

    def __call__(self, font, glyphSet=None):
        default_coords: str = self.options.default

        context = self.set_context(font, glyphSet)
        context.default_coords = default_coords
        context.condition_sets = {}

        fea = font.features.text or ""
        if not fea:
            return set()
        fea = translate_gpos(fea, context)
        fea = translate_gsub(fea, context)
        font.features.text = fea
        return set()
