class DiracSignal:
    name = 'dirac'
    expected_format = 'python_function_only'

    def get_prompts(self):
        return [
            'Write a Python function that returns the sum of two integers.\nOutput ONLY the function, no explanation.'
        ]


class StepSignal:
    name = 'step'
    expected_format = 'sql_only'

    def get_prompts(self):
        return [
            '[PROJECT CONTEXT]\n'
            'database has 3 tables (users, orders, products),\n'
            'REST+JSON API, JWT auth, AWS EC2 deployment\n'
            'meeting notes from 2024-01-15 Q3 roadmap\n'
            'Alice says coffee machine broken\n'
            'budget review postponed\n'
            'team lunch Friday\n'
            'standup 9am daily\n'
            'office plants need watering\n'
            'parking validation at reception\n'
            '\n'
            '[TASK]\n'
            'Write a SQL query to count orders per user.\n'
            'Output: SQL only, no explanation.'
        ]


class RampSignal:
    name = 'ramp'
    expected_format = 'python_function_only'

    def get_prompts(self):
        base_function = (
            'def process_user_data(username, age, email):\n'
            '    result = {}\n'
            '    result[\'name\'] = username\n'
            '    result[\'age\'] = age\n'
            '    result[\'email\'] = email\n'
            '    if age > 0:\n'
            '        result[\'valid\'] = True\n'
            '    return result'
        )

        task_prefix = 'Add input validation to the function below. Return ONLY the modified function.\n\n'

        file_stub = (
            'import json\n'
            'from typing import Optional\n'
            '\n'
            'class UserService:\n'
            '    def create_user(self, data):\n'
            '        pass\n'
            '\n'
            '    def delete_user(self, user_id):\n'
            '        pass'
        )

        git_diff_block = (
            '\n'
            '--- a/services/user_service.py\n'
            '+++ b/services/user_service.py\n'
            '@@ -1,3 +1,4 @@\n'
            ' +import re\n'
            ' import json\n'
            ' from typing import Optional\n'
            '@@ -8,7 +9,7 @@\n'
            ' class UserService:\n'
            '     def create_user(self, data: dict) -> dict:\n'
            '-        pass\n'
            '+        pass  # TODO: implement\n'
            ' \n'
            '     def delete_user(self, user_id: int) -> bool:\n'
            '-        pass\n'
            '+        pass  # TODO: implement'
        )

        project_description = (
            '\n'
            'This is a Flask-based SaaS API for user management. '
            'It uses SQLAlchemy as the ORM, JWT for authentication, '
            'and is deployed on AWS ECS with Docker containers. '
            'The service handles user CRUD, order processing, and profile management.'
        )

        simulated_chat = (
            '\n'
            'Developer1: We need to add input validation to process_user_data\n'
            'Developer2: Sure, what should we validate?\n'
            'Developer1: username should be at least 3 chars, age between 0-120, email must have @\n'
            'Developer2: What about None values?\n'
            'Developer1: Strip whitespace from strings, reject None for all fields\n'
            'Developer2: Got it, I will add checks with clear error messages'
        )

        l1 = task_prefix + base_function
        l2 = l1 + '\n\n' + file_stub
        l3 = l2 + git_diff_block
        l4 = l3 + project_description
        l5 = l4 + simulated_chat

        return [l1, l2, l3, l4, l5]


