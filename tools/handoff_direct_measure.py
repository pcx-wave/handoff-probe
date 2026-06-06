"""
handoff_direct_measure.py — measure Claude's actual token cost doing C1–C5 directly.

Gives Claude the same prompts as the probe, with real tools (read/write/bash),
and records full token usage across all turns.  Produces a comparison table
against the delegation numbers already measured.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 tools/handoff_direct_measure.py [--runs N] [--model MODEL] [--out FILE]

Defaults: --runs 3, --model claude-haiku-4-5-20251001, --out results/direct_measure.jsonl
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import anthropic

# ── seed workdir files (same as probe) ────────────────────────────────────────

SEED_FILES = {
    "app.py": textwrap.dedent("""\
        from flask import Flask, request, jsonify
        from models import db, User, Order

        app = Flask(__name__)

        @app.route('/users', methods=['GET'])
        def get_users():
            users = User.query.all()
            return jsonify([u.to_dict() for u in users])

        @app.route('/users/<int:user_id>', methods=['GET'])
        def get_user(user_id):
            user = User.query.get_or_404(user_id)
            return jsonify(user.to_dict())

        @app.route('/orders/<int:order_id>', methods=['GET'])
        def get_order(order_id):
            order = Order.query.get_or_404(order_id)
            return jsonify(order.to_dict())

        @app.route('/orders', methods=['POST'])
        def create_order():
            data = request.json
            order = Order(**data)
            db.session.add(order)
            db.session.commit()
            return jsonify(order.to_dict()), 201
    """),
    "models.py": textwrap.dedent("""\
        from flask_sqlalchemy import SQLAlchemy
        db = SQLAlchemy()

        class User(db.Model):
            id = db.Column(db.Integer, primary_key=True)
            username = db.Column(db.String(80))
            email = db.Column(db.String(120))
            age = db.Column(db.Integer)
            def to_dict(self):
                return {'id': self.id, 'username': self.username,
                        'email': self.email, 'age': self.age}

        class Order(db.Model):
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
            def to_dict(self):
                return {'id': self.id, 'user_id': self.user_id}
    """),
    "utils.py": textwrap.dedent("""\
        def format_response(data, status=200):
            return {'data': data, 'status': status}
    """),
}

# ── task prompts (identical to SweepSignal) ───────────────────────────────────

CONTEXT_HEADER = (
    "[CONTEXT]\n"
    "Stack: Python 3.10, Flask 3.x, SQLAlchemy 2.x, REST API with JWT auth.\n"
    "Project: user management SaaS API. Files: app.py (routes), models.py (User, Order), utils.py (helpers).\n"
    "\n"
    "[TASK]\n"
)

SYNC_MODULE = textwrap.dedent("""\
    import requests
    from flask import Flask, request, jsonify
    from models import db, User

    app = Flask(__name__)

    @app.route("/api/data", methods=["GET"])
    def get_data():
        data = db.query(User).all()
        response = requests.get("https://api.example.com/external")
        return jsonify({"data": data, "external": response.json()})

    @app.route("/api/process", methods=["POST"])
    def process():
        payload = request.json
        result = db.query(User).filter_by(id=payload["id"]).first()
        return jsonify({"result": result})
