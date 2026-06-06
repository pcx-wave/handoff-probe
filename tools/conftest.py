"""Make the flat handoff_* modules importable during tests without any
hardcoded path. The probe modules live alongside this file; running the
probe directly already puts this dir on sys.path[0], and this conftest does
the same for pytest collection."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
