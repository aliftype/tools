from enum import StrEnum
from typing import Any, Dict, List, NotRequired, Optional, TypedDict

import uharfbuzz as hb  # type: ignore


class Direction(StrEnum):
    LEFT_TO_RIGHT = "ltr"
    RIGHT_TO_LEFT = "rtl"
    TOP_TO_BOTTOM = "ttb"
    BOTTOM_TO_TOP = "btt"


class ComparisonMode(StrEnum):
    FULL = "full"  # Record glyph names, offsets and advance widths.
    GLYPHSTREAM = "glyphstream"  # Just glyph names.


class ShapingParameters(TypedDict, total=False):
    script: str
    direction: str
    language: str
    features: Dict[str, bool]
    shaper: str
    variations: Dict[str, float]


class Configuration(TypedDict):
    defaults: NotRequired[ShapingParameters]
    forbidden_glyphs: NotRequired[List[str]]
    ingredients: NotRequired[Dict[str, str]]
    forbidden_glyphs: NotRequired[List[str]]


class TestDefinition(ShapingParameters):
    input: str
    expectation: str | Dict[str, str]
    only: NotRequired[str]


class ShapingOutput(TypedDict):
    configuration: NotRequired[Configuration]
    tests: List[TestDefinition]


class ShapingInput(TypedDict):
    text: List[str]
    script: Optional[str]
    language: Optional[str]
    direction: Optional[Direction]
    features: Dict[str, bool]
    comparison_mode: ComparisonMode
    variations: Optional[Dict[str, float]]


def get_shaping_parameters(
    input: ShapingInput | TestDefinition,
    configuration: Configuration,
) -> ShapingParameters:
    defaults = configuration.get("defaults", ShapingParameters())
    parameters: ShapingParameters = {}
    for key in ShapingParameters.__annotations__.keys():
        if value := input.get(key, defaults.get(key)):
            parameters[key] = value
    return parameters


class _SetVariations:
    def __init__(
        self,
        font: hb.Font,  # type: ignore
        variations: Dict[str, Any] | None,
    ):
        self.font = font
        self.variations = variations
        self.saved_variations = None

    def __enter__(self):
        if self.variations:
            self.saved_variations = self.font.get_var_coords_design()
            self.font.set_variations(self.variations)
        return self.font

    def __exit__(self, exc_type, exc_value, traceback):
        if self.saved_variations is not None:
            self.font.set_var_coords_design(self.saved_variations)


class FakeBuffer:
    def __init__(self):
        self.glyph_infos: list[FakeItem] = []
        self.glyph_positions: list[FakeItem] = []


class FakeItem:
    def __init__(
        self,
        codepoint=0,
        cluster=0,
        name=None,
        x_offset=0,
        y_offset=0,
        x_advance=0,
        y_advance=0,
    ):
        self.codepoint = codepoint
        self.cluster = cluster
        self.name = name
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.x_advance = x_advance
        self.y_advance = y_advance
        self.position = [x_offset, y_offset, x_advance, y_advance]


def shape(
    font: hb.Font,  # type: ignore
    text: str,
    parameters: ShapingParameters,
) -> hb.Buffer:  # type: ignore
    buffer = hb.Buffer()  # type: ignore
    buffer.add_str(text)
    buffer.guess_segment_properties()

    if script := parameters.get("script"):
        buffer.script = script
    if direction := parameters.get("direction"):
        buffer.direction = direction
    if language := parameters.get("language"):
        buffer.language = language

    shapers = []
    if shaper := parameters.get("shaper"):
        shapers = [shaper]

    with _SetVariations(font, parameters.get("variations")):
        hb.shape(font, buffer, parameters.get("features"), shapers=shapers)  # type: ignore

    return buffer


def serialize_buffer(
    font: hb.Font,  # type: ignore
    buffer: hb.Buffer | FakeBuffer,  # type: ignore
    glyphs_only: bool = False,
) -> str:
    flags = hb.BufferSerializeFlags.DEFAULT
    if glyphs_only:
        flags |= (
            hb.BufferSerializeFlags.NO_CLUSTERS | hb.BufferSerializeFlags.NO_POSITIONS
        )
    return buffer.serialize(font, flags=flags)[1:-1]


def _move_to(x, y, buffer_list):
    buffer_list.append(f"M{x},{y}")


def _line_to(x, y, buffer_list):
    buffer_list.append(f"L{x},{y}")


