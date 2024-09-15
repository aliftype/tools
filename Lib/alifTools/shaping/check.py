# Copyright 2020 Google Sans Authors
# Copyright 2021 Simon Cozens
# Copyright 2024 Khaled Hosny

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any, Callable, Dict

import uharfbuzz as hb


class Message:
    """Status messages to be yielded by FontBakeryCheck"""

    def __init__(self, code, header, items=None):
        """
        code: (string) unique code to describe a specific failure condition.
        message: (string) human readable message.
        """
        self.code = code
        self.header = header
        self.items = items


class _FakeBuffer:
    def __init__(self):
        self.glyph_infos: list[_FakeItem] = []
        self.glyph_positions: list[_FakeItem] = []


class _FakeItem:
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


def fix_svg(svg: str) -> str:
    return svg.replace("\n", " ")


def diff(
    old_str: str,
    new_str: str,
) -> str:
    from difflib import SequenceMatcher

    sequence = SequenceMatcher(
        isjunk=lambda x: x in ("|", "+", "=", "@", ","),
        a=new_str,
        b=old_str,
    )

    old = []
    new = []
    for opcode, a0, a1, b0, b1 in sequence.get_opcodes():
        if opcode == "equal":
            old.append(old_str[b0:b1])
            new.append(new_str[a0:a1])
        elif opcode in ("insert", "delete"):
            old.append("<del>" + old_str[b0:b1] + "</del>")
            new.append("<ins>" + new_str[a0:a1] + "</ins>")
        elif opcode == "replace":
            old.append("<del>" + old_str[b0:b1] + "</del>")
            new.append("<ins>" + new_str[a0:a1] + "</ins>")
        else:
            raise RuntimeError("unexpected opcode")
    old = "<span class='expected'>" + "".join(old) + "</span>"
    new = "<span class='actual'>" + "".join(new) + "</span>"
    return "<pre>" + old + "\n" + new + "</pre>"


def _shape(
    font: hb.Font,  # type: ignore
    text: str,
    parameters: Dict[str, Any],
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


def _serialize_buffer(
    font: hb.Font,  # type: ignore
    buffer: hb.Buffer | _FakeBuffer,  # type: ignore
    glyphs_only: bool = False,
) -> str:
    outs = []
    for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions):  # type: ignore
        if (glyph_name := getattr(info, "name", None)) is None:
            glyph_name = font.glyph_to_string(info.codepoint)
        if glyphs_only:
            outs.append(glyph_name)
            continue
        outs.append("%s=%i" % (glyph_name, info.cluster))
        if pos.position[0] != 0 or pos.position[1] != 0:
            outs[-1] = outs[-1] + "@%i,%i" % (pos.position[0], pos.position[1])
        outs[-1] = outs[-1] + "+%i" % (pos.position[2])
    return "|".join(outs)


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
    if (layers := hb.ot_color_glyph_get_layers(face, gid)) and (palette := hb.ot_color_palette_get_colors(face, 0)):  # type: ignore
        for layer in layers:
            id = _glyph_to_svg_id(font, layer.glyph, defs)
            if layer.color_index != 0xFFFF:
                color = _to_svg_color(palette[layer.color_index])
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


def _buffer_to_svg(
    font: hb.Font,  # type: ignore
    buffer: hb.Buffer,  # type: ignore
    parameters: Dict[str, Any],
) -> str:
    import base64

    with _SetVariations(font, parameters.get("variations")):
        svg = _draw_buffer(font, buffer)

    # Use img tag instead of inline SVG as glyphs will have the same IDs across
    # files, which is broken when files us different variations or fonts.
    svg = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    img = f'<img src="data:image/svg+xml;base64,{svg}" alt="SVG output">'
    return img


