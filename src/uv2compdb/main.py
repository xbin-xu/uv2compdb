"""
Generate Compilation Database by parse Keil uVision project.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import logging
import argparse
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from functools import partial, cached_property
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname).1s] %(message)s",
)

predefined_regex = re.compile(r"^#define\s+(\S+)(?:\s+(.*))?")


def _to_posix_path(path: str) -> str:
    """Convert Windows path separators to POSIX format."""
    return path.replace("\\", "/")


def _split_and_strip(text: str, delimiter: str) -> list[str]:
    """Split text by delimiter and strip whitespace from each part."""
    return [striped for item in text.split(delimiter) if (striped := item.strip())]


@dataclass(frozen=True)
class Toolchain:
    path: str
    compiler: str
    assembler: str


@dataclass
class FileObject:
    file: str
    arguments: list[str] = field(default_factory=list)


@dataclass
class TargetSetting:
    name: str
    toolchain: Toolchain
    file_objects: list[FileObject] = field(default_factory=list)


@dataclass(frozen=True)
class CommandObject:
    directory: str
    file: str
    arguments: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VariousControls:
    """
    Various Controls Levels: Target, Group, File

    Various Controls Rules:
        OPTIONS      = INCLUDE_PATH + MISC + DEFINE
        INCLUDE_PATH = File.include_path + Group.include_path
                       + Target.include_path
        MISC         = Target.misc_controls + Group.misc_controls
                       + File.misc_controls
        DEFINE       = Target.undefine + Target.define
                       + Group.undefine + Group.define
                       + File.undefine + File.define
    """

    misc_controls: list[str] = field(default_factory=list)
    define: list[str] = field(default_factory=list)
    undefine: list[str] = field(default_factory=list)
    include_path: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return " ".join(self.get_options())

    def get_options(self) -> list[str]:
        # 'MBEDTLS_CONFIG_FILE=/\"config-aes-cbc.h/\"'
        #    => '-DMBEDTLS_CONFIG_FILE="config-aes-cbc.h"'
        return (
            [f"-I{_to_posix_path(x)}" for x in self.include_path]
            + [f"{_to_posix_path(x)}" for x in self.misc_controls]
            + [f"-U{_to_posix_path(x)}" for x in self.undefine]
            + [f"-D{_to_posix_path(x.replace(r'\"', '"'))}" for x in self.define]
        )

    @classmethod
    def merge(cls, parent: VariousControls, child: VariousControls) -> VariousControls:
        return cls(
            misc_controls=parent.misc_controls + child.misc_controls,
            define=parent.undefine + parent.define + child.undefine + child.define,
            undefine=[],
            include_path=child.include_path + parent.include_path,
        )


class UV2CompDB:
    """Keil uVision project parser."""

    # TODO: how to deal with delimiters inside text (e.g., -DFOO="(1, 2)")
    UV_VARIOUS_CONTROLS_MAP: dict[str, tuple[str, Callable[[str], list[str]]]] = {
        "MiscControls": ("misc_controls", partial(_split_and_strip, delimiter=" ")),
        "Define": ("define", partial(_split_and_strip, delimiter=",")),
        "Undefine": ("undefine", partial(_split_and_strip, delimiter=",")),
        "IncludePath": ("include_path", partial(_split_and_strip, delimiter=";")),
    }

    UV_TOOLCHAIN_MAP: dict[str, Toolchain] = {
        "0x00": Toolchain("", "c51", ""),
        "0x40": Toolchain("", "armcc", "armasm"),
        "0x41": Toolchain("", "armclang", "armasm"),
    }

    def __init__(self, project_path: Path) -> None:
        self.project_path: Path = project_path

    @cached_property
    def root(self) -> ET.Element:
        tree = ET.parse(self.project_path)
        return tree.getroot()

    @cached_property
    def targets(self) -> dict[str, ET.Element]:
        targets = {}
        for target in self.root.findall(".//Target"):
            if target_name := self._get_text(target.find("TargetName")):
                targets[target_name] = target
        return targets

    def _get_text(
        self, elem: ET.Element | None, pred: Callable[[str], list[str]] | None = None
    ) -> str | list[str] | None:
        if elem is None or elem.text is None:
            return None
        return pred(elem.text) if pred else elem.text

    def get_various_controls(self, elem: ET.Element | None) -> VariousControls | None:
        if elem is None:
            return None

        # None: True, "0": False, "1": True, "2": inherit
        if self._get_text(elem.find(".//CommonProperty/IncludeInBuild")) == "0":
            return None

        result = {}
        for name, (var_name, pred) in self.UV_VARIOUS_CONTROLS_MAP.items():
            result[var_name] = (
                self._get_text(elem.find(f".//Cads/VariousControls/{name}"), pred) or []
            )
        return VariousControls(**result)

    def get_toolchain(self, target: ET.Element | None) -> Toolchain | None:
        if target is None:
            return None

        if not (toolset_number := self._get_text(target.find("ToolsetNumber"))):
            return None

        uac6 = self._get_text(target.find("uAC6")) or ""
        key = toolset_number + uac6
        if not (toolchain := self.UV_TOOLCHAIN_MAP.get(key)):
            return None

        compiler_path = shutil.which(toolchain.compiler)
        return Toolchain(
            path=(
                Path(compiler_path).parent.resolve().as_posix()
                if compiler_path
                else toolchain.path
            ),
            compiler=(
                _to_posix_path(compiler_path) if compiler_path else toolchain.compiler
            ),
            assembler=(
                (Path(compiler_path).parent / toolchain.assembler).resolve().as_posix()
                if compiler_path
                else toolchain.assembler
            ),
        )

    def parse(self, target_name: str) -> TargetSetting | None:
        if (target := self.targets.get(target_name)) is None:
            logger.warning(f"Not found target: {target_name}")
            return None

        if (toolchain := self.get_toolchain(target)) is None:
            logger.warning("Not found toolchain")
            return None
        logger.info(f"Toolchain: {toolchain}")

        if (target_vc := self.get_various_controls(target)) is None:
            logger.warning(f"Not found target_controls in target: {target_name}")
            return None

        target_setting = TargetSetting(
            name=target_name,
            toolchain=toolchain,
        )
        file_objects = target_setting.file_objects
        for group in target.findall(".//Group"):
            if (group_vc := self.get_various_controls(group)) is None:
                continue

            current_vc = VariousControls.merge(target_vc, group_vc)
            for file in group.findall(".//File"):
                file_path = self._get_text(file.find("FilePath"))
                # file_type = self._get_text(file.find("FileType"))

                if not file_path or not file_path.endswith(
                    (".s", ".c", ".cpp", ".cc", ".cx", ".cxx")
                ):
                    continue

                if (file_controls := self.get_various_controls(file)) is None:
                    continue

                file = _to_posix_path(file_path)
                file_objects.append(
                    FileObject(
                        file=file,
                        arguments=VariousControls.merge(
                            current_vc, file_controls
                        ).get_options(),
                    )
                )
                # logger.debug(f"file_object: {file_objects[-1]}")
        return target_setting

    def get_predefined_macros(self, toolchain: Toolchain | None) -> list[str]:
        if toolchain is None:
            return []

        include_path = (
            [f"-I{(Path(toolchain.path).parent / 'include').resolve().as_posix()}"]
            if toolchain.path
            else []
        )

        if "armcc" in toolchain.compiler:
            cmd = f"{toolchain.compiler} --list_macros"
        elif "armclang" in toolchain.compiler:
            cmd = f"{toolchain.compiler} --target=arm-arm-none-eabi -dM -E -"
        else:
            return include_path

        logger.info(f"Get predefined macro by: `{cmd}`")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, input="")
            if result.returncode != 0:
                logger.warning(
                    f"Exited with code {result.returncode}: {result.stderr.strip()}"
                )
                return include_path
        except (FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to invoke compiler: {e}")
            return include_path

        return [
            f"-D{name}={value}"
            for line in result.stdout.splitlines()
            if (m := predefined_regex.match(line.strip()))
            for name, value in [m.groups()]
        ] + include_path

    def generate_command_objects(
        self,
        target_setting: TargetSetting | None,
        extra_args: list[str] = [],
        predefined_macros: bool = False,
    ) -> list[CommandObject]:
        if target_setting is None:
            return []

        command_objects = []
        directory = self.project_path.parent.resolve().as_posix()
        toolchain_args = (
            self.get_predefined_macros(target_setting.toolchain)
            if predefined_macros
            else []
        )
        for file_object in target_setting.file_objects:
            command_objects.append(
                CommandObject(
                    directory=directory,
                    file=file_object.file,
                    arguments=(
                        [target_setting.toolchain.compiler]
                        + toolchain_args
                        + file_object.arguments
                        + extra_args
                    ),
                )
            )
        return command_objects


def generate_compile_commands(
    command_objects: list[CommandObject], output: Path
) -> bool:
    if not command_objects:
        logger.warning("No command objects")
        return False

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(
            [asdict(obj) for obj in command_objects],
            f,
            indent=4,
            ensure_ascii=False,
        )

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json by parse Keil uVision project"
    )
    parser.add_argument("-a", "--arguments", default=None, help="add extra arguments")
    parser.add_argument(
        "-A",
        "--predefined_macros",
        action="store_true",
        help="try to add predefined macros",
    )
    parser.add_argument("-t", "--target", default=None, help="target name")
    parser.add_argument(
        "-o",
        "--output",
        default="compile_commands.json",
        help="output dir/file path (default: compile_commands.json)",
    )
    parser.add_argument("project", type=Path, help="path to .uvproj[x] file")

    args = parser.parse_args()

    try:
        uv2compdb = UV2CompDB(args.project)

        if not (targets := list(uv2compdb.targets.keys())):
            logger.error("No targets found in project")
            return 1

        if not args.target:
            args.target = targets[0]
            logger.warning(
                f"Project has multi targets: {targets}, use the first {args.target}"
            )
        elif args.target not in targets:
            logger.error(f"Not found target: {args.target}")
            return 1

        output_path = Path(args.output)
        if args.output.endswith(("/", "\\")) or (
            output_path.exists() and output_path.is_dir()
        ):
            args.output = output_path / "compile_commands.json"
        else:
            args.output = output_path

        target_setting = uv2compdb.parse(args.target)
        command_objects = uv2compdb.generate_command_objects(
            target_setting,
            _split_and_strip(args.arguments, delimiter=" ") if args.arguments else [],
            args.predefined_macros,
        )
        if not generate_compile_commands(command_objects, args.output):
            return 1
        logger.info(f"Generate at {args.output.resolve().as_posix()}")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1

    return 0
