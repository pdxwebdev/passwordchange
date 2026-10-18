"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.

Run: python -m plugins.passwordchange
"""

from plugins.passwordchange.celery_app import create_celery


def main():
    app = create_celery()
    app.worker_main(["worker", "--loglevel=INFO", "-Q", "celery"])


if __name__ == "__main__":
    main()