def _buffer_from_string(
    font: hb.Font,  # type: ignore
    text: str,
) -> _FakeBuffer:
    import re

    buffer = _FakeBuffer()
    buffer.glyph_infos = []
    buffer.glyph_positions = []
    for item in text.split("|"):
        m = re.match(r"^(.*)=(\d+)(@(-?\d+),(-?\d+))?(\+(-?\d+))?$", item)
        if not m:
            raise ValueError("Couldn't parse glyph %s in %s" % (item, text))
        groups = m.groups()

        info = _FakeItem(
            codepoint=font.glyph_from_string(groups[0]),
            cluster=int(groups[1]),
        )
        if info.codepoint is None:
            info.codepoint = 0
            info.name = groups[0]
        buffer.glyph_infos.append(info)

        pos = _FakeItem(
            x_offset=int(groups[3] or 0),
            y_offset=int(groups[4] or 0),
            x_advance=int(groups[6] or 0),
            y_advance=0,  # Sorry, vertical scripts
        )
        buffer.glyph_positions.append(pos)
    return buffer


def create_report_item(
    font: hb.Font,  # type: ignore
    message: str,
    text: str | None = None,
    parameters: Dict[str, Any] = {},
    new_buf: hb.Buffer | _FakeBuffer | None = None,  # type: ignore
    old_buf: hb.Buffer | None = None,  # type: ignore
    note: str | None = None,
    extra_data: Dict | None = None,
) -> str:
    message = f"<h4>{message}"
    if text:
        message += f": {text}"
    if note:
        message += f" ({note})"
    message += "</h4>\n"
    if extra_data:
        message += f"<pre>{extra_data}</pre>\n"

    serialized_new_buf = None
    serialized_old_buf = None
    if old_buf:
        if isinstance(old_buf, _FakeBuffer):
            try:
                serialized_old_buf = _serialize_buffer(font, old_buf)
            except Exception:
                # This may fail if the glyphs are not found in the font
                serialized_old_buf = None
                old_buf = None  # Don't try to draw it either
        else:
            serialized_old_buf = old_buf

    if new_buf:
        glyphs_only = bool(old_buf and isinstance(old_buf, str))
        serialized_new_buf = _serialize_buffer(font, new_buf, glyphs_only=glyphs_only)

    # Report a diff table
    if serialized_old_buf and serialized_new_buf:
        message += f"{diff(serialized_old_buf, serialized_new_buf)}\n"

    # Now draw it as SVG
    if new_buf:
        message += f"Got: {_buffer_to_svg(font, new_buf, parameters)}\n"

    if old_buf and isinstance(old_buf, _FakeBuffer):
        try:
            message += f"Expected: {_buffer_to_svg(font, old_buf, parameters)}"
        except KeyError:
            pass

    return message


def get_from_test_with_default(
    test: Dict[str, Any],
    configuration: Dict[str, Any],
    key: str,
    default: Any | None = None,
):
    defaults = configuration.get("defaults", {})
    return test.get(key, defaults.get(key, default))


def get_shaping_parameters(
    test: Dict[str, Any],
    configuration: Dict[str, Any],
):
    parameters = {}
    for key in ["script", "language", "direction", "features", "shaper", "variations"]:
        parameters[key] = get_from_test_with_default(test, configuration, key)
    return parameters


def get_input_strings(
    test: Dict[str, Any],
    configuration: Dict[str, Any],
) -> list[str]:
    input_type = get_from_test_with_default(test, configuration, "input_type", "string")
    if input_type == "pattern":
        from stringbrewer import StringBrewer

        sb = StringBrewer(
            recipe=test["input"],
            ingredients=configuration["ingredients"],
        )
        return sb.generate_all()
    return [test["input"]]


