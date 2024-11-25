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
import io
from functools import cached_property
from typing import NamedTuple

import uharfbuzz as hb
from blackrenderer.backends.svg import SVGSurface


class Rect(NamedTuple):
    xMin: float
    yMin: float
    xMax: float
    yMax: float

    def offset(self, x, y):
        from fontTools.misc.arrayTools import offsetRect

        return Rect(*offsetRect(self, x, y))

    def inset(self, x, y):
        from fontTools.misc.arrayTools import insetRect

        return Rect(*insetRect(self, x, y))

    def union(self, other: "Rect") -> "Rect":
        from fontTools.misc.arrayTools import unionRect

        if other is None:
            return self
        return Rect(*unionRect(self, other))


class GlyphInfo(NamedTuple):
    glyph: int
    x_advance: float
    y_advance: float
    x_offset: float
    y_offset: float


class Font:
    def __init__(self, path: str):

        self.path = path
        blob = hb.Blob.from_file_path(path)
        face = hb.Face(blob)
        self.hbFont = hb.Font(face)
        self._location = None

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
        location: dict[str, int],
    ):
        self._location = location
        self.hbFont.set_variations(location)

    @cached_property
    def sample_text(self):
        return self.hbFont.face.get_name(hb.OTNameIdPredefined.SAMPLE_TEXT)

    def _shape(
        self,
        buf: hb.Buffer,
        text: str,
        location: dict[str, int],
        features: dict[str, list[list[int]]],
    ) -> float:
        self.set_location(location)
        buf.reset()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hbFont, buf, features=features)
        return sum(g.x_advance for g in buf.glyph_positions)

    def _make_glyphs(self, buf: hb.Buffer) -> list[GlyphInfo]:
        glyphs = []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            glyphs.append(
                GlyphInfo(
                    glyph=info.codepoint,
                    x_advance=pos.x_advance,
                    y_advance=pos.y_advance,
                    x_offset=pos.x_offset,
                    y_offset=pos.y_offset,
                )
            )
        return glyphs

    @staticmethod
    def _parse_features(text: str) -> dict[str, list[list[int]]]:
        if not text:
            return {}
        features = {}
        for feature in text.split(","):
            value = None
            start = None
            end = None

            if feature[0] == "-":
                value = 0
            if feature[0] in ("+", "-"):
                feature = feature[1:]
            tag = feature
            if "=" in tag:
                tag, value = tag.split("=")
            if "[" in tag:
                tag, extra = tag.split("[")
                if extra[-1] == "]":
                    extra = extra[:-1]
                    start = end = extra
                    if ":" in extra:
                        start, end = extra.split(":")
            if value is None:
                value = 1
            if start is None or start == "":
                start = 0
            if end is None or end == "":
                end = 0xFFFFFFFF
            features.setdefault(tag, []).append(
                [int(start), int(end), bool(int(value))]
            )
        for tag, value in features.items():
            if value == [[0, 0xFFFFFFFF, 1]]:
                features[tag] = True
            if value == [[0, 0xFFFFFFFF, 0]]:
                features[tag] = False
        return features

    def shape(
        self,
        text: str,
        location: dict[str, int],
        features: str,
    ) -> list[GlyphInfo]:
        buf = hb.Buffer()
        features = self._parse_features(features)
        width = self._shape(buf, text, location, features)
        glyphs = self._make_glyphs(buf)
        return glyphs, width

    def shape_justify(
        self,
        text: str,
        location: dict[str, int],
        features: str,
        target_width: float,
    ):
        buf = hb.Buffer()
        features = self._parse_features(features)

        width = self._shape(buf, text, location, features)
        if width >= target_width:
            return self._make_glyphs(buf), width, location

        if (axis := next((a for a in self.axes if a.tag == "MSHQ"), None)) is None:
            return self._make_glyphs(buf), width, location

        location = {axis.tag: axis.max_value}
        max_width = self._shape(buf, text, location, features)

        location[axis.tag], width = solve_itp(
            lambda x: self._shape(buf, text, {axis.tag: x}, features),
            axis.default_value,
            axis.max_value,
            (axis.max_value - axis.default_value) / (1 << 14),
            target_width,
            target_width,
            width,
            max_width,
        )

        return self._make_glyphs(buf), width, location

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
        glyphs: list[GlyphInfo],
    ) -> Rect:
        bounds = None
        x, y = 0, 0
        for glyph in glyphs:
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
        canvas,
        foreground=None,
    ):
        brFont = self.brFont
        glyph_name = brFont.glyphNames[glyph.glyph]
        if foreground is not None:
            brFont.drawGlyph(glyph_name, canvas, textColor=parseColor(foreground))
        else:
            brFont.drawGlyph(glyph_name, canvas)


