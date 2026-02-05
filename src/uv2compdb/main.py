"""
Generate Compilation Database by parse Keil µVision project.
"""

import shlex
import logging
import argparse
from pathlib import Path
from importlib.metadata import version

from uv2compdb.parser import UV2CompDB, generate_compile_commands

__version__ = version("uv2compdb")
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname).1s] %(message)s",
    # format="[%(levelname).1s] [%(asctime)s] [%(filename)s:%(lineno)d] %(message)s",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json by parse Keil µVision project"
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=__version__,
        help="show version and exit",
    )
    parser.add_argument("-a", "--arguments", default=None, help="add extra arguments")
    parser.add_argument(
        "-b",
        "--build",
        action="store_true",
        help="try to build while dep/build_log files don't not exist",
    )
    parser.add_argument("-t", "--target", default=None, help="target name")
    parser.add_argument(
        "-o",
        "--output",
        default="compile_commands.json",
        help="output dir/file path (default: compile_commands.json)",
    )
    parser.add_argument(
        "-p",
        "--predefined",
        action="store_true",
        help="try to add predefined macros",
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
                f"Project has target(s): {targets}, use the first {args.target}"
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

        target_setting = uv2compdb.parse(args.target, args.build)
        command_objects = uv2compdb.generate_command_objects(
            target_setting,
            shlex.split(args.arguments) if args.arguments else [],
            args.predefined,
        )
        if not generate_compile_commands(command_objects, args.output):
            return 1
        logger.info(f"Generate at {args.output.resolve().as_posix()}")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1

    return 0
