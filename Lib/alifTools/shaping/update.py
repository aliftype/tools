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
from typing import Any, Dict, List, NotRequired, Optional, TypedDict

import uharfbuzz as hb
import yaml

from . import (
    ComparisonMode,
    Configuration,
    Direction,
    ShapingInput,
    ShapingOutput,
    ShapingParameters,
    TestDefinition,
    get_shaping_parameters,
    serialize_buffer,
    shape,
)


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
        font = hb.Font(face)  # type: ignore
        for input in shaping_input["input"]:
            for text in input["text"]:
                if face.has_var_data and "variations" not in input:
                    axis_tags = [axis.tag for axis in face.axis_infos]
                    default_coords = [axis.default_value for axis in face.axis_infos]
                    for instance in face.named_instances:
                        instance_input = input.copy()
                        if instance.design_coords != default_coords:
                            instance_input["variations"] = dict(
                                zip(axis_tags, instance.design_coords)
                            )
                        run = shape_run(
                            font,
                            font_path,
                            text,
                            instance_input,
                            configuration,
                        )
                        tests.append(run)
                else:
                    run = shape_run(font, font_path, text, input, configuration)
                    tests.append(run)
    return shaping_output


def shape_run(
    font: hb.Font,  # type: ignore
    font_path: Path,
    text: str,
    input: ShapingInput,
    configuration: Configuration,
) -> TestDefinition:
    parameters = get_shaping_parameters(input, configuration)
    parameters: ShapingParameters = json.loads(json.dumps(parameters))
    buffer = shape(font, text, parameters)

    shaping_comparison_mode = input.get("comparison_mode", ComparisonMode.FULL)
    if shaping_comparison_mode is ComparisonMode.FULL:
        glyphsonly = False
    elif shaping_comparison_mode is ComparisonMode.GLYPHSTREAM:
        glyphsonly = True
    else:
        raise ValueError(f"Unknown comparison mode {shaping_comparison_mode}.")
    expectation = serialize_buffer(font, buffer, glyphsonly)

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


class ShapingInputYaml(TypedDict):
    configuration: NotRequired[Configuration]
    input: List[ShapingInput]


if __name__ == "__main__":
    main()