class GlyphLine(NamedTuple):
    font: Font
    glyphs: list[GlyphInfo]
    rect: Rect
    x: float
    y: float
    width: float
    height: float
    location: dict[str, int] | None = None

    @classmethod
    def build(
        cls,
        font: Font,
        text: str,
        features: str,
        x: float,
        y: float,
        location: dict[str, int],
        target_width: float | None = None,
    ) -> "GlyphLine":

        if target_width is not None:
            glyphs, width, location = font.shape_justify(
                text, location, features, target_width
            )
        else:
            glyphs, width = font.shape(text, location, features)

        rect = font.calc_glyph_bounds(glyphs).offset(0, y)
        height = -rect.yMin + rect.yMax

        return cls(font, glyphs, rect, x, y, width, height, location)


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


def set_dark_colors(
    surface: SVGSurface,
    foreground: None | str,
    background: None | str,
    dark_foreground: None | str,
    dark_background: None | str,
    output: pathlib.Path,
):
    from blackrenderer.backends.svg import writeSVGElements
    from fontTools.misc import etree as ET

    css = ["@media (prefers-color-scheme: dark) {"]
    if dark_foreground:
        css += [f'path[fill="#{foreground}"] {{', f" fill: #{dark_foreground};", " }"]
    if dark_background:
        css += [f'path[fill="#{background}"] {{', f" fill: #{dark_background};", " }"]
    css += ["}"]

    svg_file = io.BytesIO()
    writeSVGElements(surface._svgElements, surface._viewBox, svg_file)
    svg_file.seek(0)

    tree: ET.ElementTree = ET.parse(svg_file)
    root = tree.getroot()
    style = ET.SubElement(root, "style")
    style.text = "\n" + "\n".join(css) + "\n"

    tree.write(output, pretty_print=True, xml_declaration=True)


def draw(
    font_path: str,
    text: None | str,
    features: str,
    foreground: None | str,
    background: None | str,
    dark_foreground: None | str,
    dark_background: None | str,
    justify: bool,
    output_path: pathlib.Path,
):
    margin = 100
    bounds = None
    lines: list[GlyphLine] = []
    y = margin
    font = Font(font_path)

    if text is None:
        text = font.sample_text

    if text is None:
        raise ValueError("No text provided and no sample text in the font")

    locations = font.locations
    for location in reversed(locations):
        font.set_location(location)

        text_lines = list(reversed(text.split("\n")))
        if justify:
            max_width = 0
            for text_line in text_lines:
                line = GlyphLine.build(
                    font,
                    text_line,
                    features,
                    margin,
                    y,
                    location,
                )
                max_width = max(max_width, line.width)

            font.set_location(location)
            for text_line in text_lines:
                line = GlyphLine.build(
                    font,
                    text_line,
                    features,
                    margin,
                    y,
                    location,
                    max_width,
                )
                lines.append(line)
                bounds = line.rect.union(bounds)
                y += line.height + margin
        else:
            for text_line in text_lines:
                line = GlyphLine.build(
                    font,
                    text_line,
                    features,
                    margin,
                    y,
                    location,
                )
                lines.append(line)
                bounds = line.rect.union(bounds)
                y += line.height + margin

    if dark_foreground and not foreground:
        foreground = "000000"
    if dark_background and not background:
        background = "FFFFFF"

    surface = SVGSurface()

    bounds = bounds.inset(-margin, -margin)
    with surface.canvas(bounds) as canvas:
        if background:
            canvas.drawRectSolid(surface._viewBox, parseColor(background))
        for line in lines:
            font = line.font
            font.set_location(line.location)
            with canvas.savedState():
                # Center align the line.
                x = (bounds[2] - line.rect[2]) / 2 - margin
                canvas.translate(x, line.y)
                for glyph in line.glyphs:
                    with canvas.savedState():
                        canvas.translate(glyph.x_offset, glyph.y_offset)
                        font.draw_glyph(glyph, canvas, foreground)
                    canvas.translate(glyph.x_advance, glyph.y_advance)

    if dark_foreground or dark_background:
        set_dark_colors(
            surface,
            foreground,
            background,
            dark_foreground,
            dark_background,
            output_path,
        )
        return
    surface.saveImage(output_path)


def parseColor(color):
    if len(color) == 8:
        return tuple(int(color[i : i + 2], 16) / 255 for i in (2, 4, 6))
    assert len(color) == 6, color
    return tuple(int(color[i : i + 2], 16) / 255 for i in (0, 2, 4)) + (1,)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create SVG sample.")
    parser.add_argument("font", help="input font")
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
        args.font,
        args.text,
        args.features,
        args.foreground,
        args.background,
        args.dark_foreground,
        args.dark_background,
        args.justify,
        args.output,
    )
