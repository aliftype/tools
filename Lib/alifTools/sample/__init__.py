# Copyright 2024 Khaled Hosny
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import pathlib
from functools import cached_property
from typing import NamedTuple, TypeAlias, List

import uharfbuzz as hb
from blackrenderer.backends.svg import SVGSurface, SVGCanvas
from fontTools.misc import etree as ET


Location: TypeAlias = dict[str, int]
Features: TypeAlias = dict[str, list[list[int]]]


class Rect(NamedTuple):
    xMin: float
    yMin: float
    xMax: float
    yMax: float

    def offset(self, x, y) -> "Rect":
        from fontTools.misc.arrayTools import offsetRect

        return Rect(*offsetRect(self, x, y))

    def inset(self, x, y) -> "Rect":
        from fontTools.misc.arrayTools import insetRect

        return Rect(*insetRect(self, x, y))

    def union(self, other: "Rect") -> "Rect":
        from fontTools.misc.arrayTools import unionRect

        if other is None:
            return self
        return Rect(*unionRect(self, other))


class TextRun(NamedTuple):
    font: "Font"
    features: Features
    location: Location
    string: str
    eot: bool = False


class GlyphInfo(NamedTuple):
    glyph: int
    x_advance: float
    y_advance: float
    x_offset: float
    y_offset: float


class GlyphRun(NamedTuple):
    font: "Font"
    location: Location
    glyphs: list[GlyphInfo]
    width: float

    def draw(
        self,
        canvas: SVGCanvas,
        foreground: str | None = None,
        palette: int | None = None,
    ):
        self.font.draw_glyph_run(self, canvas, foreground=foreground, palette=palette)


class GlyphLine(NamedTuple):
    glyphs: list[GlyphRun]
    rect: Rect
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def build(
        cls,
        text: list[TextRun],
        x: float,
        y: float,
        target_width: float | None = None,
    ) -> "GlyphLine":
        glyphs: list[GlyphRun] = []
        width: float = 0
        rect: Rect | None = None

        if target_width is not None:
            if len(text) > 1:
                raise NotImplementedError("Can’t justify multi-run text")
            run = text[0]
            glyphs_ = run.font.shape_justify(run, target_width)
            glyphs.append(glyphs_)
            rect = run.font.calc_glyph_bounds(glyphs_)
            width += glyphs_.width
        else:
            for run in text:
                glyphs_ = run.font.shape(run)
                glyphs.append(glyphs_)
                rect = run.font.calc_glyph_bounds(glyphs_).offset(width, 0).union(rect)
                width += glyphs_.width

        rect = rect.offset(0, y)
        height: float = max(r.font.height for r in glyphs)

        return cls(glyphs, rect, x, y, width, height)

    def draw(
        self,
        canvas: SVGCanvas,
        foreground: str | None,
        x_offset: float = 0,
        y_offset: float = 0,
        palette: int | None = None,
    ):
        with canvas.savedState():
            canvas.translate(self.x + x_offset, self.y + y_offset)
            for run in self.glyphs:
                run.draw(canvas=canvas, foreground=foreground, palette=palette)
                canvas.translate(run.width, 0)