def _cubic_to(c1x, c1y, c2x, c2y, x, y, buffer_list):
    buffer_list.append(f"C{c1x},{c1y} {c2x},{c2y} {x},{y}")


def _quadratic_to(c1x, c1y, x, y, buffer_list):
    buffer_list.append(f"Q{c1x},{c1y} {x},{y}")


def _close_path(buffer_list):
    buffer_list.append("Z")


_draw_functions = hb.DrawFuncs()  # type: ignore
_draw_functions.set_move_to_func(_move_to)
_draw_functions.set_line_to_func(_line_to)
_draw_functions.set_cubic_to_func(_cubic_to)
_draw_functions.set_quadratic_to_func(_quadratic_to)
_draw_functions.set_close_path_func(_close_path)


def _glyph_to_svg_path(
    font: hb.Font,  # type: ignore
    gid: int,
) -> str:
    buffer_list: list[str] = []
    font.draw_glyph(gid, _draw_functions, buffer_list)
    return "".join(buffer_list)


def _glyph_to_svg_id(
    font: hb.Font,  # type: ignore
    gid: int,
    defs: Dict[str, str],
) -> str:
    id = f"g{gid}"
    if id not in defs:
        p = _glyph_to_svg_path(font, gid)
        defs[id] = f'<path id="{id}" d="{p}"/>'
    return id


def _to_svg_color(color):
    svg_color = [f"{color.red}", f"{color.green}", f"{color.blue}"]
    if color.alpha != 255:
        svg_color.append(f"{color.alpha/255:.0%}")
    return f"rgb({','.join(svg_color)})"


def _glyph_to_svg(font, gid, x, y, defs):
    transform = f'transform="translate({x},{y})"'
    svg = [f"<g {transform}>"]
    face = font.face
    if (layers := face.get_glyph_color_layers(gid)) and (palettes := face.color_palettes):  # type: ignore
        for layer in layers:
            id = _glyph_to_svg_id(font, layer.glyph, defs)
            if layer.color_index != 0xFFFF:
                color = _to_svg_color(palettes[0].colors[layer.color_index])
                svg.append(f'<use href="#{id}" fill="{color}"/>')
            else:
                svg.append(f'<use href="#{id}"/>')
    else:
        id = _glyph_to_svg_id(font, gid, defs)
        svg.append(f'<use href="#{id}"/>')
    svg.append("</g>")
    return "\n".join(svg)


def _draw_buffer(
    font: hb.Font,  # type: ignore
    buffer: hb.Buffer,  # type: ignore
) -> str:
    defs = {}
    paths = []

    font_extents = font.get_font_extents("ltr")
    y_max = font_extents.ascender
    y_min = font_extents.descender
    x_min = x_max = 0

    x_cursor = 0
    y_cursor = 0
    for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions):
        dx, dy = pos.x_offset, pos.y_offset
        p = _glyph_to_svg(font, info.codepoint, x_cursor + dx, y_cursor + dy, defs)
        paths.append(p)

        if extents := font.get_glyph_extents(info.codepoint):
            cur_x = x_cursor + dx
            cur_y = y_cursor + dy
            min_x = cur_x + min(extents.x_bearing, 0)
            min_y = cur_y + min(extents.height + extents.y_bearing, pos.y_advance)
            max_x = cur_x + max(extents.width + extents.x_bearing, pos.x_advance)
            max_y = cur_y + max(extents.y_bearing, 0)
            x_min = min(x_min, min_x)
            y_min = min(y_min, min_y)
            x_max = max(x_max, max_x)
            y_max = max(y_max, max_y)

        x_cursor += pos.x_advance
        y_cursor += pos.y_advance

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x_min} {y_min} {x_max - x_min} {y_max - y_min}" transform="matrix(1 0 0 -1 0 0)">',
        "<defs>",
        *defs.values(),
        "</defs>",
        *paths,
        "</svg>",
        "",
    ]
    return "\n".join(svg)


def buffer_to_svg(
    font: hb.Font,  # type: ignore
    buffer: hb.Buffer,  # type: ignore
    parameters: ShapingParameters,
) -> str:
    import base64

    with _SetVariations(font, parameters.get("variations")):
        svg = _draw_buffer(font, buffer)

    # Use img tag instead of inline SVG as glyphs will have the same IDs across
    # files, which is broken when files us different variations or fonts.
    svg = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    img = f'<img src="data:image/svg+xml;base64,{svg}" alt="SVG output">'
    return img
