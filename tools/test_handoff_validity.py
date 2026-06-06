"""Tests for the validity guards. These enforce the methodology invariants so the
asymmetric-invocation and harness-blindness bugs cannot silently come back."""
import handoff_validity as V


# ---- G1: invocation symmetry --------------------------------------------------
def test_symmetry_passes_on_current_probe():
    import os
    src = open(os.path.join(V.HERE, 'handoff_probe.py')).read()
    assert V.check_invocation_symmetry(src) == [], \
        "current probe must delegate both CLIs through their wrappers"


def test_symmetry_catches_bare_opencode():
    bad = "if cli == 'opencode':\n    subprocess.run(['opencode', 'run', '--format', 'json'])\n"
    viol = V.check_invocation_symmetry(bad)
    assert any('opencode invoked directly' in v for v in viol)


def test_symmetry_catches_missing_wrapper_reference():
    bad = "if cli == 'vibe':\n    pass\n"  # vibe branch but no VIBE_DELEGATE
    viol = V.check_invocation_symmetry(bad)
    assert any('VIBE_DELEGATE' in v for v in viol)


# ---- G2: zero-score classification -------------------------------------------
def test_classify_ok_when_scored():
    assert V.classify_zero({'functional_score': 0.67, 'level': 2}) == 'OK'


def test_classify_timeout():
    assert V.classify_zero({'functional_score': 0, 'level': 3, 'exit_code': 124}) == 'TIMEOUT'


def test_classify_harness_blind_c3_signature_bug():
    # The exact bug we found: a valid validate fn on disk, but scored 0.
    entry = {'functional_score': 0, 'level': 2, 'exit_code': 0,
             'output': '', 'workdir_snippet':
             '# FILE: app.py\ndef validate_user_data(username, age, email):\n    return []'}
    assert V.classify_zero(entry) == 'HARNESS_BLIND_SUSPECT'


def test_classify_harness_blind_c4_async():
    entry = {'functional_score': 0, 'level': 3, 'exit_code': 0,
             'output': 'async def get_users():\n    await db()', 'workdir_snippet': ''}
    assert V.classify_zero(entry) == 'HARNESS_BLIND_SUSPECT'


def test_classify_seed_only_c1_artifact():
    # The C1/C2 artifact: workdir holds only the untouched Flask seed.
    seed = ("# FILE: app.py\nfrom flask import Flask\napp = Flask(__name__)\n"
            "def get_users():\n    return []\ndef get_user(u):\n    return {}\n")
    entry = {'functional_score': 0, 'level': 0, 'exit_code': 0,
             'output': seed, 'workdir_snippet': seed}
    assert V.classify_zero(entry) == 'SEED_ONLY'


def test_classify_no_output():
    assert V.classify_zero({'functional_score': 0, 'level': 2, 'exit_code': 0,
                            'output': '', 'workdir_snippet': ''}) == 'NO_OUTPUT'
