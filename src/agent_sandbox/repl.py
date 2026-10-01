"""Interactive REPL to navigate and execute commands inside a CMEK Sandbox (for humans)."""

import argparse
import cmd
import readline  # Enable arrow keys & history
import shlex
import sys
from pathlib import Path

from .client import get_agent_client, get_runtime_name
from .code_execution import CodeExecutionSandbox
from .state import StateStore


class SandboxREPL(cmd.Cmd):
    intro = (
        "\n"
        "====================================================================\n"
        "  Google Cloud Agent Platform CMEK Sandbox Interactive REPL         \n"
        "====================================================================\n"
        "  Commands:\n"
        "    !<bash_command>       Run shell command (e.g. !ls -la, !pwd, !whoami)\n"
        "    <python_expression>   Execute Python code (e.g. print(1 + 1))\n"
        "    upload <local_path>   Upload a local file to the sandbox\n"
        "    exit / quit           Destroy sandbox and exit cleanly\n"
        "====================================================================\n"
    )
    prompt = "sandbox (/home/bard)> "

    def __init__(self, sandbox: CodeExecutionSandbox):
        super().__init__()
        self.sandbox = sandbox

    def default(self, line: str):
        line = line.strip()
        if not line:
            return

        if line.startswith("!"):
            # Bash command execution
            bash_cmd = line[1:].strip()
            if not bash_cmd:
                print("Usage: !<command> (e.g. !ls -la)")
                return
            try:
                out = self.sandbox.run_command(bash_cmd)
                if out:
                    print(out.rstrip())
            except Exception as e:
                print(f"Error: {e}")
        else:
            # Python code execution
            # Wrap standalone expression in print if not already printing or assigning
            code = line
            if not any(code.startswith(kw) for kw in ["print", "import", "from", "def", "class", "for", "while", "if", "with"]) and "=" not in code:
                code = f"print({code})"

            try:
                out = self.sandbox.execute_code(code)
                if out:
                    print(out.rstrip())
            except Exception as e:
                print(f"Error: {e}")

    def do_upload(self, arg: str):
        """Upload a local file to the sandbox. Usage: upload <local_filepath>"""
        parts = shlex.split(arg)
        if not parts:
            print("Usage: upload <local_filepath>")
            return
        local_path = Path(parts[0])
        if not local_path.is_file():
            print(f"Error: Local file '{local_path}' does not exist.")
            return

        filename = local_path.name
        content = local_path.read_bytes()
        try:
            print(f"Uploading {local_path} ({len(content)} bytes)...")
            self.sandbox.execute(
                code=f"print('Uploaded: {filename}')",
                files=[{"name": filename, "content": content}],
            )
            print(f"File '{filename}' uploaded successfully to remote sandbox.")
        except Exception as e:
            print(f"Upload failed: {e}")

    def do_exit(self, arg):
        """Exit the REPL and terminate the sandbox."""
        print("\nTerminating sandbox session...")
        return True

    def do_quit(self, arg):
        """Exit the REPL and terminate the sandbox."""
        return self.do_exit(arg)

    def do_EOF(self, arg):
        """Handle Ctrl+D to exit cleanly."""
        print()
        return self.do_exit(arg)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Interactive CMEK Sandbox REPL")
    parser.add_argument(
        "--ttl",
        default="1800s",
        help="Sandbox time-to-live, billed per second (e.g. 900s, 30m)",
    )
    args = parser.parse_args(argv)

    client = get_agent_client()
    runtime_name = get_runtime_name()

    print(f"Provisioning CMEK Sandbox under {runtime_name}...")
    try:
        with CodeExecutionSandbox(
            client=client,
            runtime_name=runtime_name,
            display_name="asbx-interactive-repl",
            ttl=args.ttl,
            tracker=StateStore(),
        ) as sandbox:
            print(f"Sandbox ready: {sandbox.sandbox_name}")
            repl = SandboxREPL(sandbox)
            repl.cmdloop()
    except KeyboardInterrupt:
        print("\nSession interrupted.")
    finally:
        print("Sandbox session closed.")


if __name__ == "__main__":
    main()