# This is a very generic "do something with shaping" test runner.
# It'll be given concrete meaning later.
def run_a_set_of_shaping_tests(
    configuration: Dict[str, Any],
    fontpath: Path,
    run_a_test: Callable,
    test_filter: Callable,
    generate_report: Callable,
    preparation: Callable | None = None,
) -> Generator[tuple[bool | None, Message]]:
    blob = hb.Blob.from_file_path(fontpath)  # type: ignore
    face = hb.Face(blob)  # type: ignore
    font = hb.Font(face)  # type: ignore

    shaping_file_found = False
    ran_a_test = False
    extra_data = None

    shaping_basedir = configuration.get("test_directory")
    if not shaping_basedir:
        yield False, Message(
            "no-dir", "Shaping test directory not defined in configuration file"
        )
        return

    shaping_basedir = Path(shaping_basedir)
    if not shaping_basedir.is_dir():
        yield False, Message(
            "not-dir",
            f"Shaping test directory {shaping_basedir} not found or not a directory.",
        )
        return

    for shaping_file in shaping_basedir.glob("*.json"):
        shaping_file_found = True
        try:
            shaping_input_doc = json.loads(shaping_file.read_text(encoding="utf-8"))
        except Exception as e:
            yield False, Message(
                "shaping-invalid-json", f"{shaping_file}: Invalid JSON: {e}."
            )
            return

        configuration = shaping_input_doc.get("configuration", {})
        try:
            shaping_tests = shaping_input_doc["tests"]
        except KeyError:
            yield False, Message(
                "shaping-missing-tests",
                f"{shaping_file}: JSON file must have a 'tests' key.",
            )
            return

        if preparation:
            extra_data = preparation(fontpath, configuration)

        failed_shaping_tests = []
        for test in shaping_tests:
            if not test_filter(test, configuration):
                continue

            if "input" not in test:
                yield False, Message(
                    "shaping-missing-input",
                    f"{shaping_file}: test is missing an input key.",
                )
                return

            exclude_fonts = test.get("exclude", [])
            if fontpath.name in exclude_fonts:
                continue

            only_fonts = test.get("only")
            if only_fonts and fontpath.name not in only_fonts:
                continue

            run_a_test(
                fontpath,
                font,
                test,
                configuration,
                failed_shaping_tests,
                extra_data,
            )
            ran_a_test = True

        if ran_a_test:
            if not failed_shaping_tests:
                yield True, Message("pass", f"{shaping_file}: No regression detected")
            else:
                yield from generate_report(
                    font,
                    configuration,
                    shaping_file,
                    failed_shaping_tests,
                )

    if not shaping_file_found:
        yield None, Message("skip", "No test files found.")

    if not ran_a_test:
        yield None, Message("skip", "No applicable tests ran.")


def check_shaping_regression(
    configuration: Dict[str, Any],
    fontpath: Path,
) -> Generator[tuple[bool | None, Message]]:
    """Check that texts shape as per expectation"""
    yield from run_a_set_of_shaping_tests(
        configuration,
        fontpath,
        run_shaping_regression,
        lambda test, configuration: "expectation" in test,
        generate_shaping_regression_report,
    )


def run_shaping_regression(
    fontpath: Path,
    font: hb.Font,  # type: ignore
    test: Dict[str, Any],
    configuration: Dict[str, Any],
    failed_shaping_tests: list,
    extra_data: Dict[str, Any],
):
    shaping_text = test["input"]
    parameters = get_shaping_parameters(test, configuration)
    output_buf = _shape(font, shaping_text, parameters)
    expectation = test["expectation"]
    if isinstance(expectation, dict):
        expectation = expectation.get(fontpath.name, expectation["default"])
    output_serialized = _serialize_buffer(
        font,
        output_buf,
        glyphs_only="+" not in expectation,
    )

    if output_serialized != expectation:
        failed_shaping_tests.append((test, expectation, output_buf, output_serialized))


def generate_shaping_regression_report(
    font: hb.Font,  # type: ignore
    configuration: Dict[str, Any],
    shaping_file: Path,
    failed_shaping_tests: list,
) -> Generator[tuple[bool | None, Message]]:
    report_items = []
    for test, expected, output_buf, output_serialized in failed_shaping_tests:
        extra_data = {
            k: test[k]
            for k in ["script", "language", "direction", "features", "variations"]
            if k in test
        }
        parameters = get_shaping_parameters(test, configuration)
        # Make HTML report here.
        if "=" in expected:
            buf2 = _buffer_from_string(font, expected)
        else:
            buf2 = expected

        report_item = create_report_item(
            font=font,
            message="Shaping did not match",
            text=test["input"],
            parameters=parameters,
            new_buf=output_buf,
            old_buf=buf2,
            note=test.get("note"),
            extra_data=extra_data,
        )
        report_items.append(report_item)

    header = f"{shaping_file}: Expected and actual shaping not matching"
    yield False, Message("shaping-regression", header, report_items)


