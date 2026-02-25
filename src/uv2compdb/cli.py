"""
Generate Compilation Database by parse Keil µVision project.
"""

import shlex
import logging
import argparse
from pathlib import Path

from uv2compdb._version import __version__
from uv2compdb.uvision import UV2CompDB, generate_compile_commands

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json by parse Keil µVision project",
        add_help=False,
    )
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=__version__,
        help="Show version and exit",
    )
    parser.add_argument(
        "-a",
        "--arguments",
        default=None,
        help="Add extra arguments",
    )
    parser.add_argument(
        "-b",
        "--build",
        action="store_true",
        help="Try to build while dep/build_log files don't not exist",
    )
    parser.add_argument(
        "-t",
        "--target",
        default=None,
        help="Target name",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="compile_commands.json",
        help="Output dir/file path (default: compile_commands.json)",
    )
    parser.add_argument(
        "-p",
        "--predefined",
        action="store_true",
        help="Try to add predefined macros",
    )
    parser.add_argument(
        "project",
        type=Path,
        help="Path to .uvproj[x] file",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[%(levelname).1s] %(message)s",
        # format="[%(levelname).1s] [%(asctime)s] [%(filename)s:%(lineno)d] %(message)s",
    )

    try:
        uv2compdb = UV2CompDB(args.project)

        if not (targets := list(uv2compdb.targets.keys())):
            logger.error("No targets found in project")
            return 1

        logger.debug(f"Project has target(s): {targets}")
        if not args.target:
            args.target = targets[0]
            logger.warning(f"Not specified target, use the first '{args.target}'")
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
        logger.critical(f"Unexpected error: {e}", exc_info=args.verbose)
        return 1

    return 0
