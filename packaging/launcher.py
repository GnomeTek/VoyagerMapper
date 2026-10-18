"""PyInstaller entry point: `voymap.exe` opens the GUI; `voymap.exe <subcommand>` or
`voymap-cli.exe ...` runs the command line. Both executables share this file."""
import multiprocessing
import sys

multiprocessing.freeze_support()


def main():
    args = sys.argv[1:]
    if not args or args == ["gui"]:
        from voymap.gui.app import main as gui_main
        gui_main()
    else:
        from voymap.cli import main as cli_main
        cli_main(args)


if __name__ == "__main__":
    main()