def check_shaping_forbidden(
    configuration: Dict[str, Any],
    fontpath: Path,
) -> Generator[tuple[bool | None, Message]]:
    """Check that no forbidden glyphs are found while shaping"""
    yield from run_a_set_of_shaping_tests(
        configuration,
        fontpath,
        run_forbidden_glyph_test,
        lambda test, configuration: "forbidden_glyphs" in configuration,
        forbidden_glyph_test_results,
    )


def run_forbidden_glyph_test(
    fontpath: Path,
    font: hb.Font,  # type: ignore
    test: Dict[str, Any],
    configuration: Dict[str, Any],
    failed_shaping_tests: list,
    extra_data: Dict[str, Any],
):
    import re

    parameters = get_shaping_parameters(test, configuration)
    strings = get_input_strings(test, configuration)
    forbidden_glyphs = configuration["forbidden_glyphs"]
    for shaping_text in strings:
        output_buf = _shape(font, shaping_text, parameters)
        output_serialized = _serialize_buffer(font, output_buf, glyphs_only=True)
        glyph_names = "|" + output_serialized + "|"
        for forbidden in forbidden_glyphs:
            pattern = forbidden
            if not forbidden.startswith(r"\|"):
                pattern = r"\|" + forbidden
            if not forbidden.endswith(r"\|"):
                pattern += r"\|"
            if re.findall(pattern, glyph_names):
                failed_shaping_tests.append((shaping_text, output_buf, forbidden))


def forbidden_glyph_test_results(
    font: hb.Font,  # type: ignore
    configuration: Dict[str, Any],
    shaping_file: Path,
    failed_shaping_tests: list,
) -> Generator[tuple[bool | None, Message]]:
    report_items = []
    for shaping_text, buf, forbidden in failed_shaping_tests:
        msg = f"{shaping_text} produced '{forbidden}'"
        report_items.append(
            create_report_item(font, msg, text=shaping_text, new_buf=buf)
        )

    header = f"{shaping_file}: Forbidden glyphs found while shaping"
    yield False, Message("shaping-forbidden", header, report_items)


def check_shaping_collides(
    configuration: Dict[str, Any],
    fontpath: Path,
) -> Generator[tuple[bool | None, Message]]:
    """Check that no collisions are found while shaping"""
    yield from run_a_set_of_shaping_tests(
        configuration,
        fontpath,
        run_collides_glyph_test,
        lambda test, configuration: "collidoscope" in test
        or "collidoscope" in configuration,
        collides_glyph_test_results,
        setup_glyph_collides,
    )


def setup_glyph_collides(
    fontpath: Path,
    configuration: Dict[str, Any],
) -> Dict[str, Any]:

    collidoscope_configuration = configuration.get("collidoscope")
    if not collidoscope_configuration:
        return {
            "bases": True,
            "marks": True,
            "faraway": True,
            "adjacent_clusters": True,
        }
    from collidoscope import Collidoscope  # type: ignore

    col = Collidoscope(
        fontpath,
        collidoscope_configuration,
        direction=configuration.get("direction", "LTR"),
    )
    return {"collidoscope": col}


def run_collides_glyph_test(
    fontpath: Path,
    font: hb.Font,  # type: ignore
    test: Dict[str, Any],
    configuration: Dict[str, Any],
    failed_shaping_tests: list,
    extra_data: Dict[str, Any],
):

    parameters = get_shaping_parameters(test, configuration)
    strings = get_input_strings(test, configuration)
    col = extra_data["collidoscope"]
    allowed_collisions = get_from_test_with_default(
        test,
        configuration,
        "allowedcollisions",
        [],
    )
    for shaping_text in strings:
        output_buf = _shape(font, shaping_text, parameters)
        glyphs = col.get_glyphs(shaping_text, buf=output_buf)
        collisions = col.has_collisions(glyphs)
        bumps = [f"{c.glyph1}/{c.glyph2}" for c in collisions]
        bumps = [b for b in bumps if b not in allowed_collisions]
        if bumps:
            draw = fix_svg(col.draw_overlaps(glyphs, collisions))
            failed_shaping_tests.append((shaping_text, bumps, draw, output_buf))


