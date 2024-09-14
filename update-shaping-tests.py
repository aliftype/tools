# Copyright 2020 Google Sans Authors
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

"""Update a regression test file with the shaping output of a list of fonts."""

from __future__ import annotations

import enum
import json
from pathlib import Path
from typing import Dict, List, NotRequired, Optional, TypedDict

import uharfbuzz as hb
import yaml
from fontTools.ttLib import TTFont  # type: ignore
from fontTools.ttLib.tables._f_v_a_r import table__f_v_a_r  # type: ignore


def main(args: List[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "shaping_file", type=Path, help="The .yaml shaping definition input file path."
    )
    parser.add_argument(
        "output_file",
        type=Path,
        help="The .json shaping expectations output file path.",
    )
    parser.add_argument(
        "fonts",
        nargs="+",
        type=Path,
        help="The fonts to update the testing file with.",
    )
    parsed_args = parser.parse_args(args)

    input_path: Path = parsed_args.shaping_file
    output_path: Path = parsed_args.output_file
    fonts: List[Path] = parsed_args.fonts

    shaping_input = load_shaping_input(input_path)
    shaping_output = update_shaping_output(shaping_input, fonts)
    output_path.write_text(json.dumps(shaping_output, indent=2, ensure_ascii=False))


def update_shaping_output(
    shaping_input: ShapingInputYaml,
    font_paths: List[Path],
) -> ShapingOutput:
    tests: List[TestDefinition] = []
    shaping_output: ShapingOutput = {"tests": tests}
    if "configuration" in shaping_input:
        shaping_output["configuration"] = shaping_input["configuration"]

    configuration = shaping_input.get("configuration", {})
    for font_path in font_paths:
        blob = hb.Blob.from_file_path(font_path)  # type: ignore
        face = hb.Face(blob)  # type: ignore
        hbfont = hb.Font(face)  # type: ignore
        font = TTFont(font_path)
        for input in shaping_input["input"]:
            for text in input["text"]:
                if "fvar" in font and "variations" not in shaping_input["input"]:
                    fvar: table__f_v_a_r = font["fvar"]  # type: ignore
                    for instance in fvar.instances:
                        instance_input = input.copy()
                        instance_input["variations"] = instance.coordinates
                        run = shape_run(hbfont, font_path, text, input, configuration)
                        tests.append(run)
                else:
                    run = shape_run(hbfont, font_path, text, input, configuration)
                    tests.append(run)

    return shaping_output


def get_shaping_parameters(
    input: ShapingInput,
    configuration: Configuration,
) -> ShapingParameters:
    defaults = configuration.get("defaults", ShapingParameters())
    parameters: ShapingParameters = {}
    for key in ShapingParameters.__annotations__.keys():
        if value := input.get(key, defaults.get(key)):
            parameters[key] = value
    return parameters


def _shape(
    font: hb.Font,  # type: ignore
    text: str,
    parameters: Dict[str, Any],
) -> hb.Buffer:  # type: ignore
    buf = hb.Buffer()  # type: ignore
    buf.add_str(text)
    buf.guess_segment_properties()

    if script := parameters.get("script"):
        buf.script = script
    if direction := parameters.get("direction"):
        buf.direction = direction
    if language := parameters.get("language"):
        buf.language = language

    shapers = []
    if shaper := parameters.get("shaper"):
        shapers = [shaper]

    saved_variations = None
    variations = parameters.get("variations")
    if variations:
        saved_variations = font.get_var_coords_design()
        font.set_variations(variations)

    hb.shape(font, buf, parameters.get("features"), shapers=shapers)  # type: ignore

    if saved_variations is not None:
        font.set_var_coords_design(saved_variations)

    return buf


def _serialize_buffer(
    font: hb.Font,  # type: ignore
    buffer: hb.Buffer,  # type: ignore
    glyphs_only: bool = False,
) -> str:
    outs = []
    for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions):  # type: ignore
        glyph_name = font.glyph_to_string(info.codepoint)
        if glyphs_only:
            outs.append(glyph_name)
            continue
        outs.append("%s=%i" % (glyph_name, info.cluster))
        if pos.position[0] != 0 or pos.position[1] != 0:
            outs[-1] = outs[-1] + "@%i,%i" % (pos.position[0], pos.position[1])
        outs[-1] = outs[-1] + "+%i" % (pos.position[2])
    return "|".join(outs)


def shape_run(
    font: hb.Font,  # type: ignore
    font_path: Path,
    text: str,
    input: ShapingInput,
    configuration: Configuration,
) -> TestDefinition:
    parameters = get_shaping_parameters(input, configuration)
    parameters = json.loads(json.dumps(parameters))
    buffer = _shape(font, text, parameters)

    shaping_comparison_mode = input.get("comparison_mode", ComparisonMode.FULL)
    if shaping_comparison_mode is ComparisonMode.FULL:
        glyphsonly = False
    elif shaping_comparison_mode is ComparisonMode.GLYPHSTREAM:
        glyphsonly = True
    else:
        raise ValueError(f"Unknown comparison mode {shaping_comparison_mode}.")
    expectation = _serialize_buffer(font, buffer, glyphsonly)

    test: TestDefinition = {
        "only": font_path.name,
        "input": text,
        "expectation": expectation,
    }

    for key in TestDefinition.__annotations__.keys():
        if value := input.get(key):
            test[key] = value

    return test


def load_shaping_input(input_path: Path) -> ShapingInputYaml:
    with input_path.open("rb") as tf:
        shaping_input: ShapingInputYaml = yaml.safe_load(tf)

    if "input" not in shaping_input:
        raise ValueError(f"{input_path} does not contain a valid shaping input.")

    inputs = list(shaping_input["input"])
    for input in inputs:
        if "direction" in input:
            input["direction"] = Direction(input["direction"])
        if "comparison_mode" in input:
            input["comparison_mode"] = ComparisonMode(input["comparison_mode"])
    shaping_input["input"] = inputs

    return shaping_input


class Configuration(TypedDict):
    defaults: NotRequired[ShapingParameters]
    forbidden_glyphs: NotRequired[List[str]]


class ShapingInputYaml(TypedDict):
    configuration: NotRequired[Configuration]
    input: List[ShapingInput]


class ShapingInput(TypedDict):
    text: List[str]
    script: Optional[str]
    language: Optional[str]
    direction: Optional[Direction]
    features: Dict[str, bool]
    comparison_mode: ComparisonMode
    variations: Optional[Dict[str, float]]


class ComparisonMode(enum.StrEnum):
    FULL = "full"  # Record glyph names, offsets and advance widths.
    GLYPHSTREAM = "glyphstream"  # Just glyph names.


class Direction(enum.StrEnum):
    LEFT_TO_RIGHT = "ltr"
    RIGHT_TO_LEFT = "rtl"
    TOP_TO_BOTTOM = "ttb"
    BOTTOM_TO_TOP = "btt"


class ShapingOutput(TypedDict):
    configuration: NotRequired[Configuration]
    tests: List[TestDefinition]


class ShapingParameters(TypedDict, total=False):
    script: str
    direction: str
    language: str
    features: Dict[str, bool]
    shaper: str
    variations: Dict[str, float]


class TestDefinition(ShapingParameters):
    input: str
    expectation: str
    only: NotRequired[str]


if __name__ == "__main__":
    main()