class Font:
    def __init__(self, path: str, hbFont: hb.Font | None = None):
        self.path = path
        if hbFont is None:
            blob = hb.Blob.from_file_path(path)
            face = hb.Face(blob)
            hbFont = hb.Font(face)
        self.hbFont = hbFont
        self._location = None

    def subfont(self) -> "Font":
        hbFont = hb.Font(self.hbFont)
        return self.__class__(self.path, hbFont)

    @property
    def size(self) -> int:
        return self.hbFont.scale[0]

    @size.setter
    def size(self, size: int):
        self.hbFont.scale = (size, size)

    @cached_property
    def brFont(self):
        from blackrenderer.font import BlackRendererFont
        from fontTools.ttLib import TTFont

        return BlackRendererFont(hbFont=self.hbFont, ttFont=TTFont(self.path))

    @cached_property
    def axes(self):
        return self.hbFont.face.axis_infos

    @cached_property
    def instances(self):
        return self.hbFont.face.named_instances

    @cached_property
    def locations(self):
        instances = [i.design_coords for i in self.instances]
        if not instances:
            return [{}]
        axes = [a.tag for a in self.axes]
        locations = [dict(zip(axes, instance)) for instance in instances]
        return locations

    @property
    def location(self):
        if self._location is None:
            self._location = {a.tag: a.default_value for a in self.axes}
        return self._location

    def set_location(
        self,
        location: Location,
    ):
        for axis in self.axes:
            if axis.tag not in location:
                location[axis.tag] = axis.default_value
        self._location = location
        self.hbFont.set_variations(location)

    @cached_property
    def sample_text(self):
        return self.hbFont.face.get_name(hb.OTNameIdPredefined.SAMPLE_TEXT)

    @cached_property
    def height(self) -> float:
        return self.ascender - self.descender

    @cached_property
    def ascender(self) -> float:
        return self.hbFont.get_font_extents("ltr").ascender

    @cached_property
    def descender(self) -> float:
        return self.hbFont.get_font_extents("ltr").descender

    @cached_property
    def line_gap(self) -> float:
        return self.hbFont.get_font_extents("ltr").line_gap

    def _shape(
        self,
        buf: hb.Buffer,
        run: TextRun,
        location: Location,
    ) -> GlyphRun:
        self.set_location(location)
        buf.reset()
        buf.add_str(run.string)
        # FIXME: do real bidi
        if run.string[0] == "۝" and run.string[1:].isnumeric():
            buf.direction = "ltr"
        buf.guess_segment_properties()
        hb.shape(self.hbFont, buf, features=run.features)
        return self._make_glyphs(buf, run, location)

    def _make_glyphs(
        self,
        buf: hb.Buffer,
        run: TextRun,
        location: Location,
    ) -> GlyphRun:
        glyphs = []
        width: int = 0
        for i, (info, pos) in enumerate(zip(buf.glyph_infos, buf.glyph_positions)):
            x_advance = pos.x_advance
            x_offset = pos.x_offset
            # HACK: do proper optical margins
            if run.eot and i == len(buf.glyph_positions) - 1:
                glyph_name: str = self.brFont.glyphNames[info.codepoint]
                if glyph_name.startswith("endofayah-ar"):
                    x_offset = -x_advance
                    x_advance = 0

            width += x_advance
            glyphs.append(
                GlyphInfo(
                    glyph=info.codepoint,
                    x_advance=x_advance,
                    y_advance=pos.y_advance,
                    x_offset=x_offset,
                    y_offset=pos.y_offset,
                )
            )

        return GlyphRun(font=self, location=location, glyphs=glyphs, width=width)

    def shape(
        self,
        run: TextRun,
    ) -> GlyphRun:
        buf = hb.Buffer()
        return self._shape(buf, run, run.location)

    def shape_justify(
        self,
        run: TextRun,
        target_width: float,
    ) -> GlyphRun:
        buf = hb.Buffer()

        glyph_run = self._shape(buf, run, run.location)
        width = glyph_run.width
        if width >= target_width:
            return glyph_run

        if (axis := next((a for a in self.axes if a.tag == "MSHQ"), None)) is None:
            return glyph_run

        location = {axis.tag: axis.max_value}
        glyph_run = self._shape(buf, run, location)
        max_width = glyph_run.width

        def get_width(value):
            return self._shape(buf, run, {axis.tag: value}).width

        location[axis.tag], width = solve_itp(
            get_width,
            axis.default_value,
            axis.max_value,
            (axis.max_value - axis.default_value) / (1 << 14),
            target_width,
            target_width,
            width,
            max_width,
        )

        return self._make_glyphs(buf, run, location)

    def _glyph_bounds(
        self,
        glyph: GlyphInfo,
    ) -> Rect:
        extents = self.hbFont.get_glyph_extents(glyph.glyph)
        xMin = extents.x_bearing
        yMin = extents.y_bearing + extents.height
        xMax = extents.x_bearing + extents.width
        yMax = extents.y_bearing
        return Rect(xMin, yMin, xMax, yMax)

    def calc_glyph_bounds(
        self,
        run: GlyphRun,
    ) -> Rect:
        self.set_location(run.location)
        bounds: Rect | None = None
        x, y = 0, 0
        for glyph in run.glyphs:
            glyph_bounds = self._glyph_bounds(glyph).offset(
                x + glyph.x_offset,
                y + glyph.y_offset,
            )
            x += glyph.x_advance
            y += glyph.y_advance
            bounds = glyph_bounds.union(bounds)
        return bounds

    def draw_glyph(
        self,
        glyph: GlyphInfo,
        canvas: SVGCanvas,
        foreground: str | None = None,
        palette: int | None = None,
    ):
        brFont = self.brFont
        glyph_name = brFont.glyphNames[glyph.glyph]
        if palette is not None:
            palette: List[List[float, float, float, float]] = brFont.getPalette(palette)

        if foreground is not None:
            brFont.drawGlyph(
                glyph_name,
                canvas,
                palette=palette,
                textColor=parseColor(foreground),
            )
        else:
            brFont.drawGlyph(glyph_name, canvas, palette=palette)

    def draw_glyph_run(
        self,
        run: GlyphRun,
        canvas: SVGCanvas,
        foreground: str | None = None,
        palette: int | None = None,
    ):
        self.set_location(run.location)
        with canvas.savedState():
            for glyph in run.glyphs:
                with canvas.savedState():
                    canvas.translate(glyph.x_offset, glyph.y_offset)
                    self.draw_glyph(glyph, canvas, foreground, palette=palette)
                canvas.translate(glyph.x_advance, glyph.y_advance)