def collides_glyph_test_results(
    font: hb.Font,  # type: ignore
    configuration: Dict[str, Any],
    shaping_file: Path,
    failed_shaping_tests: list,
) -> Generator[tuple[bool | None, Message]]:
    report_items = []
    seen_bumps = {}
    for shaping_text, bumps, draw, buf in failed_shaping_tests:
        # Make HTML report here.
        if tuple(bumps) in seen_bumps:
            continue
        seen_bumps[tuple(bumps)] = True
        report_item = create_report_item(
            font=font,
            message=f"{',' .join(bumps)} collision found in"
            f" e.g. <span class='tf'>{shaping_text}</span> <div>{draw}</div>",
            new_buf=buf,
        )
        report_items.append(report_item)
    header = (
        f"{shaping_file}: {len(failed_shaping_tests)} collisions found while shaping"
    )
    yield False, Message("shaping-collides", header, report_items)


def run_checks(
    configuration: Dict[str, Any],
    fontpath: Path,
):
    return {
        "Check that texts shape as per expectation": check_shaping_regression(
            configuration, fontpath
        ),
        "Check that no forbidden glyphs are found while shaping": check_shaping_forbidden(
            configuration, fontpath
        ),
        "Check that no collisions are found while shaping": check_shaping_collides(
            configuration, fontpath
        ),
    }


def emoticon(status: bool | None) -> str:
    return {
        False: "FAIL 🔥",
        None: "SKIP ⏩",
        True: "PASS ✅",
    }[status]


def generate_html(
    results: Dict[str, Generator[tuple[bool | None, Message], None, None]],
) -> tuple[str, bool]:
    all_pass = True

    html = """
<!DOCTYPE html>
<html>
<head>
    <title>Shaping checks results</title>
    <style>
        body {
            font-family: sans-serif;
            max-width: 720px;
            margin: auto;
            padding-bottom: 3rem;
        }

        h3 {
            display: flex;
            align-items: baseline;
            margin-inline-start: -6em;
        }

        h3 .indicator {
            flex: 0 0 5em;
            text-align: end;
            padding-inline-end: 1em;
        }

        h3 .text {
            flex: 1 0;
            font-weight: normal;
        }

        .item ul {
            /*list-style-type: none;*/
            padding-inline-start: 0;
        }

        .items img {
            height: 100px;
            margin: 10px;
        }

        .items del {
            background-color: rgba(255, 0, 0, 0.6);
            text-decoration: none;
        }

        .items ins {
            background-color: rgba(0, 255, 0, 0.6);
            text-decoration: none;
        }

        .items pre .expected {
            background-color: rgba(255, 0, 0, 0.2);
        }

        .items pre .actual {
            background-color: rgba(0, 255, 0, 0.2);
        }
    </style>
</head>
<body>
    <h1>Shaping checks results</h1>
"""
    for check, check_results in results.items():
        html += f"<h2>{check}</h2>\n"
        for status, result in check_results:
            if status is False:
                all_pass = False
            indicator = emoticon(status)
            html += f"<h3><span class='indicator'>{indicator}</span> <span class='text'>{result.header}</span></h3>\n"
            if result.items:
                items = "\n".join(result.items)
                html += f"<div class='items'>\n{items}\n</div>\n"
    html += """
</body>
</html>
"""
    return html, all_pass


def main(argv=None):
    import argparse

    import yaml

    parser = argparse.ArgumentParser(description="Run Google Fonts checks on a font.")
    parser.add_argument("font", help="font file", type=Path)
    parser.add_argument("config", help="configuration file", type=Path)
    parser.add_argument("html", help="output file", type=Path)

    args = parser.parse_args(argv)

    config = yaml.safe_load(args.config.open())
    results = run_checks(config, args.font)
    html, status = generate_html(results)
    args.html.write_text(html)
    return status


if __name__ == "__main__":
    import sys

    sys.exit(main() == False)
