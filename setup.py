from setuptools import setup


setup(
    name="instagram-followback-checker",
    version="0.3.1",
    description="Local-first desktop app, web UI, and CLI for checking Instagram followback relationships",
    author="Misha Belyakov",
    extras_require={
        "live": ["playwright>=1.55,<2"],
    },
    py_modules=[
        "instagram_followback_checker",
        "instagram_followback_live",
        "instagram_followback_web",
        "instagram_nonfollowers",
    ],
    entry_points={
        "console_scripts": [
            "ig-followback=instagram_followback_checker:main",
            "ig-followback-live=instagram_followback_live:main",
            "ig-followback-ui=instagram_followback_web:main",
        ]
    },
)
