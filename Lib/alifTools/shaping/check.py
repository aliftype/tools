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

from . import (
    Configuration,
    ShapingParameters,
    TestDefinition,
    buffer_to_svg,
    get_shaping_parameters,
    serialize_buffer,
    shape,
    FakeBuffer,
    FakeItem,
)


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


def _buffer_from_string(
    font: hb.Font,  # type: ignore
    text: str,
) -> FakeBuffer:
    import re

    buffer = FakeBuffer()
    buffer.glyph_infos = []
    buffer.glyph_positions = []
    for item in text.split("|"):
        m = re.match(r"^(.*)=(\d+)(@(-?\d+),(-?\d+))?(\+(-?\d+))?$", item)
        if not m:
            raise ValueError("Couldn't parse glyph %s in %s" % (item, text))
        groups = m.groups()

        info = FakeItem(
            codepoint=font.glyph_from_string(groups[0]),
            cluster=int(groups[1]),
        )
        if info.codepoint is None:
            info.codepoint = 0
            info.name = groups[0]
        buffer.glyph_infos.append(info)

        pos = FakeItem(
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
    parameters: ShapingParameters = {},
    new_buf: hb.Buffer | None = None,  # type: ignore
    old_buf: hb.Buffer | FakeBuffer | str | None = None,  # type: ignore
    serialized_new_buf: str | None = None,
    serialized_old_buf: str | None = None,
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

    # Report a diff table
    if serialized_old_buf and serialized_new_buf:
        message += f"{diff(serialized_old_buf, serialized_new_buf)}\n"

    # Now draw it as SVG
    if new_buf:
        message += f"Got: {buffer_to_svg(font, new_buf, parameters)}\n"

    if old_buf and isinstance(old_buf, FakeBuffer):
        try:
            message += f"Expected: {buffer_to_svg(font, old_buf, parameters)}"
        except KeyError:
            pass

    return message


def get_from_test_with_default(
    test: TestDefinition,
    configuration: Configuration,
    key: str,
    default: Any,
) -> Any:
    defaults = configuration.get("defaults", {})
    return test.get(key, defaults.get(key, default))


def get_input_strings(
    test: TestDefinition,
    configuration: Configuration,
) -> list[str]:
    input_type = get_from_test_with_default(test, configuration, "input_type", "string")
    if input_type == "pattern":
        from stringbrewer import StringBrewer

        assert "ingredients" in configuration
        sb = StringBrewer(
            recipe=test["input"],
            ingredients=configuration["ingredients"],
        )
        return sb.generate_all()
    return [test["input"]]


# This is a very generic "do something with shaping" test runner.
# It'll be given concrete meaning later.
def run_a_set_of_shaping_tests(
    configuration: Configuration,
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
        yield (
            False,
            Message(
                "no-dir",
                "Shaping test directory not defined in configuration file",
            ),
        )
        return

    shaping_basedir = Path(shaping_basedir)
    if not shaping_basedir.is_dir():
        yield (
            False,
            Message(
                "not-dir",
                f"Shaping test directory {shaping_basedir} not found or not a directory.",
            ),
        )
        return

    for shaping_file in shaping_basedir.glob("*.json"):
        shaping_file_found = True
        try:
            shaping_input_doc = json.loads(shaping_file.read_text(encoding="utf-8"))
        except Exception as e:
            yield (
                False,
                Message("shaping-invalid-json", f"{shaping_file}: Invalid JSON: {e}."),
            )
            return

        configuration = shaping_input_doc.get("configuration", {})
        try:
            shaping_tests = shaping_input_doc["tests"]
        except KeyError:
            yield (
                False,
                Message(
                    "shaping-missing-tests",
                    f"{shaping_file}: JSON file must have a 'tests' key.",
                ),
            )
            return

        if preparation:
            extra_data = preparation(fontpath, configuration)

        failed_shaping_tests = []
        for test in shaping_tests:
            if not test_filter(test, configuration):
                continue

            if "input" not in test:
                yield (
                    False,
                    Message(
                        "shaping-missing-input",
                        f"{shaping_file}: test is missing an input key.",
                    ),
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
    configuration: Configuration,
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
    test: TestDefinition,
    configuration: Configuration,
    failed_shaping_tests: list,
    extra_data: Dict[str, Any],
):
    shaping_text = test["input"]
    parameters = get_shaping_parameters(test, configuration)
    output_buf = shape(font, shaping_text, parameters)
    expectation = test["expectation"]
    if isinstance(expectation, dict):
        expectation = expectation.get(fontpath.name, expectation["default"])
    output_serialized = serialize_buffer(
        font,
        output_buf,
        glyphs_only="+" not in expectation,
    )

    if output_serialized != expectation:
        failed_shaping_tests.append((test, expectation, output_buf, output_serialized))


def generate_shaping_regression_report(
    font: hb.Font,  # type: ignore
    configuration: Configuration,
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
            serialized_new_buf=output_serialized,
            serialized_old_buf=expected,
            note=test.get("note"),
            extra_data=extra_data,
        )
        report_items.append(report_item)

    header = f"{shaping_file}: Expected and actual shaping not matching"
    yield False, Message("shaping-regression", header, report_items)


def check_shaping_forbidden(
    configuration: Configuration,
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
    test: TestDefinition,
    configuration: Configuration,
    failed_shaping_tests: list,
    extra_data: Dict[str, Any],
):
    import re

    assert "forbidden_glyphs" in configuration

    parameters = get_shaping_parameters(test, configuration)
    strings = get_input_strings(test, configuration)
    forbidden_glyphs = configuration["forbidden_glyphs"]
    for shaping_text in strings:
        output_buf = shape(font, shaping_text, parameters)
        output_serialized = serialize_buffer(font, output_buf, glyphs_only=True)
        glyph_names = "|" + output_serialized + "|"
        for forbidden in forbidden_glyphs:
            pattern = forbidden
            if not forbidden.startswith(r"\|"):
                pattern = r"\|" + forbidden
            if not forbidden.endswith(r"\|"):
                pattern += r"\|"
            if re.findall(pattern, glyph_names):
                highlighted = re.sub(
                    pattern,
                    lambda m: "<del>" + m.group(0) + "</del>",
                    glyph_names,
                )
                failed_shaping_tests.append(
                    (
                        shaping_text,
                        output_buf,
                        highlighted,
                        pattern,
                    )
                )


def forbidden_glyph_test_results(
    font: hb.Font,  # type: ignore
    configuration: Configuration,
    shaping_file: Path,
    failed_shaping_tests: list,
) -> Generator[tuple[bool | None, Message]]:
    report_items = []
    for shaping_text, buf, highlighted, pattern in failed_shaping_tests:
        msg = f"{shaping_text} produced glyphs matching:</br><code>{pattern}</code>"
        report_items.append(
            create_report_item(
                font,
                msg,
                new_buf=buf,
                extra_data=highlighted,
            )
        )

    header = f"{shaping_file}: Forbidden glyphs found while shaping"
    yield False, Message("shaping-forbidden", header, report_items)


def check_shaping_collides(
    configuration: Configuration,
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
    configuration: Configuration,
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
    test: TestDefinition,
    configuration: Configuration,
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
        output_buf = shape(font, shaping_text, parameters)
        glyphs = col.get_glyphs(shaping_text, buf=output_buf)
        collisions = col.has_collisions(glyphs)
        bumps = [f"{c.glyph1}/{c.glyph2}" for c in collisions]
        bumps = [b for b in bumps if b not in allowed_collisions]
        if bumps:
            draw = fix_svg(col.draw_overlaps(glyphs, collisions))
            failed_shaping_tests.append((shaping_text, bumps, draw, output_buf))


def collides_glyph_test_results(
    font: hb.Font,  # type: ignore
    configuration: Configuration,
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
            message=f"{','.join(bumps)} collision found in"
            f" e.g. <span class='tf'>{shaping_text}</span> <div>{draw}</div>",
            new_buf=buf,
        )
        report_items.append(report_item)
    header = (
        f"{shaping_file}: {len(failed_shaping_tests)} collisions found while shaping"
    )
    yield False, Message("shaping-collides", header, report_items)


def run_checks(
    configuration: Configuration,
    fontpath: Path,
):
    return {
        "Check that texts shape as per expectation": check_shaping_regression(
            configuration,
            fontpath,
        ),
        "Check that no forbidden glyphs are found while shaping": check_shaping_forbidden(
            configuration,
            fontpath,
        ),
        "Check that no collisions are found while shaping": check_shaping_collides(
            configuration,
            fontpath,
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