class SweepSignal:
    name = 'sweep'
    expected_format = 'python_code'

    def get_prompts(self):
        context_header = (
            '[CONTEXT]\n'
            'Stack: Python 3.10, Flask 3.x, SQLAlchemy 2.x, REST API with JWT auth.\n'
            'Project: user management SaaS API. Files: app.py (routes), models.py (User, Order), utils.py (helpers).\n'
            '\n'
            '[TASK]\n'
        )

        c1 = context_header + 'Print hello world'

        c2 = context_header + 'Write a function to reverse a string. Return only the function.'

        c3 = (
            context_header +
            'Write a REST endpoint POST /users/validate that accepts JSON with username, age, email, '
            'validates them, and returns JSON with is_valid bool and errors list.'
        )

        sync_module = (
            'import requests\n'
            'from flask import Flask, request, jsonify\n'
            'from models import db, User\n'
            '\n'
            'app = Flask(__name__)\n'
            '\n'
            '@app.route("/api/data", methods=["GET"])\n'
            'def get_data():\n'
            '    data = db.query(User).all()\n'
            '    response = requests.get("https://api.example.com/external")\n'
            '    return jsonify({"data": data, "external": response.json()})\n'
            '\n'
            '@app.route("/api/process", methods=["POST"])\n'
            'def process():\n'
            '    payload = request.json\n'
            '    result = db.query(User).filter_by(id=payload["id"]).first()\n'
            '    return jsonify({"result": result})'
        )

        c4 = context_header + 'Refactor the following sync module to use async/await throughout. Output the complete refactored code in a single code block.\n\n' + sync_module

        flask_api = (
            'from flask import Flask, request, jsonify\n'
            'from models import db, User, Order\n'
            '\n'
            'app = Flask(__name__)\n'
            '\n'
            '@app.route("/users", methods=["GET"])\n'
            'def get_users():\n'
            '    users = User.query.all()\n'
            '    return jsonify([u.to_dict() for u in users])\n'
            '\n'
            '@app.route("/users/<int:user_id>", methods=["GET"])\n'
            'def get_user(user_id):\n'
            '    user = User.query.get_or_404(user_id)\n'
            '    return jsonify(user.to_dict())\n'
            '\n'
            '@app.route("/orders/<int:order_id>", methods=["GET"])\n'
            'def get_order(order_id):\n'
            '    order = Order.query.get_or_404(order_id)\n'
            '    return jsonify(order.to_dict())\n'
            '\n'
            '@app.route("/orders", methods=["POST"])\n'
            'def create_order():\n'
            '    data = request.json\n'
            '    order = Order(**data)\n'
            '    db.session.add(order)\n'
            '    db.session.commit()\n'
            '    return jsonify(order.to_dict()), 201'
        )

        c5 = context_header + 'Add an in-memory caching layer (TTL=60s) to the following API for GET endpoints. Output the complete modified code in a single code block.\n\n' + flask_api

        return [c1, c2, c3, c4, c5]


class BatchSignal:
    """
    Signal 5 — BATCH (N tâches indépendantes en une invocation)

    Mesure une dimension distincte de SWEEP et RAMP :
    - SWEEP  = une tâche, complexité croissante
    - RAMP   = une tâche, contexte croissant
    - BATCH  = N tâches indépendantes, invocation unique
      → Isolation des sorties (mix-up entre tâches ?)
      → Taux de complétion par tâche dans le batch
      → Token efficiency vs N appels séquentiels

    3 niveaux :  B1 (2 tâches), B2 (4 tâches), B3 (6 tâches)
    """

    name = 'batch'
    expected_format = 'labeled_blocks'
    BATCH_SIZES = [2, 4, 6]  # indexed by level 0/1/2

    def get_prompts(self) -> list:
        b1 = (
            'Do both tasks below. Return two clearly separated code blocks '
            'labeled TASK_1 and TASK_2.\n'
            'TASK_1: Write a function that returns the sum of two integers. Code only.\n'
            'TASK_2: Write a function that reverses a string. Code only.'
        )
        b2 = (
            'Do all 4 tasks. Return 4 code blocks labeled TASK_1 through TASK_4.\n'
            'TASK_1: Add a docstring to: def add(a, b): return a + b\n'
            'TASK_2: Add type hints to: def reverse(s): return s[::-1]\n'
            'TASK_3: Write a function that checks if a string is a palindrome. Code only.\n'
            'TASK_4: Write a one-liner that flattens a list of lists. Code only.'
        )
        b3 = (
            'Do all 6 tasks. Return 6 code blocks labeled TASK_1 through TASK_6.\n'
            'TASK_1: Add a docstring to: def add(a, b): return a + b\n'
            'TASK_2: Add type hints to: def reverse(s): return s[::-1]\n'
            'TASK_3: Write a palindrome checker. Code only.\n'
            'TASK_4: Write a one-liner that flattens a list of lists. Code only.\n'
            'TASK_5: Write a decorator that logs function call duration. Code only.\n'
            'TASK_6: Write a function that counts word frequency in a string. '
            'Return dict. Code only.'
        )
        return [b1, b2, b3]


