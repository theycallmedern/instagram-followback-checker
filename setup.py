from setuptools import setup


setup(
    name="instagram-followback-checker",
    version="0.1.0",
    description="CLI tool for analyzing followback relationships from official Instagram JSON exports",
    py_modules=["instagram_followback_checker", "instagram_nonfollowers"],
    entry_points={
        "console_scripts": [
            "ig-followback=instagram_followback_checker:main",
        ]
    },
)