# Ported from HarfBuzz:
# https://github.com/harfbuzz/harfbuzz/blob/b6196986d7f17cd5d6aebec88b527726b1493a9c/src/hb-algs.hh#L1511
def solve_itp(f, a, b, epsilon, min_y, max_y, ya, yb):
    import math

    n1_2 = max(math.ceil(math.log2((b - a) / epsilon)) - 1.0, 0.0)
    n0 = 1  # Hardwired
    k1 = 0.2 / (b - a)  # Hardwired.
    n_max = n0 + int(n1_2)
    scaled_epsilon = epsilon * (1 << n_max)
    _2_epsilon = 2.0 * epsilon

    y_itp = 0

    while b - a > _2_epsilon:
        x1_2 = 0.5 * (a + b)
        r = scaled_epsilon - 0.5 * (b - a)
        xf = (yb * a - ya * b) / (yb - ya)
        sigma = x1_2 - xf
        b_a = b - a
        b_a_k2 = b_a * b_a
        delta = k1 * b_a_k2
        sigma_sign = 1 if sigma >= 0 else -1
        xt = xf + delta * sigma_sign if delta <= abs(x1_2 - xf) else x1_2
        x_itp = xt if abs(xt - x1_2) <= r else x1_2 - r * sigma_sign
        y_itp = f(x_itp)

        if y_itp > max_y:
            b = x_itp
            yb = y_itp
        elif y_itp < min_y:
            a = x_itp
            ya = y_itp
        else:
            return x_itp, y_itp

        scaled_epsilon *= 0.5

    return 0.5 * (a + b), y_itp


def _surface_to_tree(
    surface: SVGSurface,
) -> ET.ElementTree:
    import io
    from blackrenderer.backends.svg import SVGSurface, writeSVGElements

    svg_file = io.BytesIO()
    writeSVGElements(surface._svgElements, surface._viewBox, svg_file)
    svg_file.seek(0)

    return ET.parse(svg_file)


def _set_dark_colors(
    surface: SVGSurface,
    foreground: None | str,
    background: None | str,
    dark_foreground: None | str,
    dark_background: None | str,
):
    css = ["@media (prefers-color-scheme: dark) {"]
    if dark_foreground:
        css += [f'path[fill="#{foreground}"] {{', f" fill: #{dark_foreground};", " }"]
    if dark_background:
        css += [f'path[fill="#{background}"] {{', f" fill: #{dark_background};", " }"]
    css += ["}"]

    tree = _surface_to_tree(surface)
    root = tree.getroot()
    style = ET.SubElement(root, "style")
    style.text = "\n" + "\n".join(css) + "\n"

    return tree


def make_lines(
    text_lines: list[list[TextRun]],
    justify: bool,
    x: float,
    y: float,
) -> list[GlyphLine]:
    lines: list[GlyphLine] = []
    if justify:
        max_width = 0
        for text_line in text_lines:
            line = GlyphLine.build(
                text_line,
                x,
                y,
            )
            max_width = max(max_width, line.width)

        for text_line in text_lines:
            line = GlyphLine.build(
                text_line,
                x,
                y,
                max_width,
            )
            lines.append(line)
            y += line.height
    else:
        for text_line in text_lines:
            line = GlyphLine.build(
                text_line,
                x,
                y,
            )
            lines.append(line)
            y += line.height

    return lines, y


