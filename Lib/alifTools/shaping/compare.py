# Copyright 2026 Khaled Hosny
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

"""Compare a font with the reference font from an expectations file."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

import uharfbuzz as hb

from . import _SetVariations

Location = Tuple[Tuple[str, float], ...]

_RASTER = hb.RasterPaint()  # type: ignore


class _RecordingPen:
    def __init__(self):
        self.commands: List[tuple] = []

    def moveTo(self, point):
        self.commands.append(("moveTo", point))

    def lineTo(self, point):
        self.commands.append(("lineTo", point))

    def qCurveTo(self, *points):
        self.commands.append(("qCurveTo", points))

    def curveTo(self, *points):
        self.commands.append(("curveTo", points))

    def closePath(self):
        self.commands.append(("closePath",))

    def endPath(self):
        self.commands.append(("endPath",))


class GlyphKey(NamedTuple):
    path: tuple
    layers: tuple
    advance: int
    paint: bool


def _path(font: hb.Font, gid: int) -> tuple:  # type: ignore
    pen = _RecordingPen()
    font.draw_glyph_with_pen(gid, pen)
    return tuple(pen.commands)


def _glyph_key(font: hb.Font, gid: int) -> GlyphKey:  # type: ignore
    face = font.face
    layers = face.get_glyph_color_layers(gid) or ()
    palettes = face.color_palettes if layers else []
    colors = dict(enumerate(palettes[0].colors)) if palettes else {}
    return GlyphKey(
        _path(font, gid),
        tuple(
            (_path(font, layer.glyph), colors.get(layer.color_index))
            for layer in layers
        ),
        font.get_glyph_h_advance(gid),
        bool(face.glyph_has_color_paint(gid)),
    )


def _bitmap(font: hb.Font, gid: int) -> Optional[tuple]:  # type: ignore
    _RASTER.paint_glyph(font, gid)
    image = _RASTER.render()
    return None if image is None else (image.extents, image.buffer)


def compare_glyphs(
    reference: hb.Font,  # type: ignore
    current: hb.Font,  # type: ignore
    locations: Iterable[Location],
) -> Dict[Location, Dict[str, bool]]:
    changes: Dict[Location, Dict[str, bool]] = {}
    for location in locations:
        found = changes[location] = {}
        variations = dict(location)
        with _SetVariations(reference, variations), _SetVariations(current, variations):
            for current_gid in range(current.face.glyph_count):
                name = current.get_glyph_name(current_gid)
                if not name:
                    continue
                reference_gid = reference.get_glyph_from_name(name)
                if reference_gid is None:
                    continue
                reference_key = _glyph_key(reference, reference_gid)
                current_key = _glyph_key(current, current_gid)
                if reference_key == current_key and not reference_key.paint:
                    continue
                visible = reference_key.advance != current_key.advance
                if not visible:
                    reference_bitmap = _bitmap(reference, reference_gid)
                    visible = reference_bitmap != _bitmap(current, current_gid)
                    if not visible and reference_key == current_key:
                        continue
                found[name] = visible
    return changes


def _positions(buffer: Any) -> List[Tuple[int, int, int, int]]:
    return [
        (p.x_offset, p.y_offset, p.x_advance, p.y_advance)
        for p in buffer.glyph_positions
    ]


def buffers_render_same(
    reference: hb.Font,  # type: ignore
    reference_buffer: Any,
    current: hb.Font,  # type: ignore
    current_buffer: Any,
    variations: Optional[Dict[str, float]],
) -> bool:
    if _positions(reference_buffer) != _positions(current_buffer):
        return False
    with _SetVariations(reference, variations), _SetVariations(current, variations):
        for reference_info, current_info in zip(
            reference_buffer.glyph_infos, current_buffer.glyph_infos
        ):
            reference_gid = reference_info.codepoint
            current_gid = current_info.codepoint
            reference_key = _glyph_key(reference, reference_gid)
            current_key = _glyph_key(current, current_gid)
            if reference_key == current_key and not reference_key.paint:
                continue
            if _bitmap(reference, reference_gid) != _bitmap(current, current_gid):
                return False
    return True
