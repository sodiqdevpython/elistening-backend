#!/usr/bin/env python
"""Django boshqaruv skripti."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django topilmadi. Virtual muhit yoqilganmi? "
            "`pip install -r requirements.txt` ni bajaring."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