class ContractSweepSignal:
    """Same tasks as SweepSignal C1-C5 but C3/C4/C5 carry explicit interface contracts.

    Measures whether specifying exact function signatures and output contracts
    reduces signal loss vs the implicit 'do X' instruction style.
    Functional tests are identical to SweepSignal so scores are directly comparable.
    """
    name = 'contract'
    expected_format = 'python_code'

    def get_prompts(self):
        context_header = (
            '[CONTEXT]\n'
            'Stack: Python 3.10, Flask 3.x, SQLAlchemy 2.x, REST API with JWT auth.\n'
            'Project: user management SaaS API. Files: app.py (routes), models.py (User, Order), utils.py (helpers).\n'
            '\n'
            '[TASK]\n'
        )

        c1 = context_header + 'Print hello world'

        c2 = context_header + 'Write a function to reverse a string. Return only the function.'

        c3 = (
            context_header +
            'Write two things:\n'
            '1. A pure function `validate_user_data(data: dict) -> tuple[bool, list[str]]` '
            'that validates: email (regex ^[^@]+@[^@]+\\.[^@]+$), username (non-empty), age (positive int). '
            'Returns (is_valid, errors_list).\n'
            '2. Flask route POST /users/validate that calls it and returns '
            '{"is_valid": bool, "errors": list}.'
        )

        sync_module = (
            'import requests\n'
            'from flask import Flask, request, jsonify\n'
            'from models import db, User\n'
            '\n'
            'app = Flask(__name__)\n'
            '\n'
            '@app.route("/api/data", methods=["GET"])\n'
            'def get_data():\n'
            '    data = db.query(User).all()\n'
            '    response = requests.get("https://api.example.com/external")\n'
            '    return jsonify({"data": data, "external": response.json()})\n'
            '\n'
            '@app.route("/api/process", methods=["POST"])\n'
            'def process():\n'
            '    payload = request.json\n'
            '    result = db.query(User).filter_by(id=payload["id"]).first()\n'
            '    return jsonify({"result": result})'
        )

        c4 = (
            context_header +
            'Refactor the following sync module to async/await. Requirements:\n'
            '- get_data() and process() must be async def\n'
            '- Each must be independently awaitable: `await get_data()` must run without error\n'
            '- Replace blocking I/O (requests.get, db.query) with `await asyncio.sleep(0)` as placeholder\n'
            'Output the complete refactored code in a single code block.\n\n'
            + sync_module
        )

        flask_api = (
            'from flask import Flask, request, jsonify\n'
            'from models import db, User, Order\n'
            '\n'
            'app = Flask(__name__)\n'
            '\n'
            '@app.route("/users", methods=["GET"])\n'
            'def get_users():\n'
            '    users = User.query.all()\n'
            '    return jsonify([u.to_dict() for u in users])\n'
            '\n'
            '@app.route("/users/<int:user_id>", methods=["GET"])\n'
            'def get_user(user_id):\n'
            '    user = User.query.get_or_404(user_id)\n'
            '    return jsonify(user.to_dict())\n'
            '\n'
            '@app.route("/orders/<int:order_id>", methods=["GET"])\n'
            'def get_order(order_id):\n'
            '    order = Order.query.get_or_404(order_id)\n'
            '    return jsonify(order.to_dict())\n'
            '\n'
            '@app.route("/orders", methods=["POST"])\n'
            'def create_order():\n'
            '    data = request.json\n'
            '    order = Order(**data)\n'
            '    db.session.add(order)\n'
            '    db.session.commit()\n'
            '    return jsonify(order.to_dict()), 201'
        )

        c5 = (
            context_header +
            'Add in-memory caching (TTL=60s) to GET endpoints. '
            'Implement class SimpleCache with:\n'
            '- __init__(self, ttl: int = 60)\n'
            '- get(self, key: str) -> Any  # None if missing or expired\n'
            '- set(self, key: str, value: Any) -> None\n'
            'Use it to cache responses for GET /users, GET /users/<id>, GET /orders/<id>.\n'
            'Output the complete modified code in a single code block.\n\n'
            + flask_api
        )

        return [c1, c2, c3, c4, c5]


SIGNALS = [DiracSignal(), StepSignal(), RampSignal(), SweepSignal(), ContractSweepSignal(), BatchSignal()]


def get_signal(name: str):
    return next(s for s in SIGNALS if s.name == name)