""")

TASKS = {
    "C1": CONTEXT_HEADER + "Print hello world",
    "C2": CONTEXT_HEADER + "Write a function to reverse a string. Return only the function.",
    "C3": (
        CONTEXT_HEADER
        + "Write a REST endpoint POST /users/validate that accepts JSON with username, age, email, "
        "validates them, and returns JSON with is_valid bool and errors list."
    ),
    "C4": (
        CONTEXT_HEADER
        + "Refactor the following sync module to use async/await throughout. "
        "Output the complete refactored code in a single code block.\n\n"
        + SYNC_MODULE
    ),
    "C5": (
        CONTEXT_HEADER
        + "Add an in-memory caching layer (TTL=60s) to the following API for GET endpoints. "
        "Output the complete modified code in a single code block.\n\n"
        + "\n".join([
            "from flask import Flask, request, jsonify",
            "from models import db, User, Order",
            "",
            "app = Flask(__name__)",
            "",
            "@app.route('/users', methods=['GET'])",
            "def get_users():",
            "    users = User.query.all()",
            "    return jsonify([u.to_dict() for u in users])",
            "",
            "@app.route('/users/<int:user_id>', methods=['GET'])",
            "def get_user(user_id):",
            "    user = User.query.get_or_404(user_id)",
            "    return jsonify(user.to_dict())",
            "",
            "@app.route('/orders/<int:order_id>', methods=['GET'])",
            "def get_order(order_id):",
            "    order = Order.query.get_or_404(order_id)",
            "    return jsonify(order.to_dict())",
            "",
            "@app.route('/orders', methods=['POST'])",
            "def create_order():",
            "    data = request.json",
            "    order = Order(**data)",
            "    db.session.add(order)",
            "    db.session.commit()",
            "    return jsonify(order.to_dict()), 201",
        ])
    ),
}

# ── tool definitions ───────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from the working directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the working directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in the working directory.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_python",
        "description": "Run a Python snippet and return stdout+stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"}
            },
            "required": ["code"],
        },
    },
]


def _dispatch_tool(name: str, inputs: dict, workdir: str) -> str:
    if name == "read_file":
        p = Path(workdir) / inputs["path"]
        return p.read_text() if p.exists() else f"File not found: {inputs['path']}"
    if name == "write_file":
        p = Path(workdir) / inputs["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(inputs["content"])
        return f"Written {inputs['path']} ({len(inputs['content'])} chars)"
    if name == "list_files":
        files = [str(f.relative_to(workdir)) for f in Path(workdir).rglob("*") if f.is_file()]
        return "\n".join(files) if files else "(empty)"
    if name == "run_python":
        result = subprocess.run(
            [sys.executable, "-c", inputs["code"]],
            capture_output=True, text=True, timeout=10, cwd=workdir,
        )
        return (result.stdout + result.stderr).strip() or "(no output)"
    return f"Unknown tool: {name}"


def run_task(client: anthropic.Anthropic, model: str, task_prompt: str) -> dict:
    """Run one task, return token breakdown and turn count."""
    with tempfile.TemporaryDirectory() as workdir:
        # Seed files
        for fname, content in SEED_FILES.items():
            (Path(workdir) / fname).write_text(content)

        messages = [{"role": "user", "content": task_prompt}]

        total_input = 0
        total_output = 0
        total_cache_write = 0
        total_cache_read = 0
        tool_calls = 0
        turns = 0
        t0 = time.time()

        while True:
            turns += 1
            resp = client.messages.create(
                model=model,
                max_tokens=4096,
                tools=TOOLS,
                messages=messages,
            )

            u = resp.usage
            total_input += u.input_tokens
            total_output += u.output_tokens
            total_cache_write += getattr(u, "cache_creation_input_tokens", 0)
            total_cache_read += getattr(u, "cache_read_input_tokens", 0)

            # Accumulate assistant turn
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "end_turn":
                break

            if resp.stop_reason == "tool_use":
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        tool_calls += 1
                        result = _dispatch_tool(block.name, block.input, workdir)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                break  # max_tokens or other stop

        elapsed = time.time() - t0
        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_write_tokens": total_cache_write,
            "cache_read_tokens": total_cache_read,
            "tool_calls": tool_calls,
            "turns": turns,
            "elapsed_s": round(elapsed, 1),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="Runs per task level (default 3)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Model to use (default: haiku-4.5)")
    parser.add_argument("--out", default="results/direct_measure.jsonl",
                        help="Output file for raw results")
    parser.add_argument("--levels", default="C1,C2,C3,C4,C5",
                        help="Comma-separated levels to run (default: all)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    levels = [l.strip().upper() for l in args.levels.split(",")]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Known delegation numbers for comparison (from measured sessions + probe runs)
    deleg_ref = {
        "C1": {"prompt_tok": 1027, "sub_tok": 22921, "verify_tok": 403},
        "C2": {"prompt_tok": 1027, "sub_tok": 23634, "verify_tok": 403},
        "C3": {"prompt_tok": 1027, "sub_tok": 35837, "verify_tok": 403},
        "C4": {"prompt_tok": 1027, "sub_tok": 28345, "verify_tok": 403},
        "C5": {"prompt_tok": 1027, "sub_tok": 48756, "verify_tok": 403},
    }

    all_results = []
    summary = {}  # level -> aggregated

    for level in levels:
        if level not in TASKS:
            print(f"Unknown level {level}, skipping")
            continue
        print(f"\n── {level} ({args.runs} runs) ──")
        level_results = []
        for run in range(1, args.runs + 1):
            print(f"  run {run}/{args.runs}... ", end="", flush=True)
            try:
                r = run_task(client, args.model, TASKS[level])
                r.update({"level": level, "run": run, "model": args.model})
                all_results.append(r)
                level_results.append(r)
                with open(out_path, "a") as f:
                    f.write(json.dumps(r) + "\n")
                print(
                    f"out={r['output_tokens']} in={r['input_tokens']} "
                    f"tools={r['tool_calls']} turns={r['turns']} ({r['elapsed_s']}s)"
                )
            except Exception as e:
                print(f"ERROR: {e}")

        if not level_results:
            continue

        avg = lambda k: sum(x[k] for x in level_results) / len(level_results)
        summary[level] = {
            "n": len(level_results),
            "avg_input": avg("input_tokens"),
            "avg_output": avg("output_tokens"),
            "avg_tool_calls": avg("tool_calls"),
            "avg_turns": avg("turns"),
        }

    # ── print comparison table ────────────────────────────────────────────────
    print("\n\n" + "═" * 90)
    print(f"  Direct Claude vs Delegation — token breakdown per task  (model: {args.model})")
    print("═" * 90)
    print(
        f"  {'Lvl':<5}  {'Direct: input':>14}  {'Direct: output':>15}  {'Tool calls':>11}  "
        f"{'Deleg: prompt':>14}  {'Deleg: job':>11}  {'Deleg: verify':>14}"
    )
    print(
        f"  {'':5}  {'(all turns)':>14}  {'(all turns)':>15}  {'':>11}  "
        f"{'(orch out)':>14}  {'(off-quota)':>11}  {'(orch out)':>14}"
    )
    print("─" * 90)
    for level in levels:
        if level not in summary:
            continue
        s = summary[level]
        d = deleg_ref.get(level, {})
        print(
            f"  {level:<5}  {s['avg_input']:>14,.0f}  {s['avg_output']:>15,.0f}  "
            f"{s['avg_tool_calls']:>11.1f}  "
            f"{d.get('prompt_tok', 0):>14,}  {d.get('sub_tok', 0):>11,}  "
            f"{d.get('verify_tok', 0):>14,}"
        )
    print("═" * 90)
    print(f"\n  Raw results written to: {out_path}")


if __name__ == "__main__":
    main()