def draw_lines(
    lines: list[GlyphLine],
    foreground: None | str,
    background: None | str,
    dark_foreground: None | str,
    dark_background: None | str,
    margin: float,
    palette: int | None = None,
) -> ET.ElementTree:
    bounds: Rect | None = None
    for line in lines:
        bounds = line.rect.union(bounds)
    bounds = bounds.inset(-margin, -margin)

    surface = SVGSurface()
    with surface.canvas(bounds) as canvas:
        if background:
            canvas.drawRectSolid(surface._viewBox, parseColor(background))
        for line in lines:
            # Center align the line.
            x_offset = (bounds[2] - line.rect[2]) / 2 - margin
            line.draw(
                canvas=canvas,
                foreground=foreground,
                x_offset=x_offset,
                palette=palette,
            )

    if dark_foreground or dark_background:
        return _set_dark_colors(
            surface,
            foreground,
            background,
            dark_foreground,
            dark_background,
        )

    return _surface_to_tree(surface)


def draw(
    font_paths: list[pathlib.Path],
    text: None | str,
    features: str,
    foreground: None | str,
    background: None | str,
    dark_foreground: None | str,
    dark_background: None | str,
    justify: bool,
    output_path: pathlib.Path,
):
    lines: list[GlyphLine] = []
    y = 0
    x = 0
    fonts = [Font(font_path) for font_path in font_paths]

    features: Features = parseFeatures(features)

    fonts_locations = []
    if len(fonts) == 1:
        fonts_locations = [(fonts[0], location) for location in fonts[0].locations]
    else:
        fonts_locations = [(font, {}) for font in fonts]

    for font, location in reversed(fonts_locations):
        sample_text = text or font.sample_text
        if not sample_text:
            raise ValueError("No text provided and no sample text in the font")

        text_lines = [
            [
                TextRun(
                    font=font,
                    features=features,
                    location=location,
                    string=line,
                )
            ]
            for line in reversed(sample_text.split("\n"))
        ]

        lines_, y = make_lines(text_lines=text_lines, justify=justify, x=x, y=y)

        lines.extend(lines_)

    if dark_foreground and not foreground:
        foreground = "000000"
    if dark_background and not background:
        background = "FFFFFF"

    tree = draw_lines(
        lines=lines,
        foreground=foreground,
        background=background,
        dark_foreground=dark_foreground,
        dark_background=dark_background,
        margin=100,
    )

    tree.write(output_path, pretty_print=True, xml_declaration=True)


def parseColor(color):
    if len(color) == 8:
        return tuple(int(color[i : i + 2], 16) / 255 for i in (2, 4, 6))
    assert len(color) == 6, color
    return tuple(int(color[i : i + 2], 16) / 255 for i in (0, 2, 4)) + (1,)


def parseFeatures(text: str) -> Features:
    if not text:
        return {}
    features = {}
    for feature in text.split(","):
        value = None
        start = None
        end = None

        feature = feature.strip()
        if feature[0] == "-":
            value = 0
        if feature[0] in ("+", "-"):
            feature = feature[1:]
        tag = feature
        if "[" in tag:
            assert "]" in tag, f"Invalid feature tag: {tag}"
            tag, extra = tag.split("[")
            extra, tag2 = extra.split("]")
            tag += tag2
            start = end = extra
            if ":" in extra:
                start, end = extra.split(":")
        if "=" in tag:
            tag, value = tag.split("=")
        if value is None:
            value = 1
        if start is None or start == "":
            start = 0
        if end is None or end == "":
            end = 0xFFFFFFFF
        features.setdefault(tag, []).append([int(start), int(end), int(value)])
    for tag, value in features.items():
        if len(value) != 1:
            continue
        if value[0][:2] == [0, 0xFFFFFFFF]:
            features[tag] = value[0][2]
    return features


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create SVG sample.")
    parser.add_argument("fonts", help="input font", nargs="+", type=pathlib.Path)
    parser.add_argument("-t", "--text", help="input text")
    parser.add_argument("-f", "--features", help="input features")
    parser.add_argument(
        "-o",
        "--output",
        help="output SVG",
        required=True,
        type=pathlib.Path,
    )
    parser.add_argument("--foreground", help="foreground color")
    parser.add_argument("--background", help="background color")
    parser.add_argument("--dark-foreground", help="foreground color (dark theme)")
    parser.add_argument("--dark-background", help="background color (dark theme)")
    parser.add_argument("--justify", help="justify text", action="store_true")

    args = parser.parse_args(argv)

    draw(
        args.fonts,
        args.text,
        args.features,
        args.foreground,
        args.background,
        args.dark_foreground,
        args.dark_background,
        args.justify,
        args.output,
    )
